"""`python -m tests.proof.host.docker_gate preflight [--strict] | run [--select ID …] [-- <pytest
args>] | pin-images | inventory` (CSC-5, MC-B-02)."""

from __future__ import annotations

import argparse
import json
import sys

from tests.proof.host import host_lock
from tests.proof.host.docker_gate import inventory
from tests.proof.host.docker_gate import preflight as preflight_mod
from tests.proof.host.docker_gate import run as run_mod


def _inventory() -> int:
    docker_bin = preflight_mod.resolve_docker()
    if not docker_bin:
        print("docker_gate inventory: docker CLI not resolved")
        return 3
    endpoint = preflight_mod.read_endpoint(docker_bin)
    if endpoint is None:
        print("docker_gate inventory: endpoint of context desktop-linux unreadable")
        return 3
    snap = inventory.snapshot(docker_bin, endpoint)
    print(json.dumps(snap, indent=2))
    return 0 if snap["engine"]["reachable"] else 3


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pytest_args: list[str] = []
    if "--" in argv:  # everything after `--` is pytest's (`run` only)
        split = argv.index("--")
        argv, pytest_args = argv[:split], argv[split + 1 :]
    parser = argparse.ArgumentParser(prog="python -m tests.proof.host.docker_gate")
    sub = parser.add_subparsers(dest="command", required=True)
    preflight_parser = sub.add_parser("preflight")
    preflight_parser.add_argument("--strict", action="store_true")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--select", action="append", default=None)
    sub.add_parser("pin-images")
    sub.add_parser("inventory")
    args = parser.parse_args(argv)
    if pytest_args and args.command != "run":
        parser.error("`--` pytest args belong to `run`")

    try:
        if args.command == "preflight" and args.strict:
            rc = preflight_mod.strict_preflight()
            print(f"docker_gate preflight --strict: exit {rc}")
            return rc
        if args.command == "preflight":
            record = preflight_mod.preflight()
            print(f"docker_gate preflight: status={record['status']}")
            return 0
        if args.command == "run":
            return run_mod.run(select_ids=args.select, pytest_args=pytest_args)
        if args.command == "pin-images":
            return run_mod.pin_images()
        if args.command == "inventory":
            return _inventory()
    except host_lock.HostRunTimedOut as exc:  # killed at HOST_RUN_MAX; lock freed; no record
        print(f"docker_gate {args.command}: {exc}")
        return 1
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
