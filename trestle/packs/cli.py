"""`trestle pack ...`: the operator's pack commands. No command restarts the service: the lock
changes, and the service rescans (the design's "no restart" rule).

    trestle pack install <name | path | git-url@ref[#subdir]> [--owner company|user]
                         [--replace PACK] [--no-wait]
    trestle pack list [--json]
    trestle pack remove NAME [--no-wait]
    trestle pack upgrade [NAME ...] [--no-wait]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from trestle.packs import PackError
from trestle.packs.install import INSTALL_OWNERS, Installer


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("pack", help="Install, list, remove and upgrade plugin packs")
    psub = parser.add_subparsers(dest="pack_command", required=True)

    def common(cmd: argparse.ArgumentParser, *, wait: bool = True) -> None:
        cmd.add_argument("--home", help="Override TRESTLE_HOME")
        if wait:
            cmd.add_argument(
                "--no-wait",
                action="store_true",
                help="Refuse while a live run uses a plugin being replaced, instead of waiting",
            )

    install = psub.add_parser(
        "install",
        help="Install a bundled pack by name, a folder in place, or a git repo at a tag/commit",
    )
    install.add_argument("spec", help="<name | path | git-url@tag-or-commit[#subdir]>")
    install.add_argument(
        "--owner",
        choices=INSTALL_OWNERS,
        help="Owner label for a git or path install (default user)",
    )
    install.add_argument(
        "--replace", metavar="PACK", help="Swap this installed pack out (e.g. a working copy)"
    )
    common(install)

    listing = psub.add_parser(
        "list", help="Installed packs, overrides, and available bundled packs"
    )
    listing.add_argument("--json", action="store_true", help="Print JSON")
    common(listing, wait=False)

    remove = psub.add_parser("remove", help="Uninstall a pack")
    remove.add_argument("name")
    common(remove)

    upgrade = psub.add_parser("upgrade", help="Install packs again from where they came from")
    upgrade.add_argument("names", nargs="*")
    common(upgrade)


def run(args: argparse.Namespace, *, home: Path, out: TextIO | None = None) -> int:
    from trestle.server.home import check_home

    stream = out if out is not None else sys.stdout
    try:
        check_home(home)
        installer = Installer(home=home, out=stream, no_wait=getattr(args, "no_wait", False))
        command = args.pack_command
        if command == "install":
            installer.install(args.spec, owner=args.owner, replace_pack=args.replace)
            return _report_grants(home, stream)
        if command == "remove":
            installer.remove(args.name)
            return 0
        if command == "upgrade":
            installer.upgrade(list(args.names))
            return _report_grants(home, stream)
        if command == "list":
            return _list(home, stream, as_json=args.json)
    except PackError as exc:
        print(f"trestle pack: {exc}", file=sys.stderr)
        return 1
    return 2


def _report_grants(home: Path, out: TextIO) -> int:
    """Grants pin digests (Decision 2), so a changed library drops its plugins' grants: name each
    one that no longer matches, and exit non-zero so an unattended upgrade notices."""
    from trestle.packs.overview import stale_grants

    stale = stale_grants(home)
    for name, plugin in stale:
        print(
            f"grant dropped: {name} -> {plugin} no longer matches the published plugin; "
            f"review it and run: trestle credentials grant {name} {plugin}",
            file=out,
        )
    return 1 if stale else 0


def _list(home: Path, out: TextIO, *, as_json: bool) -> int:
    from trestle.packs.overview import overview

    view = overview(home)
    if as_json:
        print(json.dumps(view, indent=2), file=out)
        return 0
    installed = view["installed"]
    overrides = view["overrides"]
    available = view["available"]
    assert isinstance(installed, list) and isinstance(overrides, list)
    assert isinstance(available, list)
    if not installed:
        print("no packs installed", file=out)
    for row in installed:
        print(f"{row['label']}  {row['kind']}  {row['pack_dir']}", file=out)
        for plugin in row["plugins"]:
            print(f"  {plugin}", file=out)
    for row in overrides:
        flag = "  [restricted: allowlisted name served by an override]" if row["flagged"] else ""
        print(f"override: {row['plugin']} from {row['by']} over {row['over']}{flag}", file=out)
    if available:
        print(f"available bundled packs: {', '.join(available)}", file=out)
    return 0
