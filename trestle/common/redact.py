"""The one write-path scrub for declared secrets (MC-CORE-13; WR-EVID-8).

A plugin declares its secret arguments by name (`@trestle(secrets=[...])`, MC-18). Two things
follow. Admission writes `spec.json` with those arguments redacted (`redact_args`) and keeps the
real values in memory only, on the run's `WorkOrder`; the wrapper and the child receive them
through one environment variable that the child removes before the plugin is called
(`encode_env`, `take_env`). And every write path applies the same scrub to what it is about to
persist (`scrub`, `scrub_json`), so a secret the plugin logs, prints, raises, returns or attaches
never reaches disk as its value.

Two vocabularies, both fixed here:

- a *declared name* is what the decorator lists: an argument name, or a dotted path into a dict
  argument (`"auth.password"`). An exact key that itself contains a dot wins over the path.
- a *secret value* is a string found in the real arguments at a declared name (every string leaf,
  when the value is a list or a dict). `scrub` and `holds_secret` take a set of these strings.
  Only strings are scrubbed: a number or a boolean has no stable text form to look for, and an
  empty string would match everywhere.

This module imports nothing of the plugin, the workflow or the packs.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, overload

from trestle.common.errtext import replace_roots
from trestle.common.fsutil import atomic_write

# What replaces a secret, everywhere: one fixed token (an agent reading a record sees that a value
# was there, never what it was).
REDACTED = "<redacted>"
# What stands in for a file that held a secret and cannot be scrubbed as text.
REFUSED_BINARY = b"[refused: the file held a declared secret]"
# The limit marker a refused binary artifact leaves (`limit_exceeded`, stream "artifacts").
BINARY_LIMIT = "secret_in_binary"
# The one environment variable that carries the real values from the conductor to the child.
SECRETS_ENV = "TRESTLE_RUN_SECRETS"
# A file larger than this is never read whole for a text rewrite; it is streamed for a match and
# treated as binary (a secret in it is refused, not scrubbed).
TEXT_SCRUB_MAX = 64 * 1024 * 1024
_CHUNK = 1024 * 1024


# --- declared names -> values in the arguments -------------------------------------------------


def _resolve(args: Mapping[str, Any], name: str) -> tuple[str, ...] | None:
    """The key path in `args` a declared name points at, or None when the arguments do not carry
    it (an optional secret the caller left at its default)."""
    if name in args:
        return (name,)
    parts = tuple(name.split("."))
    if len(parts) < 2:
        return None
    node: Any = args
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return parts


def _get(args: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = args
    for key in path:
        node = node[key]
    return node


def _set(args: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = args
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy(v) for v in value]
    return value


def redact_args(args: Mapping[str, Any], declared_secrets: Iterable[str]) -> dict[str, Any]:
    """`args` as `spec.json` records them: a copy, each declared secret's value replaced by
    `REDACTED`. A declared name the arguments do not carry changes nothing."""
    out = _copy(dict(args))
    for name in sorted(declared_secrets):
        path = _resolve(out, name)
        if path is not None:
            _set(out, path, REDACTED)
    return out  # type: ignore[no-any-return]


def secret_values(args: Mapping[str, Any], declared_secrets: Iterable[str]) -> dict[str, Any]:
    """The real values at the declared names, by declared name (only those the arguments carry).
    This is what a `WorkOrder` holds in memory."""
    found: dict[str, Any] = {}
    for name in sorted(declared_secrets):
        path = _resolve(args, name)
        if path is not None:
            found[name] = _copy(_get(args, path))
    return found


def restore_args(args: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """`args` (as `spec.json` holds them) with the real values put back at their declared
    names. The child's in-memory arguments; never written."""
    out = _copy(dict(args))
    for name in sorted(values, key=lambda n: (n.count("."), n)):
        path = _resolve(out, name)
        if path is not None:
            _set(out, path, _copy(values[name]))
    return out  # type: ignore[no-any-return]


