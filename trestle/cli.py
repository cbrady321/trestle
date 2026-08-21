"""Trestle CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trestle.server.doctor import run_doctor, run_recover
from trestle.server.main import create_kernel, default_home, run_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle", description="Trestle execution ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="Start the MCP stdio server")
    doctor = sub.add_parser("doctor", help="Report service health and configuration")
    doctor.add_argument("--home", help="Override TRESTLE_HOME")
    doctor.add_argument(
        "--gc",
        action="store_true",
        help="Run a retention sweep and include gc stats",
    )
    recover = sub.add_parser("recover", help="Run crash recovery sweep")
    recover.add_argument("--home", help="Override TRESTLE_HOME")
    pin = sub.add_parser("pin", help="Pin a run or artifact for retention")
    pin.add_argument("target", help="Run id or artifact id to pin")
    pin.add_argument("--home", help="Override TRESTLE_HOME")
    unpin = sub.add_parser("unpin", help="Remove a retention pin")
    unpin.add_argument("target", help="Run id or artifact id to unpin")
    unpin.add_argument("--home", help="Override TRESTLE_HOME")

    args = parser.parse_args(argv)
    if args.command == "serve":
        return run_server()
    if args.command == "doctor":
        return run_doctor(home=args.home, run_gc_pass=getattr(args, "gc", False))
    if args.command == "recover":
        return run_recover(home=args.home)
    if args.command == "pin":
        return _pin_cli(target=args.target, home=args.home)
    if args.command == "unpin":
        return _unpin_cli(target=args.target, home=args.home)
    return 1


def _pin_cli(*, target: str, home: str | None) -> int:
    trestle_home = Path(home) if home else default_home()
    kernel = create_kernel(home=trestle_home, skip_recovery=True)
    outcome = kernel.control.pin(target)
    print(outcome.message)
    return 0 if outcome.code.endswith("_accepted") else 1


def _unpin_cli(*, target: str, home: str | None) -> int:
    trestle_home = Path(home) if home else default_home()
    kernel = create_kernel(home=trestle_home, skip_recovery=True)
    outcome = kernel.control.unpin(target)
    print(outcome.message)
    return 0 if outcome.code.endswith("_accepted") else 1


if __name__ == "__main__":
    sys.exit(main())
