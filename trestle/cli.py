"""Trestle CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trestle.ops.serve import run_ops_server
from trestle.server.doctor import run_doctor, run_recover
from trestle.server.home import HomeRefused
from trestle.server.init_cmd import run_init
from trestle.server.main import create_kernel, default_home, run_server
from trestle.server.plugin_paths import PluginDirMissing


def _add_home_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", help="Override TRESTLE_HOME")


def _add_plugin_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--plugin-dir",
        action="append",
        dest="plugin_dirs",
        metavar="PATH",
        help=(
            "Plugin directory to watch, relative to the working directory "
            "(repeatable; replaces config/env when set)"
        ),
    )


def _cli_plugin_dirs(raw: list[str] | None) -> list[Path] | None:
    if not raw:
        return None
    return [Path(path) for path in raw]


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
    serve.add_argument(
        "--port",
        type=int,
        default=18732,
        help=(
            "HTTP bind port on 127.0.0.1 (streamable-http only, default 18732; 0 picks a free port"
            " and prints `trestle: listening 127.0.0.1:<port>` on stderr)"
        ),
    )
    _add_home_arg(serve)
    _add_plugin_dir_arg(serve)

    ops = sub.add_parser("ops", help="Operator HTTP surface")
    ops_sub = ops.add_subparsers(dest="ops_command", required=True)
    ops_serve = ops_sub.add_parser("serve", help="Start operator HTTP API (sessions/telemetry)")
    ops_serve.add_argument(
        "--port",
        type=int,
        default=18733,
        help="Bind port on 127.0.0.1 (default 18733)",
    )
    _add_home_arg(ops_serve)
    _add_plugin_dir_arg(ops_serve)

    init = sub.add_parser("init", help="Create TRESTLE_HOME and seed default plugins")
    _add_home_arg(init)
    init.add_argument(
        "--no-seed",
        action="store_true",
        help="Create plugins directory without seeding echo.py",
    )
    init.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade a v0.3.0 home to format 2 (stop every v0.3.0 server first)",
    )

    doctor = sub.add_parser("doctor", help="Report service health and configuration")
    _add_home_arg(doctor)
    _add_plugin_dir_arg(doctor)
    doctor.add_argument(
        "--gc",
        action="store_true",
        help="Run a retention sweep and include gc stats",
    )
    doctor.add_argument(
        "--rebuild-keys",
        action="store_true",
        help="Repair home/keys/ from the runs' created rows (expired keys stay expired)",
    )

    recover = sub.add_parser(
        "recover", help="Reap runs whose server died, then run a retention sweep"
    )
    _add_home_arg(recover)

    pin = sub.add_parser("pin", help="Pin a run or artifact for retention")
    pin.add_argument("target", help="Run id or artifact id to pin")
    _add_home_arg(pin)

    unpin = sub.add_parser("unpin", help="Remove a retention pin")
    unpin.add_argument("target", help="Run id or artifact id to unpin")
    _add_home_arg(unpin)

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except HomeRefused as exc:
        # v0.3.1: every entry point checks home/format and the local-mount rule first
        print(f"trestle: {exc}", file=sys.stderr)
        return 2
    except PluginDirMissing as exc:
        print(f"trestle: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "serve":
        home = Path(args.home) if getattr(args, "home", None) else None
        return run_server(
            transport=args.transport,
            port=args.port,
            home=home,
            cli_plugin_dirs=_cli_plugin_dirs(getattr(args, "plugin_dirs", None)),
        )
    if args.command == "ops" and args.ops_command == "serve":
        home = Path(args.home) if args.home else None
        return run_ops_server(
            port=args.port,
            home=home,
            cli_plugin_dirs=_cli_plugin_dirs(getattr(args, "plugin_dirs", None)),
        )
    if args.command == "init":
        trestle_home = Path(args.home) if args.home else default_home()
        return run_init(home=trestle_home, seed_echo=not args.no_seed, upgrade=args.upgrade)
    if args.command == "doctor":
        return run_doctor(
            home=args.home,
            run_gc_pass=getattr(args, "gc", False),
            cli_plugin_dirs=_cli_plugin_dirs(getattr(args, "plugin_dirs", None)),
            rebuild_keys=getattr(args, "rebuild_keys", False),
        )
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
