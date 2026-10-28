from __future__ import annotations

import argparse
import sys

from tests.proof.host import proc_gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.host.proc_gate")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--select", action="append", default=None)
    args = parser.parse_args(argv)
    if args.command == "run":
        return proc_gate.run(select=args.select)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
