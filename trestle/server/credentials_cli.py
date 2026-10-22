"""`trestle credentials ...`: the operator's commands for identities, values and grants (change 3).

    trestle credentials add NAME --env VAR [--grant PLUGIN ...] [--replace]
    trestle credentials remove NAME
    trestle credentials list
    trestle credentials grant NAME PLUGIN
    trestle credentials revoke NAME PLUGIN

A value is never an argument: `add` reads it with getpass from a terminal, or from stdin when it
is piped (`op read ... | trestle credentials add gh --env GH_TOKEN`), so it is in no shell history
and no process listing. Nothing here prints a value.

The tables (`credentials.toml`) are read once at server start, so a command that changes a table
or a grant says so and names `trestle service restart` (or does it, with `--restart`). A value
rotated in place (`add --replace` with nothing else changed) applies to the next run, because the
server reads values at each admission.
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TextIO

from trestle.common import credentials as cred
from trestle.server.credential_store import (
    KEY_PROVIDERS,
    StoreError,
    default_key_provider,
    ensure_store_dir,
)
from trestle.server.credentials import (
    GRANT_CURRENT,
    CredentialsConfig,
    Grant,
    Identity,
    grant_status,
    load_credentials,
    pinned,
    save_credentials,
    unpinned,
    with_identity,
    without_identity,
)

RESTART_NOTE = (
    "applies at the next start of the service: run `trestle service restart` (or pass --restart)"
)
ROTATED_NOTE = "the new value applies to the next run (no restart needed)"


class CredentialsCliError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Published:
    """One plugin as the home publishes it now."""

    source_sha256: str
    package_digests: Mapping[str, str]
    origin: str


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "credentials", help="Manage credential identities, their stored values and grants"
    )
    csub = parser.add_subparsers(dest="credentials_command", required=True)

    def common(cmd: argparse.ArgumentParser, *, restart: bool) -> None:
        cmd.add_argument("--home", help="Override TRESTLE_HOME")
        if restart:
            cmd.add_argument(
                "--restart",
                action="store_true",
                help="Restart the service afterwards (trestle service restart)",
            )

    add = csub.add_parser(
        "add",
        help="Store a value (read from the terminal, or stdin when piped) under NAME",
        description=(
            "Store a credential value under NAME. The value is read with a hidden prompt from a "
            "terminal, or from stdin when piped; it is never a command-line argument."
        ),
    )
    add.add_argument("name", help="Identity name (lower case, digits, '-' and '_')")
    add.add_argument("--env", help="Variable name ctx.credential_env returns the value under")
    add.add_argument(
        "--grant",
        action="append",
        default=[],
        metavar="PLUGIN",
        help="Grant the identity to this plugin as now published (repeatable)",
    )
    add.add_argument(
        "--replace",
        action="store_true",
        help="Rotate the value of an existing identity in place (its grants are kept)",
    )
    add.add_argument(
        "--key-provider",
        choices=KEY_PROVIDERS,
        help=(
            "Where the store's key lives, chosen when the first identity is added "
            f"(default {default_key_provider()} on this platform)"
        ),
    )
    common(add, restart=True)

    remove = csub.add_parser("remove", help="Delete an identity and its stored value")
    remove.add_argument("name")
    common(remove, restart=True)

    listing = csub.add_parser("list", help="Identities, env names and grants (never values)")
    common(listing, restart=False)

    grant = csub.add_parser(
        "grant", help="Grant an identity to a plugin at its current published digests"
    )
    grant.add_argument("name")
    grant.add_argument("plugin")
    common(grant, restart=True)

    revoke = csub.add_parser("revoke", help="Remove a plugin's grant")
    revoke.add_argument("name")
    revoke.add_argument("plugin")
    common(revoke, restart=True)


# --- shared ------------------------------------------------------------------------------------


@contextmanager
def _locked(home: Path) -> Iterator[None]:
    """One credentials command at a time on a home (the tables and the store change together)."""
    directory = ensure_store_dir(home)
    fd = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def published_plugins(home: Path) -> dict[str, Published]:
    """Every plugin the home's watched folders publish now, with its recorded digests and origin
    (one registry refresh: the same publication a server's start would make)."""
    from trestle.server.home import check_home
    from trestle.server.plugin_paths import resolve_plugin_dirs
    from trestle.server.registry import Registry
    from trestle.server.snapshots import load_declared, load_origin

    check_home(home)
    registry = Registry(home=home, plugin_dirs=resolve_plugin_dirs(home, cli_dirs=None))
    registry.refresh()
    out: dict[str, Published] = {}
    for name, snap in registry.snapshots.items():
        out[name] = Published(
            source_sha256=snap.source_sha256,
            package_digests=dict(load_declared(snap).package_digests),
            origin=load_origin(snap),
        )
    return out


def _pin(home: Path, plugin: str, published: dict[str, Published] | None = None) -> Grant:
    from trestle.server.snapshots import ORIGIN_AGENT

    if plugin in cred.NEVER_GRANTED:
        raise CredentialsCliError(
            f"plugin {plugin!r} runs any command an agent picks and is never granted a credential"
        )
    found = (published if published is not None else published_plugins(home)).get(plugin)
    if found is None:
        raise CredentialsCliError(
            f"no plugin {plugin!r} is published on this home (check `trestle doctor`)"
        )
    if found.origin == ORIGIN_AGENT:
        raise CredentialsCliError(
            f"plugin {plugin!r} was published by an agent (publish_plugin) and is never granted "
            "a credential; review it and save it into a watched folder as your own edit first"
        )
    return Grant(
        plugin=plugin,
        source_sha256=found.source_sha256,
        package_digests=dict(found.package_digests),
    )


def read_value(stdin: TextIO, name: str) -> str:
    """The value: a hidden prompt from a terminal, else stdin (one trailing newline dropped)."""
    if stdin.isatty():
        value = getpass.getpass(f"value for {name} (input hidden): ")
    else:
        value = stdin.read()
        if value.endswith("\n"):
            value = value[:-1]
        if value.endswith("\r"):
            value = value[:-1]
    if not value:
        raise CredentialsCliError("no value given: nothing stored")
    if "\x00" in value:
        raise CredentialsCliError("the value holds a NUL byte: nothing stored")
    return value


def _identity_or_fail(config: CredentialsConfig, name: str) -> Identity:
    ident = config.identities.get(name)
    if ident is None:
        raise CredentialsCliError(f"no identity {name!r} (see `trestle credentials list`)")
    return ident


# --- the commands ------------------------------------------------------------------------------


def cmd_add(home: Path, args: argparse.Namespace, stdin: TextIO, out: TextIO) -> bool:
    """Returns whether a table changed (a restart is needed)."""
    name = args.name
    if not cred.valid_name(name):
        raise CredentialsCliError(
            f"{name!r} is not an identity name (lower case letters, digits, '-' and '_')"
        )
    if args.env is not None and not cred.valid_env(args.env):
        raise CredentialsCliError(f"--env {args.env!r} is not a variable name like GH_TOKEN")
    config = load_credentials(home)
    existing = config.identities.get(name)
    if existing is not None and not args.replace:
        raise CredentialsCliError(
            f"identity {name!r} exists; pass --replace to rotate its value (grants are kept)"
        )
    if existing is None and args.replace:
        raise CredentialsCliError(f"no identity {name!r} to replace; add it without --replace")
    if existing is None and args.env is None:
        raise CredentialsCliError("--env VAR is required for a new identity")
    if args.key_provider is not None and config.key_provider not in (None, args.key_provider):
        raise CredentialsCliError(
            f"the store's key provider is already {config.key_provider!r}; it is not changed here"
        )
    # every grant is checked before anything is stored
    published = published_plugins(home) if args.grant else None
    grants = [_pin(home, plugin, published) for plugin in args.grant]
    value = read_value(stdin, name)

    if config.key_provider is None:
        config = replace(config, key_provider=args.key_provider or default_key_provider())
    ident = existing or Identity(name=name, kind=cred.KIND_ENCRYPTED_FILE, env=str(args.env))
    if args.env is not None:
        ident = replace(ident, env=args.env)
    for grant in grants:
        ident = pinned(ident, grant)
    try:
        config.store(home).put(name, value)
    except StoreError as exc:
        raise CredentialsCliError(f"the value was not stored: {exc}") from None
    new_config = with_identity(config, ident)
    changed = new_config != load_credentials(home)
    if changed:
        save_credentials(home, new_config)
    verb = "rotated" if existing is not None else "added"
    print(f"{verb} {name} (env {ident.env}); value stored, not shown", file=out)
    for grant in grants:
        print(f"granted {name} to {grant.plugin}", file=out)
    return changed


def cmd_remove(home: Path, args: argparse.Namespace, out: TextIO) -> bool:
    config = load_credentials(home)
    _identity_or_fail(config, args.name)
    save_credentials(home, without_identity(config, args.name))
    try:
        config.store(home).delete(args.name)
    except StoreError as exc:
        print(f"warning: the stored value could not be removed: {exc}", file=out)
    print(f"removed {args.name} and its stored value", file=out)
    return True


def cmd_list(home: Path, out: TextIO) -> None:
    config = load_credentials(home)
    if not config.identities:
        print("no credential identities configured", file=out)
        return
    published = published_plugins(home)
    print(f"key provider: {config.key_provider or default_key_provider()}", file=out)
    for name in sorted(config.identities):
        ident = config.identities[name]
        print(f"{name}  kind={ident.kind}  env={ident.env}", file=out)
        if not ident.grants:
            print("  grants: none", file=out)
        for grant in sorted(ident.grants, key=lambda g: g.plugin):
            print(f"  grant {grant.plugin}: {_status(grant, published)}", file=out)


def _status(grant: Grant, published: Mapping[str, Published]) -> str:
    found = published.get(grant.plugin)
    status = grant_status(
        grant, (found.source_sha256, found.package_digests) if found is not None else None
    )
    if status == GRANT_CURRENT:
        return f"current (source {str(grant.source_sha256)[:12]})"
    if status == "stale":
        return (
            "stale: the plugin was republished; re-pin with "
            f"`trestle credentials grant <name> {grant.plugin}`"
        )
    if status == "unpinned":
        return "unpinned: run `trestle credentials grant` to pin it"
    return "plugin missing: no such plugin is published"


def cmd_grant(home: Path, args: argparse.Namespace, out: TextIO) -> bool:
    config = load_credentials(home)
    ident = _identity_or_fail(config, args.name)
    grant = _pin(home, args.plugin)
    previous = ident.grant_for(args.plugin)
    if previous == grant:
        print(f"{args.name} is already granted to {args.plugin} as now published", file=out)
        return False
    save_credentials(home, with_identity(config, pinned(ident, grant)))
    verb = "re-pinned" if previous is not None else "granted"
    print(
        f"{verb} {args.name} to {args.plugin} at source {str(grant.source_sha256)[:12]}"
        f" and {len(grant.package_digests)} package digest(s)",
        file=out,
    )
    return True


def cmd_revoke(home: Path, args: argparse.Namespace, out: TextIO) -> bool:
    config = load_credentials(home)
    ident = _identity_or_fail(config, args.name)
    if ident.grant_for(args.plugin) is None:
        raise CredentialsCliError(f"{args.name} is not granted to {args.plugin}")
    save_credentials(home, with_identity(config, unpinned(ident, args.plugin)))
    print(f"revoked {args.name} from {args.plugin}", file=out)
    return True


def _restart(home: Path) -> int:
    from trestle.server.main import DEFAULT_PORT
    from trestle.server.service import Service, ServiceError, default_platform

    try:
        service = Service(
            platform=default_platform(),
            user_home=Path.home(),
            trestle_home=Path(os.path.abspath(home)),
            port=DEFAULT_PORT,
        )
        return service.restart()
    except ServiceError as exc:
        print(f"trestle service: {exc}", file=sys.stderr)
        return exc.code


def run(
    args: argparse.Namespace,
    *,
    home: Path,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    out = out if out is not None else sys.stdout
    command = args.credentials_command
    try:
        if command == "list":
            cmd_list(home, out)
            return 0
        with _locked(home):
            if command == "add":
                changed = cmd_add(home, args, stdin, out)
            elif command == "remove":
                changed = cmd_remove(home, args, out)
            elif command == "grant":
                changed = cmd_grant(home, args, out)
            elif command == "revoke":
                changed = cmd_revoke(home, args, out)
            else:  # pragma: no cover - argparse refuses anything else
                raise CredentialsCliError(f"unknown command {command!r}", code=2)
    except CredentialsCliError as exc:
        print(f"trestle credentials: {exc}", file=sys.stderr)
        return exc.code
    except ValueError as exc:  # a malformed credentials.toml
        print(f"trestle credentials: {exc}", file=sys.stderr)
        return 2
    if changed:
        print(RESTART_NOTE if not args.restart else "restarting the service", file=out)
        if args.restart:
            return _restart(home)
    elif command == "add":
        print(ROTATED_NOTE, file=out)
    if command in ("add", "grant"):
        # change 2c (owner decision 1): the one moment the operator is certain to be present
        from trestle.server.doctor import exec_exposure_warning

        warning = exec_exposure_warning(home)
        if warning is not None:
            print(f"warning: {warning}", file=out)
    return 0
