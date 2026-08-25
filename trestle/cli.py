"""Trestle CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trestle.ops.serve import run_ops_server
from trestle.server.doctor import run_doctor, run_recover
from trestle.server.main import create_kernel, default_home, run_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle", description="Trestle execution ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the MCP server (stdio or streamable HTTP)")
    serve.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http"],
        help="Agent MCP transport (default stdio)",
    )
    serve.add_argument("--host", default="127.0.0.1", help="HTTP bind host (streamable-http only)")
    serve.add_argument(
        "--port",
        type=int,
        default=18732,
        help="HTTP bind port (streamable-http only, default 18732)",
    )
    ops = sub.add_parser("ops", help="Operator HTTP surface")
    ops_sub = ops.add_subparsers(dest="ops_command", required=True)
    ops_serve = ops_sub.add_parser("serve", help="Start operator HTTP API (sessions/telemetry)")
    ops_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    ops_serve.add_argument("--port", type=int, default=18733, help="Bind port (default 18733)")
    ops_serve.add_argument("--home", help="Override TRESTLE_HOME")
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
        return run_server(
            transport=args.transport,
            host=args.host,
            port=args.port,
        )
    if args.command == "ops" and args.ops_command == "serve":
        home = Path(args.home) if args.home else None
        return run_ops_server(host=args.host, port=args.port, home=home)
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