def secret_strings(values: Mapping[str, Any]) -> frozenset[str]:
    """Every non-empty string leaf of the secret values: the set `scrub` looks for."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node:
                found.add(node)
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    for value in values.values():
        walk(value)
    return frozenset(found)


# --- the environment hand-over ------------------------------------------------------------------


def encode_env(values: Mapping[str, Any]) -> str:
    return json.dumps(dict(values), sort_keys=True, separators=(",", ":"))


def _decode_env(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def env_values(environ: Mapping[str, str]) -> dict[str, Any]:
    """The secret values an environment carries (empty when it carries none). Reads only."""
    return _decode_env(environ.get(SECRETS_ENV))


def take_env(environ: MutableMapping[str, str]) -> dict[str, Any]:
    """The secret values an environment carries, and the variable removed from it, so a process
    the plugin starts afterwards never inherits them."""
    return _decode_env(environ.pop(SECRETS_ENV, None))


def env_strings(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """The scrub set of the running process's environment (the wrapper reads this: it never
    touches the plugin's arguments, only the console the plugin wrote)."""
    return secret_strings(env_values(os.environ if environ is None else environ))


# --- the scrub ---------------------------------------------------------------------------------


def _forms(secrets: Iterable[str]) -> list[str]:
    """Each secret as it can appear in text: as is, and as JSON escapes it inside a string (both
    ASCII-escaped and not), longest first so a secret that contains another is replaced whole."""
    forms: set[str] = set()
    for secret in secrets:
        if not secret:
            continue
        forms.add(secret)
        forms.add(json.dumps(secret, ensure_ascii=True)[1:-1])
        forms.add(json.dumps(secret, ensure_ascii=False)[1:-1])
    return sorted(forms, key=lambda form: (-len(form), form))


@overload
def scrub(data: str, secrets: Iterable[str]) -> str: ...
@overload
def scrub(data: bytes, secrets: Iterable[str]) -> bytes: ...
def scrub(data: str | bytes, secrets: Iterable[str]) -> str | bytes:
    """`data` with every occurrence of every secret replaced by `REDACTED`; the same type back."""
    forms = _forms(secrets)
    if not forms:
        return data
    if isinstance(data, bytes):
        out = data
        for form in forms:
            out = out.replace(form.encode("utf-8"), REDACTED.encode("utf-8"))
        return out
    text = data
    for form in forms:
        text = text.replace(form, REDACTED)
    return text


def scrub_cut(text: str, secrets: Iterable[str]) -> str:
    """`scrub` for text that was cut off at its end (a capped capture): a secret the cut split in
    two leaves its first half at the very end, and that tail is replaced too."""
    text = scrub(text, secrets)
    longest = 0
    for form in _forms(secrets):
        for size in range(min(len(form) - 1, len(text)), longest, -1):
            if text.endswith(form[:size]):
                longest = size
                break
    return text[: len(text) - longest] + REDACTED if longest else text


def holds_secret(data: bytes, secrets: Iterable[str]) -> bool:
    """Whether `data` contains any secret (in any form `scrub` would replace)."""
    return any(form.encode("utf-8") in data for form in _forms(secrets))


def scrub_json(value: Any, secrets: Iterable[str]) -> Any:
    """A JSON-shaped value with the scrub applied to every string in it, keys included. Applied
    before the value is encoded, so a secret is found as itself and never in its escaped form."""
    forms = frozenset(secrets)
    if not forms:
        return value
    return _map_strings(value, lambda text: scrub(text, forms))


# --- the scrub every write path applies --------------------------------------------------------


@dataclass(frozen=True)
class Scrubber:
    """What one process applies before it writes: the run's declared secret values (replaced by
    `REDACTED`) and its host roots (`errtext.replace_roots`: TRESTLE_HOME, the run directory, the
    work directory and $HOME become their fixed tokens, so an answer built from a record never
    carries a host path). Secrets go first. An empty scrubber changes nothing."""

    secrets: frozenset[str] = frozenset()
    roots: Mapping[str, Path] = field(default_factory=dict)

    def text(self, value: str) -> str:
        return replace_roots(scrub(value, self.secrets), self.roots)

    def capped_text(self, value: str) -> str:
        """`text` for text that a byte cap cut off at its end (see `scrub_cut`)."""
        return replace_roots(scrub_cut(value, self.secrets), self.roots)

    def data(self, data: bytes) -> tuple[bytes, bool]:
        """What a write of `data` should put on disk: (bytes, refused). Text has secrets and host
        paths scrubbed; a binary that holds a secret is refused (`REFUSED_BINARY`)."""
        if not self.secrets and not self.roots:
            return data, False
        if is_text(data):
            return self.text(data.decode("utf-8")).encode("utf-8"), False
        if holds_secret(data, self.secrets):
            return REFUSED_BINARY, True
        return data, False

    def json(self, value: Any) -> Any:
        """A JSON-shaped value with `text` applied to every string in it, keys included."""
        if not self.secrets and not self.roots:
            return value
        return _map_strings(value, self.text)


