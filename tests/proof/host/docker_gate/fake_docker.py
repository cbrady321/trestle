#!/usr/bin/env python3
"""A fake `docker` CLI for the host-docker gate's CI tests (L.NW-2.1; MC-13, WR-PROOF-5).

It is invoked by ABSOLUTE PATH only (never resolved through PATH, never named `docker` on PATH), so
no gate test can reach a real engine. It records every call (argv, the docker endpoint variables,
cwd, stdin state) as one JSON line in `$FAKE_DOCKER_LOG`, answers from a JSON engine-state file
`$FAKE_DOCKER_STATE`, and mutates that file for the few write commands the gate's housekeeping
issues. A `pull` is recorded and refused: a test asserting "no pull ever reached the shim" reads the
log. It imports no networking module.

State file (all keys optional)::

    {"reachable": true, "server_version": "29.8.0", "engine_id": "ENG1",
     "endpoint": "unix:///fake/desktop-linux.sock",
     "containers": [{"id": "c1", "names": ["web"], "image": "alpine", "state": "running",
                     "labels": {"k": "v"}}],
     "images": [{"id": "sha256:aa", "repository": "alpine", "tag": "3.20",
                 "repo_digests": ["alpine@sha256:<hex>"]}],
     "volumes": [{"name": "v1", "labels": {}}],
     "networks": [{"id": "n1", "name": "bridge", "labels": {}}]}

`$FAKE_DOCKER_MODE`: unset/`normal`; `stdin-read` (read stdin to EOF and log the bytes, for the
later leaves' stdin-closed assertions); `hang` (block forever after logging, for the bounded-run
tests).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# This directory holds a `select.py` (the run-set plugin) that would shadow the stdlib `select`
# when the shim runs as a script (sys.path[0] is its directory): drop it before importing.
_HERE = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if not p or str(Path(p).resolve()) != _HERE]

import select  # noqa: E402
import signal  # noqa: E402

STATE_ENV = "FAKE_DOCKER_STATE"
LOG_ENV = "FAKE_DOCKER_LOG"
MODE_ENV = "FAKE_DOCKER_MODE"
DEFAULT_ENDPOINT = "unix:///fake/desktop-linux.sock"
DEFAULT_VERSION = "29.8.0"


# -- helpers used by the tests (not by the shim's own run) ----------------------------------------


def write_state(path: Path, **state: object) -> None:
    Path(path).write_text(json.dumps(state, indent=2))


def read_state(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def read_log(path: Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def fake_env(state_path: Path, log_path: Path, **extra: str) -> dict[str, str]:
    """`os.environ` plus the shim's variables (tests pass this as the docker call's env)."""
    env = dict(os.environ)
    env[STATE_ENV] = str(state_path)
    env[LOG_ENV] = str(log_path)
    env.update(extra)
    return env


def install_shim(
    directory: Path, state: Path, log: Path | None = None, mode: str | None = None
) -> str:
    """An absolute-path launcher for this shim, bound to `state` (and `log`, `mode`) by CONTENT,
    not by environment (L.NW-2.3): the adapters run docker with an environment built from empty,
    so the shim's variables cannot reach it any other way. The launcher's shebang is the running
    interpreter, so it starts under the execution port's scrubbed `PATH`. Returns its path."""
    launcher = Path(directory) / "docker"
    bindings = {STATE_ENV: str(state)}
    if log is not None:
        bindings[LOG_ENV] = str(log)
    if mode is not None:
        bindings[MODE_ENV] = mode
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import os, runpy\n"
        f"os.environ.update({bindings!r})\n"
        f"runpy.run_path({str(Path(__file__).resolve())!r}, run_name='__main__')\n"
    )
    launcher.chmod(0o755)
    return str(launcher)


# -- the shim ------------------------------------------------------------------------------------


