"""Credential identities: the names and shapes shared by the server, the child and the CLI.

An identity is a name an operator configures (`$TRESTLE_HOME/credentials.toml`) and a plugin
declares (`@trestle(credentials=[...])`); its value never crosses an agent-facing surface. At
admission the server resolves each granted identity's value and puts it on the run's `WorkOrder`
secrets, the same in-memory path `secrets=` arguments take (MC-CORE-13), under a key no argument
name can take: `credential:<name>:<ENV>`. The child splits those entries off before it restores
the arguments, so a credential never becomes an argument, and every value is in the run's scrub
set because every value on that path is.

This module imports nothing of the server, the plugin or the workflow.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

# An identity name: lower case, digits, `-` and `_`, as `ci-reader` or `gh`. No dot (a dotted
# name is a path into an argument) and no colon (the key separator below).
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
# The variable name `credential_env` returns the value under.
ENV_PATTERN = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
# The one store backend change 3 implements; the record's others are change 4.
KIND_ENCRYPTED_FILE = "encrypted_file"
KINDS_LATER = frozenset(
    {"keychain", "onepassword", "aws_assume_role", "github_app", "cli_passthrough"}
)

_KEY_PREFIX = "credential:"
# The run-scoped channel directory, beside `work/` and `evidence/` in the run directory: it holds
# the `credential_file` files (0700 directory, 0600 files) and is removed when the run ends.
CHANNEL_DIR = "channel"


def valid_name(name: object) -> bool:
    return isinstance(name, str) and NAME_PATTERN.fullmatch(name) is not None


def valid_env(name: object) -> bool:
    return isinstance(name, str) and ENV_PATTERN.fullmatch(name) is not None


def secret_key(name: str, env: str) -> str:
    """The `WorkOrder` secrets key that carries identity `name`'s value (and its variable name)."""
    return f"{_KEY_PREFIX}{name}:{env}"


def split_secrets(
    values: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, tuple[str, str]]]:
    """The run's secret values split into (argument secrets, delivered credentials). The second
    maps each identity name to `(env name, value)`."""
    args: dict[str, Any] = {}
    delivered: dict[str, tuple[str, str]] = {}
    for key, value in values.items():
        if key.startswith(_KEY_PREFIX):
            name, _, env = key[len(_KEY_PREFIX) :].partition(":")
            if valid_name(name) and valid_env(env) and isinstance(value, str):
                delivered[name] = (env, value)
            continue
        args[key] = value
    return args, delivered


def channel_dir(run_dir: Path) -> Path:
    return run_dir / CHANNEL_DIR


def write_channel_file(channel: Path, name: str, value: str) -> Path:
    """Write `value` as `channel/<name>`: the directory 0700, the file 0600, replaced whole (a
    temporary beside it, then a rename), so a reader never sees half a value."""
    from trestle.common.fsutil import atomic_write

    if not valid_name(name):
        raise ValueError(f"not a credential name: {name!r}")
    channel.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(channel, 0o700)
    path = channel / name
    atomic_write(path, value.encode("utf-8"), mode=0o600)
    return path


def not_declared(name: str, declared: Iterable[str]) -> LookupError:
    """The error `credential_env` and `credential_file` raise for a name the plugin did not
    declare (or that the run was not given)."""
    listed = ", ".join(sorted(declared)) or "none"
    return LookupError(
        f"credential {name!r} is not declared by this plugin (declared: {listed}); "
        f"add it to @trestle(credentials=[...]) and have the operator grant it"
    )
