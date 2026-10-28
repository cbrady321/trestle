"""A docker CLI over `twin.fake_binding`'s engine state file (L.RB-12.3; the twins' sweep engine).

    python state_docker.py STATE [--host ENDPOINT] ps -aq --no-trunc --filter name=^/<name>$
    python state_docker.py STATE [--host ENDPOINT] stop <name>
    python state_docker.py STATE [--host ENDPOINT] rm [-f] <name>

What the host sweep runs for a twin's `ArgvRelease` (through `docker_stop_fails.py`, which the
twin's test operator configuration allowlists): it answers the three release verbs against the
same JSON state the fake engine in the plugin process keeps, as docker does (`ps -a` lists a
stopped container too; `rm` refuses a running one without `-f`). Standard library only: the sweep
runs it with an environment built from empty.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"containers": []}


def _save(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".cli.tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    path, args = Path(argv[0]), argv[1:]
    if args[:1] == ["--host"]:
        args = args[2:]
    state = _load(path)
    containers = state.setdefault("containers", [])
    verb, rest = (args[0], args[1:]) if args else ("", [])
    if verb == "ps":
        pattern = None
        for i, a in enumerate(rest):
            if a == "--filter" and i + 1 < len(rest) and rest[i + 1].startswith("name="):
                pattern = re.compile(rest[i + 1][len("name=") :])
        for c in containers:
            if pattern is None or pattern.search("/" + c["name"]):
                print(hashlib.sha256(c["name"].encode()).hexdigest())
        return 0
    names = [a for a in rest if not a.startswith("-")]
    hit = [c for c in containers if c["name"] in names]
    if not names or len(hit) != len(names):
        return 1
    if verb == "stop":
        for c in hit:
            c["state"] = "exited"
    elif verb == "rm":
        if any(c.get("state") == "running" for c in hit) and not {"-f", "--force"} & set(rest):
            return 1
        state["containers"] = [c for c in containers if c["name"] not in names]
    else:
        return 2
    _save(path, state)
    print("\n".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
