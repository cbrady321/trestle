from __future__ import annotations

import argparse
import sys

from tests.proof.host import host_lock
from tests.proof.host.docker_gate import preflight as preflight_mod

NOT_BUILT_MODES = {
    "run": "L.NW-2.10",
    "select": "L.NW-2.10",
    "inventory": "L.NW-2.10",
    "pin-images": "L.NW-2.10",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.host.docker_gate")
    sub = parser.add_subparsers(dest="command", required=True)
    preflight_parser = sub.add_parser("preflight")
    preflight_parser.add_argument("--strict", action="store_true")
    for mode in NOT_BUILT_MODES:
        sub.add_parser(mode)
    args = parser.parse_args(argv)

    if args.command == "preflight" and args.strict:
        try:
            rc = preflight_mod.strict_preflight()
        except host_lock.HostRunTimedOut as exc:
            print(f"docker_gate preflight --strict: {exc}")
            return 1
        print(f"docker_gate preflight --strict: exit {rc}")
        return rc

    if args.command == "preflight":
        record = preflight_mod.preflight()
        print(f"docker_gate preflight: status={record['status']}")
        return 0

    builder = NOT_BUILT_MODES[args.command]
    print(f"docker_gate {args.command}: not built; see {builder}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