def _stdin_state() -> tuple[str, bytes]:
    """tty | closed | eof | idle | data, plus the bytes read (never blocks on an open pipe)."""
    try:
        fd = sys.stdin.fileno()
    except (ValueError, OSError):
        return "closed", b""
    try:
        if os.isatty(fd):
            return "tty", b""
        ready, _, _ = select.select([fd], [], [], 0)
        if not ready:
            return "idle", b""
        data = os.read(fd, 65536)
    except OSError:
        return "closed", b""
    return ("data", data) if data else ("eof", b"")


def _load_state() -> dict:
    path = os.environ.get(STATE_ENV)
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text())


def _save_state(state: dict) -> None:
    path = os.environ.get(STATE_ENV)
    if path:
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        os.replace(tmp, path)


def _labels_text(labels: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def _emit_lines(rows: list[dict]) -> None:
    for row in rows:
        print(json.dumps(row))


def _opt(args: list[str], name: str) -> str | None:
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _name_filter(args: list[str]) -> re.Pattern[str] | None:
    for i, a in enumerate(args):
        val = None
        if a == "--filter" and i + 1 < len(args):
            val = args[i + 1]
        elif a.startswith("--filter="):
            val = a.split("=", 1)[1]
        if val and val.startswith("name="):
            return re.compile(val[len("name=") :])
    return None


def _reachable(state: dict) -> bool:
    return bool(state.get("reachable", True))


def _unreachable() -> int:
    sys.stderr.write("Cannot connect to the Docker daemon (fake_docker: engine stopped)\n")
    return 1


def _cmd_ps(args: list[str], state: dict) -> int:
    rows = state.get("containers", [])
    pat = _name_filter(args)
    quiet = "-q" in args or "-aq" in args or "--quiet" in args
    out = []
    for c in rows:
        names = c.get("names") or [c.get("name", "")]
        if pat is not None and not any(pat.search("/" + n) for n in names):
            continue
        if quiet:
            out.append(c["id"])
            continue
        out.append(
            {
                "ID": c["id"],
                "Names": ",".join(names),
                "Image": c.get("image", ""),
                "State": c.get("state", "running"),
                "Labels": _labels_text(c.get("labels", {})),
            }
        )
    if quiet:
        for line in out:
            print(line)
    else:
        _emit_lines(out)
    return 0


def _cmd_image(args: list[str], state: dict) -> int:
    sub = args[0] if args else ""
    images = state.get("images", [])
    if sub == "ls":
        _emit_lines(
            [
                {
                    "ID": i["id"],
                    "Repository": i.get("repository", "<none>"),
                    "Tag": i.get("tag", "<none>"),
                    "Digest": "<none>",
                }
                for i in images
            ]
        )
        return 0
    if sub == "inspect":
        ref = next((a for a in args[1:] if not a.startswith("-")), "")
        for i in images:
            tags = [f"{i.get('repository')}:{i.get('tag')}"]
            if ref in tags or ref in i.get("repo_digests", []) or ref == i["id"]:
                print(
                    json.dumps(
                        [
                            {
                                "Id": i["id"],
                                "RepoTags": tags,
                                "RepoDigests": i.get("repo_digests", []),
                            }
                        ]
                    )
                )
                return 0
        sys.stderr.write(f"Error response from daemon: No such image: {ref}\n")
        return 1
    sys.stderr.write(f"fake_docker: unsupported: image {sub}\n")
    return 2


def _cmd_remove(kind: str, names: list[str], state: dict) -> int:
    key = {"container": "containers", "network": "networks", "volume": "volumes"}[kind]
    rows = state.get(key, [])
    keep, gone = [], []
    for row in rows:
        row_names = row.get("names") or [row.get("name", "")]
        hit = (
            row.get("id") in names or row.get("name") in names or any(n in names for n in row_names)
        )
        (gone if hit else keep).append(row)
    if not gone:
        sys.stderr.write(f"Error: No such {kind}: {' '.join(names)}\n")
        return 1
    state[key] = keep
    _save_state(state)
    for row in gone:
        print(row.get("id") or row.get("name"))
    return 0


def _dispatch(args: list[str], state: dict) -> int:
    if not args:
        sys.stderr.write("fake_docker: no command\n")
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "pull":
        sys.stderr.write("fake_docker: pull refused (WR-PROOF-5)\n")
        return 1
    if cmd == "context" and rest[:1] == ["inspect"]:
        print(state.get("endpoint", DEFAULT_ENDPOINT))
        return 0
    if cmd == "--version":
        print(f"Docker version {state.get('server_version', DEFAULT_VERSION)}, fake")
        return 0
    if not _reachable(state):
        return _unreachable()
    if cmd == "info":
        template = _opt(rest, "--format")
        if template == "{{.ServerVersion}}":  # the adapters' reachability read (L.NW-2.3)
            print(state.get("server_version", DEFAULT_VERSION))
            return 0
        print(
            json.dumps(
                {
                    "ServerVersion": state.get("server_version", DEFAULT_VERSION),
                    "ID": state.get("engine_id", "FAKE-ENGINE-ID"),
                    "Name": "fake",
                }
            )
        )
        return 0
    if cmd == "ps":
        return _cmd_ps(rest, state)
    if cmd == "image":
        return _cmd_image(rest, state)
    if cmd == "volume" and rest[:1] == ["ls"]:
        _emit_lines(
            [
                {"Name": v["name"], "Labels": _labels_text(v.get("labels", {}))}
                for v in state.get("volumes", [])
            ]
        )
        return 0
    if cmd == "network" and rest[:1] == ["ls"]:
        _emit_lines(
            [
                {"ID": n["id"], "Name": n["name"], "Labels": _labels_text(n.get("labels", {}))}
                for n in state.get("networks", [])
            ]
        )
        return 0
    if cmd == "network" and rest[:1] == ["rm"]:
        return _cmd_remove("network", [a for a in rest[1:] if not a.startswith("-")], state)
    if cmd == "volume" and rest[:1] == ["rm"]:
        return _cmd_remove("volume", [a for a in rest[1:] if not a.startswith("-")], state)
    if cmd in ("rm", "stop"):
        names = [a for a in rest if not a.startswith("-")]
        if cmd == "stop":
            hit = False
            for c in state.get("containers", []):
                cn = c.get("names") or [c.get("name", "")]
                if c["id"] in names or any(n in names for n in cn):
                    c["state"] = "exited"
                    hit = True
            if not hit:
                sys.stderr.write(f"Error: No such container: {' '.join(names)}\n")
                return 1
            _save_state(state)
            return 0
        return _cmd_remove("container", names, state)
    sys.stderr.write(f"fake_docker: unsupported: {' '.join(args)}\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = os.environ.get(MODE_ENV, "normal")
    stdin_state, stdin_bytes = _stdin_state()
    if mode == "stdin-read":
        stdin_bytes += sys.stdin.buffer.read()
        stdin_state = "data" if stdin_bytes else "eof"
    endpoint = _opt(argv, "--host")
    args = list(argv)
    if endpoint is not None:
        i = args.index("--host") if "--host" in args else None
        if i is not None:
            del args[i : i + 2]
        else:
            args = [a for a in args if not a.startswith("--host=")]
    entry = {
        "argv": argv,
        "args": args,
        "host": endpoint,
        "env": {
            k: os.environ.get(k)
            for k in (
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "TRESTLE_HOST_GATE",
                "TRESTLE_HOST_LOCK_HELD",
            )
        },
        "cwd": os.getcwd(),
        "stdin": stdin_state,
        "stdin_bytes": stdin_bytes.decode("utf-8", "replace"),
        "pid": os.getpid(),
    }
    log = os.environ.get(LOG_ENV)
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    if mode == "hang":
        while True:
            signal.pause()
    return _dispatch(args, _load_state())


if __name__ == "__main__":
    sys.exit(main())
