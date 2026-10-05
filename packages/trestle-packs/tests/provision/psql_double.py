#!/usr/bin/env python3
"""A `docker` CLI double whose only verb is `exec` of the provisioning adapter's `psql` script
(L.RB-6.1; MC-13, WR-PROOF-5). Invoked by ABSOLUTE PATH through a launcher (`rig.install_double`),
never through `PATH`, so no test can reach a real engine.

It answers the way `docker exec <container> sh -c 'exec psql ...' sh ROLE DB SQL` answers, with the
SQL run by `sqlite3` (the adapter's statements are the portable subset of Postgres SQL):

- the container must exist in the state file (`docker`: "No such container", exit 1);
- the script must connect the way the official image demands a password for: over TCP to the
  container's own address (`hostname -i`), never `127.0.0.1`, `localhost` or a socket (exit 97
  names what it saw), and never `pg_isready`;
- `PGPASSWORD` must be the state's password and the role and database must be the state's
  (`psql: error: ... FATAL:  password authentication failed`, exit 2);
- a statement on a table that does not exist prints `ERROR:  relation "<t>" does not exist`
  (psql's own text, exit 3 under ON_ERROR_STOP); a `SELECT` prints its rows `|`-separated (`-tA`).

State file (JSON): `container`, `password`, `role`, `database`, `store` (the sqlite file), and the
optional failure scripts `fail_submit` (exit 3 before any change), `apply_then_fail` (run the
statement, then exit 1: a connection lost after the commit) and `hang` (a statement that changes
something blocks forever). Every call is logged as one JSON line in the log file: argv, whether
`PGPASSWORD` was set, the stdin state.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

STATE_ENV = "PSQL_DOUBLE_STATE"
LOG_ENV = "PSQL_DOUBLE_LOG"


def _log(argv: list[str], env: dict[str, str]) -> None:
    path = os.environ.get(LOG_ENV)
    if not path:
        return
    try:
        stdin_is_tty = os.isatty(sys.stdin.fileno())
    except (ValueError, OSError):
        stdin_is_tty = False
    row = {"argv": argv, "pgpassword_set": "PGPASSWORD" in env, "stdin_tty": stdin_is_tty}
    with open(path, "a", encoding="utf-8") as sink:
        sink.write(json.dumps(row) + "\n")


def _parse_exec(args: list[str]) -> tuple[dict[str, str], str, list[str]] | None:
    """`exec [-e K=V]... NAME CMD...` -> (env, name, cmd)."""
    rest, env = list(args), {}
    while rest and rest[0].startswith("-"):
        if rest[0] in ("-e", "--env") and len(rest) > 1:
            key, _, value = rest[1].partition("=")
            env[key] = value
            rest = rest[2:]
        else:
            rest = rest[1:]
    if not rest:
        return None
    return env, rest[0], rest[1:]


def main(argv: list[str]) -> int:
    state = json.loads(Path(os.environ[STATE_ENV]).read_text(encoding="utf-8"))
    args = list(argv)
    if args[:1] == ["--host"]:
        args = args[2:]
    if not args or args[0] != "exec":
        sys.stderr.write("psql_double: only `exec` is simulated\n")
        return 2
    parsed = _parse_exec(args[1:])
    if parsed is None:
        sys.stderr.write("psql_double: exec needs a container\n")
        return 2
    env, name, command = parsed
    _log(argv, env)
    if name != state["container"]:
        sys.stderr.write(f"Error response from daemon: No such container: {name}\n")
        return 1
    if command[:2] != ["sh", "-c"] or len(command) != 7 or command[3] != "sh":
        sys.stderr.write(
            f"psql_double: not the adapter's `sh -c <script> sh ROLE DB SQL`: {command}\n"
        )
        return 2
    script, role, database, sql = command[2], command[4], command[5], command[6]
    for banned in ("127.0.0.1", "localhost", "/var/run", ".s.PGSQL", "pg_isready", "::1"):
        if banned in script:
            sys.stderr.write(f"psql_double: the script connects over a trusted path ({banned})\n")
            return 97
    if "hostname -i" not in script or "psql" not in script:
        sys.stderr.write("psql_double: the script does not run psql over the container address\n")
        return 97
    if env.get("PGPASSWORD") != state["password"] or (role, database) != (
        state["role"],
        state["database"],
    ):
        sys.stderr.write(
            f'psql: error: connection to server at "172.17.0.2", port 5432 failed: FATAL:  '
            f'password authentication failed for user "{role}"\n'
        )
        return 2
    is_read = sql.lstrip().upper().startswith("SELECT")
    if state.get("hang") and not is_read:
        while True:
            time.sleep(1)
    if state.get("fail_submit") and not is_read:
        sys.stderr.write("psql: error: connection to server was lost\n")
        return 3
    connection = sqlite3.connect(state["store"])
    try:
        if is_read:
            rows = connection.execute(sql).fetchall()
            sys.stdout.write("".join("|".join(str(v) for v in row) + "\n" for row in rows))
        else:
            connection.executescript(sql)
            connection.commit()
    except sqlite3.OperationalError as exc:
        missing = re.match(r"no such table: (\w+)", str(exc))
        if missing:
            sys.stderr.write(f'ERROR:  relation "{missing.group(1)}" does not exist\n')
        else:
            sys.stderr.write(f"ERROR:  {exc}\n")
        return 3
    finally:
        connection.close()
    if state.get("apply_then_fail") and not is_read:
        sys.stderr.write("psql: error: connection to server was lost\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
