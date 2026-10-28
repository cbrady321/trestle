"""`$TRESTLE_HOME/packs.lock`: every installed pack, in install order (the registry's pack folder
order). Written whole and atomically, so each write moves its mtime, which is part of the
registry's scan signature: an install republishes with no restart."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from trestle.common.fsutil import atomic_write
from trestle.packs import PackError

LOCK_FILE = "packs.lock"
LOCK_FORMAT = 1
# the lock commands hold while they change the lock and the pack copies (one at a time per home)
INSTALL_LOCK = "packs-install.lock"
PACKS_DIR = "packs"

OWNER_TRESTLE = "trestle"
OWNER_COMPANY = "company"
OWNER_USER = "user"
OWNER_LOCAL = "local"

KIND_BUNDLED = "bundled"
KIND_GIT = "git"
KIND_PATH = "path"


@dataclass(frozen=True)
class LockedPack:
    """One installed pack. `pack_dir` is the copy under `packs/<owner>/<pack>/` for a pinned pack
    (bundled, git), the folder itself for a path install (P-7)."""

    name: str
    owner: str
    kind: str
    location: str
    version: str
    pack_dir: str
    plugins_dir: str
    ref: str | None = None
    commit: str | None = None
    subdir: str | None = None
    library_dir: str | None = None
    library_dist: str | None = None
    replaces: str | None = None
    overrides: tuple[str, ...] = ()
    requires_packs: tuple[str, ...] = ()
    config_files: tuple[str, ...] = ()
    installed_at: str = ""
    plugins: tuple[str, ...] = field(default=())

    @property
    def label(self) -> str:
        """How rows show the pack (P-1): `github@0.5.0 (trestle)`."""
        return f"{self.name}@{self.version} ({self.owner})"

    @property
    def live(self) -> bool:
        """A path install is served in place and its library is editable (P-13)."""
        return self.kind == KIND_PATH

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("overrides", "requires_packs", "config_files", "plugins"):
            out[key] = list(out[key])
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LockedPack:
        def strs(key: str) -> tuple[str, ...]:
            value = raw.get(key, [])
            return tuple(str(x) for x in value) if isinstance(value, list) else ()

        def opt(key: str) -> str | None:
            value = raw.get(key)
            return str(value) if isinstance(value, str) else None

        return cls(
            name=str(raw["name"]),
            owner=str(raw["owner"]),
            kind=str(raw["kind"]),
            location=str(raw["location"]),
            version=str(raw.get("version", "")),
            pack_dir=str(raw["pack_dir"]),
            plugins_dir=str(raw["plugins_dir"]),
            ref=opt("ref"),
            commit=opt("commit"),
            subdir=opt("subdir"),
            library_dir=opt("library_dir"),
            library_dist=opt("library_dist"),
            replaces=opt("replaces"),
            overrides=strs("overrides"),
            requires_packs=strs("requires_packs"),
            config_files=strs("config_files"),
            installed_at=str(raw.get("installed_at", "")),
            plugins=strs("plugins"),
        )


def lock_path(home: Path) -> Path:
    return home / LOCK_FILE


def packs_root(home: Path) -> Path:
    return home / PACKS_DIR


def read_lock(home: Path) -> list[LockedPack]:
    """The installed packs, in install order. No lock file: none. An unreadable lock is an error
    the caller reports (the registry logs it and serves no pack, never a guess)."""
    path = lock_path(home)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise PackError(f"{path} is unreadable: {exc}") from None
    try:
        raw = json.loads(text)
        entries = raw["packs"]
        if raw.get("format") != LOCK_FORMAT or not isinstance(entries, list):
            raise ValueError("unknown lock format")
        return [LockedPack.from_dict(entry) for entry in entries]
    except (ValueError, KeyError, TypeError) as exc:
        raise PackError(f"{path} is unreadable: {exc}") from None


def write_lock(home: Path, packs: list[LockedPack]) -> None:
    body = {"format": LOCK_FORMAT, "packs": [pack.to_dict() for pack in packs]}
    atomic_write(lock_path(home), (json.dumps(body, indent=2) + "\n").encode("utf-8"))


def lock_mtime(home: Path) -> int | None:
    try:
        return lock_path(home).stat().st_mtime_ns
    except FileNotFoundError:
        return None


@contextmanager
def install_session(home: Path) -> Iterator[None]:
    """One pack command at a time on a home (the lock and the copies change together)."""
    from trestle.server.home import file_lock, locks_dir

    with file_lock(locks_dir(home) / INSTALL_LOCK):
        yield