NO_SCRUB = Scrubber()


def _map_strings(value: Any, fn: Any) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {(fn(k) if isinstance(k, str) else k): _map_strings(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, tuple):
        return tuple(_map_strings(v, fn) for v in value)
    return value


def run_roots(run_dir: Path, home: Path | None = None) -> dict[str, Path]:
    """The host roots a run's writes are scrubbed of: TRESTLE_HOME (`home`, else the environment's),
    the run directory, the plugin's work directory and the user's home."""
    roots: dict[str, Path] = {"run": run_dir, "cwd": run_dir / "work", "user-home": Path.home()}
    if home is None and os.environ.get("TRESTLE_HOME"):
        home = Path(os.environ["TRESTLE_HOME"])
    if home is not None:
        roots["home"] = home
    return roots


# --- files -------------------------------------------------------------------------------------


def is_text(data: bytes) -> bool:
    """A file is text when it has no NUL byte and decodes as UTF-8; anything else is binary."""
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def scrub_bytes(data: bytes, secrets: Iterable[str]) -> tuple[bytes, bool]:
    """What a write of `data` should put on disk: (bytes, refused). Text is scrubbed; a binary
    that holds a secret is refused, and `REFUSED_BINARY` stands in for it."""
    forms = frozenset(secrets)
    if not forms:
        return data, False
    if is_text(data):
        return scrub(data, forms), False
    if holds_secret(data, forms):
        return REFUSED_BINARY, True
    return data, False


def _file_holds_secret(path: Path, secrets: frozenset[str]) -> bool:
    """A streamed match, holding back the longest form's length between chunks."""
    needles = [form.encode("utf-8") for form in _forms(secrets)]
    keep = max(len(n) for n in needles) - 1
    tail = b""
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            window = tail + chunk
            if any(n in window for n in needles):
                return True
            tail = window[-keep:] if keep else b""
    return False


def copy_scrubbed(src: Path, dest: Path, scrubber: Scrubber) -> bool:
    """Copy `src` to `dest` through the scrubber (a promotion). Returns True when the file is
    refused (a binary holding a secret): nothing is written to `dest`. A file above
    `TEXT_SCRUB_MAX` is streamed for a secret and otherwise copied as it is."""
    if src.stat().st_size > TEXT_SCRUB_MAX:
        if scrubber.secrets and _file_holds_secret(src, scrubber.secrets):
            return True
        shutil.copy2(src, dest)
        return False
    data, refused = scrubber.data(src.read_bytes())
    if refused:
        return True
    dest.write_bytes(data)
    return False


def scrub_file(path: Path, secrets: Iterable[str]) -> bool:
    """Scrub one file in place. Returns True when a binary holding a secret was refused (its
    content replaced by `REFUSED_BINARY`); text is rewritten only when it changed."""
    forms = frozenset(secrets)
    if not forms or path.is_symlink() or not path.is_file():
        return False
    try:
        size = path.stat().st_size
        if size > TEXT_SCRUB_MAX:
            if not _file_holds_secret(path, forms):
                return False
            atomic_write(path, REFUSED_BINARY)
            return True
        data = path.read_bytes()
        out, refused = scrub_bytes(data, forms)
        if out != data:
            atomic_write(path, out)
        return refused
    except OSError:
        return False


def scrub_tree(root: Path, secrets: Iterable[str]) -> int:
    """`scrub_file` over every regular file under `root` (the plugin's work directory, once
    nothing can write to it). Returns the number of binary files refused."""
    forms = frozenset(secrets)
    if not forms or not root.is_dir():
        return 0
    refused = 0
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            if scrub_file(Path(dirpath) / name, forms):
                refused += 1
    return refused
