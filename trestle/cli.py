"""Trestle CLI entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from trestle.ops.serve import run_ops_server
from trestle.server.doctor import run_doctor, run_recover
from trestle.server.home import HomeRefused
from trestle.server.init_cmd import run_init
from trestle.server.main import DEFAULT_PORT, create_kernel, default_home, run_server
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
    parser.add_argument("--version", action="store_true", help="Print the build and exit")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser(
        "serve", help="Start the MCP server (streamable HTTP on 127.0.0.1, bearer token)"
    )
    serve.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=(
            f"HTTP bind port on 127.0.0.1 (default {DEFAULT_PORT}; 0 picks a free port"
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
        help="Create the plugins directory without seeding echo.py and exec.py",
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

    cancel = sub.add_parser(
        "cancel", help="Cancel a live run, whatever session sent it (the operator's cancel)"
    )
    cancel.add_argument("run_id", help="The root run's id")
    _add_home_arg(cancel)

    service = sub.add_parser("service", help="Run Trestle as this user's service")
    service_sub = service.add_subparsers(dest="service_command", required=True)
    for name, text in (
        ("install", "Write and load the LaunchAgent / systemd --user unit (idempotent)"),
        ("uninstall", "Stop (draining), unload and remove the unit"),
        ("status", "Loaded, running, pid, endpoint, home, installed and running build"),
        ("start", "Load the unit if needed and start the service"),
        ("stop", "SIGTERM drain, then unload; stays down until start"),
        ("restart", "Stop (draining), then start"),
        ("upgrade", "Install the new build, migrate the home, restart"),
    ):
        cmd = service_sub.add_parser(name, help=text)
        if name == "install":
            _add_home_arg(cmd)
            cmd.add_argument(
                "--port",
                type=int,
                default=DEFAULT_PORT,
                help=f"Loopback port the service serves (default {DEFAULT_PORT})",
            )
        if name in {"install", "upgrade"}:
            cmd.add_argument(
                "--drain",
                action="store_true",
                help="Wait for live runs to end instead of refusing while one is live",
            )
        if name == "upgrade":
            cmd.add_argument(
                "--source",
                metavar="SPEC",
                help="What pip installs (default: where this trestle was installed from)",
            )

    from trestle.server.credentials_cli import add_parser as add_credentials_parser

    add_credentials_parser(sub)

    from trestle.packs.cli import add_parser as add_pack_parser

    add_pack_parser(sub)

    args = parser.parse_args(argv)
    if args.version:
        from trestle.common.build import build_version

        print(f"trestle {build_version()}")
        return 0
    if args.command is None:
        parser.error("a command is required")
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
    if args.command == "service":
        return _service_cli(args)
    if args.command == "credentials":
        from trestle.server import credentials_cli

        home = Path(args.home).expanduser() if args.home else default_home()
        return credentials_cli.run(args, home=home)
    if args.command == "pack":
        from trestle.packs import cli as pack_cli

        home = Path(args.home).expanduser() if args.home else default_home()
        return pack_cli.run(args, home=home)
    if args.command == "init":
        trestle_home = Path(args.home) if args.home else default_home()
        return run_init(home=trestle_home, seed=not args.no_seed, upgrade=args.upgrade)
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
    if args.command == "cancel":
        return _cancel_cli(run_id=args.run_id, home=args.home)
    return 1


def _service_cli(args: argparse.Namespace) -> int:
    from trestle.server.service import (
        Service,
        ServiceError,
        default_install_command,
        default_platform,
        trestle_binary,
    )

    try:
        home = Path(args.home).expanduser() if getattr(args, "home", None) else default_home()
        service = Service(
            platform=default_platform(),
            user_home=Path.home(),
            trestle_home=Path(os.path.abspath(home)),
            port=getattr(args, "port", DEFAULT_PORT),
        )
        command = args.service_command
        if command == "status":
            status = service.status()
            for line in status.lines():
                print(line)
            return 0 if status.running else 3
        if command == "install":
            return service.install(drain=args.drain)
        if command == "upgrade":
            if args.source:
                service.install_command = default_install_command(trestle_binary(), args.source)
            return service.upgrade(drain=args.drain)
        return {
            "uninstall": service.uninstall,
            "start": service.start,
            "stop": service.stop,
            "restart": service.restart,
        }[command]()
    except ServiceError as exc:
        print(f"trestle service: {exc}", file=sys.stderr)
        return exc.code


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


def _cancel_cli(*, run_id: str, home: str | None) -> int:
    """Change 2a: the operator's unscoped cancel. It writes the run's cancel flag; the owning
    server's supervisor (or scheduler pass) acts on it, as for a cancel any other server received.
    Exit 0 accepted, 1 refused (an unknown run, one that has ended, a child handle), 2 a refused
    home or a usage error."""
    from trestle.common import codes
    from trestle.server.home import is_locked, owner_lock_path
    from trestle.server.recovery import find_run_dir

    trestle_home = Path(home) if home else default_home()
    kernel = create_kernel(home=trestle_home, skip_recovery=True)
    outcome = kernel.control.cancel(run_id, None)
    if outcome.code != codes.CANCEL_ACCEPTED:
        print(f"trestle cancel: {outcome.message}", file=sys.stderr)
        return 1
    print(outcome.message)
    run_dir = find_run_dir(trestle_home, run_id)
    if run_dir is not None and not is_locked(owner_lock_path(run_dir)):
        print(
            "its server is not running: the run ends interrupted at the next reaper pass "
            "(trestle recover runs one now)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
