"""Credential identities: the names, shapes and hand-over shared by the server, wrapper and child.

An identity is a name an operator configures (`$TRESTLE_HOME/credentials.toml`) and a plugin
declares (`@trestle(credentials=[...])`); its value never crosses an agent-facing surface. At
admission the server resolves each granted identity to `(env name, value)` and keeps it in memory
on the run's `WorkOrder` (`Delivered`). Every value is in the run's scrub set (`scrub_set`), so a
value the plugin writes anywhere reaches disk and every answer as `<redacted>` (MC-CORE-13).

**The hand-over is a pipe, never the environment or argv.** A process's initial environment is
readable by any process of the same user (`ps -E` on macOS, `/proc/<pid>/environ` on Linux), and
removing a variable from `os.environ` does not remove it from the kernel's copy. So the conductor
gives the wrapper, and the wrapper gives the child, an inherited pipe: the environment carries only
the pipe's file descriptor number (`SECRETS_FD_ENV`), the sender writes the values to it and
closes it, and the receiver reads it to EOF and closes it (`receive`) before any plugin code is
imported. Nothing a plugin starts inherits the descriptor.

This module imports nothing of the server, the plugin or the workflow.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

# An identity name: lower case, digits, `-` and `_`, as `ci-reader` or `gh`.
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
# The variable name `credential_env` returns the value under.
ENV_PATTERN = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
# The one store backend change 3 implements; the record's others are change 4.
KIND_ENCRYPTED_FILE = "encrypted_file"
KINDS_LATER = frozenset(
    {"keychain", "onepassword", "aws_assume_role", "github_app", "cli_passthrough"}
)

# Change 2c: plugin names that never carry a credential. `exec` runs any argv the agent picks, so
# a credential on it would hand the value to any agent; a declaration on a plugin of this name is
# refused at publication, and `trestle credentials grant`/`add --grant` refuse it too. An operator
# who copies the file under another name has written their own plugin.
NEVER_GRANTED: frozenset[str] = frozenset({"exec"})

# The run-scoped channel directory, beside `work/` and `evidence/` in the run directory: it holds
# the `credential_file` files (0700 directory, 0600 files) and is removed when the run ends.
CHANNEL_DIR = "channel"
# The one environment variable of the hand-over: the number of an inherited pipe's read end. It is
# never a value.
SECRETS_FD_ENV = "TRESTLE_RUN_SECRETS_FD"
_READ_CHUNK = 64 * 1024

# identity name -> (env name, value): what a run is given, in memory only
Delivered = dict[str, tuple[str, str]]


def valid_name(name: object) -> bool:
    return isinstance(name, str) and NAME_PATTERN.fullmatch(name) is not None


def valid_env(name: object) -> bool:
    return isinstance(name, str) and ENV_PATTERN.fullmatch(name) is not None


def scrub_set(delivered: Mapping[str, tuple[str, str]]) -> frozenset[str]:
    """Every non-empty value a run was given: what each of its write paths scrubs."""
    return frozenset(value for _env, value in delivered.values() if value)


# --- the hand-over -----------------------------------------------------------------------------


def encode(delivered: Mapping[str, tuple[str, str]]) -> bytes:
    body = {name: {"env": env, "value": value} for name, (env, value) in delivered.items()}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decode(raw: bytes) -> Delivered:
    try:
        loaded = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        return {}
    out: Delivered = {}
    if not isinstance(loaded, dict):
        return out
    for name, item in loaded.items():
        if not isinstance(item, dict):
            continue
        env, value = item.get("env"), item.get("value")
        if valid_name(name) and valid_env(env) and isinstance(value, str):
            assert isinstance(env, str)
            out[name] = (env, value)
    return out


@dataclass
class Handover:
    """One hand-over to one process about to be spawned. `prepare` sets the environment it will be
    given and returns the descriptor to pass (`subprocess.Popen(pass_fds=...)`); after the spawn
    `started` closes this side's copy of the read end and writes the values on a thread (a pipe
    holds only so much before a reader drains it), and `failed` closes both ends instead."""

    delivered: Mapping[str, tuple[str, str]]
    _read: int | None = field(default=None, init=False)
    _write: int | None = field(default=None, init=False)

    def prepare(self, env: MutableMapping[str, str]) -> tuple[int, ...]:
        env.pop(SECRETS_FD_ENV, None)  # only this run's own pipe, never an inherited number
        if not self.delivered:
            return ()
        self._read, self._write = os.pipe()
        env[SECRETS_FD_ENV] = str(self._read)
        return (self._read,)

    def started(self) -> None:
        if self._read is None or self._write is None:
            return
        os.close(self._read)
        self._read = None
        payload, fd = encode(self.delivered), self._write
        self._write = None

        def write() -> None:
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(payload)
            except OSError:  # the receiver died before reading: nothing to deliver to
                pass

        threading.Thread(target=write, name="trestle-handover", daemon=True).start()

    def failed(self) -> None:
        for fd in (self._read, self._write):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._read = self._write = None


def receive(environ: MutableMapping[str, str]) -> Delivered:
    """The values handed to this process: the variable removed, the pipe read to EOF and closed.
    Empty when nothing was handed over."""
    raw_fd = environ.pop(SECRETS_FD_ENV, None)
    if not raw_fd:
        return {}
    try:
        fd = int(raw_fd)
    except ValueError:
        return {}
    chunks: list[bytes] = []
    try:
        while chunk := os.read(fd, _READ_CHUNK):
            chunks.append(chunk)
    except OSError:
        return {}
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return decode(b"".join(chunks))


# --- the channel directory ---------------------------------------------------------------------


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
