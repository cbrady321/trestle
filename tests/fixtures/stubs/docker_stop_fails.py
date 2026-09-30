#!/usr/bin/env python3
"""docker_stop_fails: a release wrapper around a docker CLI (L.RB-12.3, reused by L.RB-12.5).

    docker_stop_fails.py CONFIG [--host ENDPOINT] ARGS...

`CONFIG` is a JSON file: `{"real": [argv prefix of the docker it wraps], "fail": null | "stop" |
"remove", "log": "<path>"}`. Every call is appended to `log` as one JSON line (`args`, `failed`).
A call whose verb is the selected release step (`stop` for "stop", `rm` for "remove") fails with
exit 1 and runs nothing; every other call runs the wrapped docker with the same arguments and
passes its stdout and exit status through.

A test binds the reference ports to a `docker` shell script that execs this file with its CONFIG,
so the `ArgvRelease` descriptors the run records name that script: the host sweep runs it only
when a test operator configuration lists it in `[operator] release_executables` (V-10.1); no
other configuration ever does. The sweep runs a release command with an environment built from
empty, so nothing here reads the environment: CONFIG carries everything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

VERBS = {"stop": "stop", "remove": "rm"}


def _verb(args: list[str]) -> str:
    rest = args[2:] if args[:1] == ["--host"] else args
    return rest[0] if rest else ""


def main(argv: list[str]) -> int:
    config = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    args = argv[1:]
    failing = config.get("fail")
    failed = failing is not None and _verb(args) == VERBS[failing]
    with Path(config["log"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps({"args": args, "failed": failed}) + "\n")
    if failed:
        sys.stderr.write(f"docker_stop_fails: planted {failing} failure\n")
        return 1
    done = subprocess.run([*config["real"], *args], stdin=subprocess.DEVNULL, check=False)
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
