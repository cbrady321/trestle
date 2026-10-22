"""Credential identities and grants: `$TRESTLE_HOME/credentials.toml` (decision record change 3).

The file is managed by `trestle credentials` and read once at server start, like `config.toml`,
so nothing an agent sends can add, change or widen an identity. It holds no value: values are in
the encrypted store (`credential_store`). One table per identity:

    [store]
    key_provider = "keychain"            # or "file"; fixed by the first `add`

    [credentials.gh]
    kind = "encrypted_file"
    env = "GH_TOKEN"                      # the variable name `ctx.credential_env` returns
    grants = [
        { plugin = "release", source_sha256 = "<hex>", package_digests = { pkg = "<hex>" } },
    ]

A grant covers a plugin only at the `source_sha256` **and** `package_digests` its publication
recorded: a republish that changes the entry file or any declared package leaves the grant stale
until `trestle credentials grant` pins it again. A bare-string grant (`grants = ["release"]`) is
read as an unpinned grant: it covers nothing until `grant` pins it.

Admission checks a plugin's declared identities against these tables (`refusal`), then reads the
values from the store (`resolve`); both happen before any run id exists.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from trestle.common import codes
from trestle.common import credentials as cred
from trestle.common.fsutil import atomic_write
from trestle.common.types import RequestOutcome
from trestle.server.credential_store import (
    KEY_PROVIDERS,
    EncryptedStore,
    StoreError,
    make_key_provider,
)

CREDENTIALS_FILE = "credentials.toml"

GRANT_CURRENT = "current"
GRANT_STALE = "stale"
GRANT_MISSING = "plugin missing"
GRANT_UNPINNED = "unpinned"


@dataclass(frozen=True)
class Grant:
    plugin: str
    # None: a bare-string grant, not pinned to any digest yet
    source_sha256: str | None = None
    package_digests: Mapping[str, str] = field(default_factory=dict)

    def covers(self, plugin: str, source_sha256: str, package_digests: Mapping[str, str]) -> bool:
        return (
            self.plugin == plugin
            and self.source_sha256 is not None
            and self.source_sha256 == source_sha256
            and dict(self.package_digests) == dict(package_digests)
        )


@dataclass(frozen=True)
class Identity:
    name: str
    kind: str
    env: str
    grants: tuple[Grant, ...] = ()

    def covers(self, plugin: str, source_sha256: str, package_digests: Mapping[str, str]) -> bool:
        return any(g.covers(plugin, source_sha256, package_digests) for g in self.grants)

    def grant_for(self, plugin: str) -> Grant | None:
        return next((g for g in self.grants if g.plugin == plugin), None)


@dataclass(frozen=True)
class CredentialsConfig:
    identities: Mapping[str, Identity] = field(default_factory=dict)
    # None: never chosen (no `add` yet); the platform default applies
    key_provider: str | None = None

    def store(self, home: Path) -> EncryptedStore:
        return EncryptedStore(home, make_key_provider(home, self.key_provider))


def credentials_path(home: Path) -> Path:
    return home / CREDENTIALS_FILE


# --- reading -----------------------------------------------------------------------------------


def _where(name: str | None = None) -> str:
    return f"credentials.toml [credentials.{name}]" if name else "credentials.toml"


def _grant(raw: object, name: str) -> Grant:
    if isinstance(raw, str):
        return Grant(plugin=raw)
    if not isinstance(raw, dict):
        raise ValueError(f"{_where(name)} grants entries must be tables or plugin names")
    plugin = raw.get("plugin")
    sha = raw.get("source_sha256")
    pkgs = raw.get("package_digests", {})
    if not isinstance(plugin, str) or not plugin:
        raise ValueError(f'{_where(name)} a grant needs plugin = "<name>"')
    if sha is not None and not isinstance(sha, str):
        raise ValueError(f"{_where(name)} grant for {plugin}: source_sha256 must be a string")
    if not isinstance(pkgs, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in pkgs.items()
    ):
        raise ValueError(
            f"{_where(name)} grant for {plugin}: package_digests must be a table of strings"
        )
    unknown = set(raw) - {"plugin", "source_sha256", "package_digests"}
    if unknown:
        raise ValueError(f"{_where(name)} grant for {plugin}: unknown keys {sorted(unknown)}")
    return Grant(plugin=plugin, source_sha256=sha, package_digests=dict(pkgs))


def _identity(name: str, raw: object) -> Identity:
    if not cred.valid_name(name):
        raise ValueError(
            f"{_where()}: {name!r} is not an identity name (lower case, digits, '-' and '_')"
        )
    if not isinstance(raw, dict):
        raise ValueError(f"{_where(name)} must be a table")
    kind = raw.get("kind")
    if kind in cred.KINDS_LATER:
        raise ValueError(
            f"{_where(name)} kind {kind!r} is not implemented yet; this build supports "
            f'kind = "{cred.KIND_ENCRYPTED_FILE}" only'
        )
    if kind != cred.KIND_ENCRYPTED_FILE:
        raise ValueError(
            f'{_where(name)} unknown kind {kind!r}; expected "{cred.KIND_ENCRYPTED_FILE}"'
        )
    env = raw.get("env")
    if not cred.valid_env(env):
        raise ValueError(f'{_where(name)} env must be a variable name like "GH_TOKEN"')
    assert isinstance(env, str)
    grants_raw = raw.get("grants", [])
    if not isinstance(grants_raw, list):
        raise ValueError(f"{_where(name)} grants must be a list")
    unknown = set(raw) - {"kind", "env", "grants"}
    if unknown:
        raise ValueError(f"{_where(name)} unknown keys {sorted(unknown)}")
    grants = tuple(_grant(g, name) for g in grants_raw)
    plugins = [g.plugin for g in grants]
    if len(set(plugins)) != len(plugins):
        raise ValueError(f"{_where(name)} grants a plugin twice")
    return Identity(name=name, kind=kind, env=env, grants=grants)


def parse_credentials(text: str) -> CredentialsConfig:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"credentials.toml does not parse: {exc}") from None
    unknown = set(data) - {"store", "credentials"}
    if unknown:
        raise ValueError(f"credentials.toml: unknown tables {sorted(unknown)}")
    store = data.get("store", {})
    if not isinstance(store, dict):
        raise ValueError("credentials.toml [store] must be a table")
    provider = store.get("key_provider")
    if provider is not None and provider not in KEY_PROVIDERS:
        raise ValueError(f"credentials.toml [store] key_provider must be one of {KEY_PROVIDERS}")
    tables = data.get("credentials", {})
    if not isinstance(tables, dict):
        raise ValueError("credentials.toml [credentials] must hold one table per identity")
    identities = {name: _identity(name, raw) for name, raw in tables.items()}
    return CredentialsConfig(identities=identities, key_provider=provider)


def load_credentials(home: Path) -> CredentialsConfig:
    """The identity tables, or none when the file is absent. A malformed file raises ValueError,
    as a malformed config.toml does: the server does not start on it."""
    path = credentials_path(home)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CredentialsConfig()
    return parse_credentials(text)


# --- writing (the CLI) -------------------------------------------------------------------------

_HEADER = """\
# Trestle credential identities. Managed by `trestle credentials`: add, remove, grant, revoke.
# No value is ever in this file; values are in the encrypted store under credentials/.
# Read once at server start: after a change, `trestle service restart`.
"""


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _grant_line(grant: Grant) -> str:
    parts = [f"plugin = {_q(grant.plugin)}"]
    if grant.source_sha256 is not None:
        parts.append(f"source_sha256 = {_q(grant.source_sha256)}")
        pkgs = ", ".join(
            f"{_q(k)} = {_q(grant.package_digests[k])}" for k in sorted(grant.package_digests)
        )
        parts.append(f"package_digests = {{ {pkgs} }}" if pkgs else "package_digests = {}")
    return "    { " + ", ".join(parts) + " },"


def render_credentials(config: CredentialsConfig) -> str:
    lines = [_HEADER]
    if config.key_provider is not None:
        lines += ["[store]", f"key_provider = {_q(config.key_provider)}", ""]
    for name in sorted(config.identities):
        ident = config.identities[name]
        lines += [
            f"[credentials.{name}]",
            f"kind = {_q(ident.kind)}",
            f"env = {_q(ident.env)}",
        ]
        if ident.grants:
            lines.append("grants = [")
            lines += [_grant_line(g) for g in sorted(ident.grants, key=lambda g: g.plugin)]
            lines.append("]")
        else:
            lines.append("grants = []")
        lines.append("")
    return "\n".join(lines)


def save_credentials(home: Path, config: CredentialsConfig) -> None:
    text = render_credentials(config)
    parse_credentials(text)  # every start reads it back: never write what would not load
    atomic_write(credentials_path(home), text.encode("utf-8"), mode=0o600)


def with_identity(config: CredentialsConfig, ident: Identity) -> CredentialsConfig:
    identities = dict(config.identities)
    identities[ident.name] = ident
    return replace(config, identities=identities)


def without_identity(config: CredentialsConfig, name: str) -> CredentialsConfig:
    identities = {k: v for k, v in config.identities.items() if k != name}
    return replace(config, identities=identities)


def pinned(ident: Identity, grant: Grant) -> Identity:
    """`ident` with `grant` replacing any grant of the same plugin."""
    kept = tuple(g for g in ident.grants if g.plugin != grant.plugin)
    return replace(ident, grants=(*kept, grant))


def unpinned(ident: Identity, plugin: str) -> Identity:
    return replace(ident, grants=tuple(g for g in ident.grants if g.plugin != plugin))


# --- pin status --------------------------------------------------------------------------------


def grant_status(grant: Grant, current: tuple[str, Mapping[str, str]] | None) -> str:
    """A grant's status against the plugin's current publication (`(source_sha256,
    package_digests)`, None when no such plugin is published)."""
    if current is None:
        return GRANT_MISSING
    if grant.source_sha256 is None:
        return GRANT_UNPINNED
    return GRANT_CURRENT if grant.covers(grant.plugin, current[0], current[1]) else GRANT_STALE


# --- admission ---------------------------------------------------------------------------------


OPERATOR_RUNS = "the operator runs: "


def service_log(home: Path, message: str) -> None:
    """One operator-side line in `$TRESTLE_HOME/service.log` (never an agent-facing answer)."""
    try:
        with (home / "service.log").open("a", encoding="utf-8") as fh:
            fh.write(f"credentials: {message}\n")
    except OSError:
        pass


def _outcome(code: str, command: str, detail: str) -> RequestOutcome:
    """An admission refusal an agent reads: the fixed operator instruction first, so the errtext
    cut can never drop it, then what is wrong in terms of the identity alone. It never names
    where the store or its key is (that is in the operator's `trestle doctor` and the log)."""
    message = f"{OPERATOR_RUNS}{command}; {detail}"
    return RequestOutcome(code=code, message=message[:200], retryable=False, origin="admission")


def refusal(
    config: CredentialsConfig,
    plugin: str,
    declared: Iterable[str],
    source_sha256: str,
    package_digests: Mapping[str, str],
) -> RequestOutcome | None:
    """`admission.credential_unknown` for a declared identity no table defines, else
    `admission.credential_not_granted` for one whose grants do not cover this plugin at its
    current source and package digests; None when every declared identity is granted."""
    for name in sorted(declared):
        ident = config.identities.get(name)
        if ident is None:
            return _outcome(
                codes.ADMISSION_CREDENTIAL_UNKNOWN,
                f"trestle credentials add {name} --env VAR",
                f"credential {name!r} is not configured on this service",
            )
        if not ident.covers(plugin, source_sha256, package_digests):
            return _outcome(
                codes.ADMISSION_CREDENTIAL_NOT_GRANTED,
                f"trestle credentials grant {name} {plugin}",
                f"credential {name!r} is not granted to this plugin as now published",
            )
    return None


def resolve(
    config: CredentialsConfig,
    home: Path,
    declared: Iterable[str],
    *,
    log: Callable[[str], None] | None = None,
) -> cred.Delivered | RequestOutcome:
    """The declared identities as `(env name, value)` by name, read from the store now (a rotated
    value applies to this run); or `admission.credential_unavailable` naming the identity when the
    store cannot give it. Why the store failed, and where it is, goes to `log` (the operator's
    service log), never into the refusal."""
    names = sorted(declared)
    if not names:
        return {}
    try:
        stored = config.store(home).read_all()
    except StoreError as exc:
        if log is not None:
            log(f"credential store unreadable for {', '.join(names)}: {exc}")
        return _outcome(
            codes.ADMISSION_CREDENTIAL_UNAVAILABLE,
            "trestle doctor",
            f"credential {names[0]!r}: the credential store could not give its value",
        )
    out: cred.Delivered = {}
    for name in names:
        value = stored.get(name)
        if not value:
            return _outcome(
                codes.ADMISSION_CREDENTIAL_UNAVAILABLE,
                f"trestle credentials add {name} --replace",
                f"credential {name!r} has no stored value",
            )
        out[name] = (config.identities[name].env, value)
    return out
