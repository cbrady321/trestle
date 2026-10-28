#!/usr/bin/env python3
"""Network-refusing fetch logger (MC-B-06, L.RB-4.1).

Stands for every unprompted download a toolchain might attempt (WR-ENV-16, D-19). A stub that
would fetch calls this by absolute path instead; it records the attempt and refuses it. It never
imports a networking module and never opens a socket, so no connection is ever made: the log is
the only evidence a fetch was tried, and a run whose fetch log is empty tried none.

  fetch_logger.py --fetch <kind> [--target <name>] [--url <url>]

`kind` is `distribution` (the JVM wrapper's first-run download) or `toolchain` (JDK
auto-provisioning). Exit 7 always ("refused"). The log path is `STUB_FETCH_LOG` (one JSON line per
attempt `{"fetch", "target", "url", "pid", "refused": true}`); with no log configured the attempt
is still refused and reported on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REFUSED_EXIT = 7
KINDS = ("distribution", "toolchain")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="fetch_logger")
    parser.add_argument("--fetch", required=True, choices=KINDS)
    parser.add_argument("--target", default="")
    parser.add_argument("--url", default="")
    args = parser.parse_args(argv)
    record = {
        "fetch": args.fetch,
        "target": args.target,
        "url": args.url,
        "pid": os.getpid(),
        "refused": True,
    }
    path = os.environ.get("STUB_FETCH_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    print(
        f"fetch_logger: refused {args.fetch} fetch of {args.target or args.url!r}", file=sys.stderr
    )
    return REFUSED_EXIT


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
