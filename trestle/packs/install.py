"""`trestle pack install | remove | upgrade`: the operator's commands (P-4, P-5, P-7, P-11, P-13,
P-16). Agents never install anything, and an install never restarts the service: it writes the
lock, and the registry rescans.

    install <name | path | git-url@ref[#subdir]> [--owner company|user] [--replace PACK] [--no-wait]

1. Resolve the pack: a bundled pack by name from the release checkout (P-2); a git repository
   cloned at a tag or commit, a branch refused (P-4); a path, used in place (P-13).
2. Check it: `pack.toml`, `requires.tools` on the service PATH, plugin names that collide with an
   installed pack unless declared (`replaces`, `overrides`, P-11). `requires.packs` are installed
   first from the same release (P-16).
3. Wait for live runs of the plugins being replaced only (`--no-wait`: refuse while any is live).
4. Copy a pinned pack (bundled, git) to `$TRESTLE_HOME/packs/<owner>/<pack>/` and install its
   library from the copy, non-editable (P-7); a path install's library is editable (P-13).
5. Write the lock.

The host steps (git, pip, PATH lookup, live runs, sleeping) go through `Runners`, so the tests
need none of them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import trestle
from trestle.packs import PackError
from trestle.packs.bundled import available_bundled, bundled_dir, bundled_root
from trestle.packs.lock import (
    KIND_BUNDLED,
    KIND_GIT,
    KIND_PATH,
    OWNER_COMPANY,
    OWNER_LOCAL,
    OWNER_TRESTLE,
    OWNER_USER,
    LockedPack,
    install_session,
    packs_root,
    read_lock,
    write_lock,
)
from trestle.packs.manifest import (
    PACK_NAME,
    PLUGINS_DIR,
    PackManifest,
    load_manifest,
    read_library,
    wrapper_names,
)

INSTALL_OWNERS = (OWNER_COMPANY, OWNER_USER)
GIT_PREFIXES = ("git@", "https://", "http://", "ssh://", "git://", "file://")
_COMMIT = re.compile(r"[0-9a-f]{7,40}\Z")
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".venv", "venv", "build", "dist", "*.egg-info", ".pytest_cache"
)
DEFAULT_WAIT_S = 3600.0
WAIT_POLL_S = 1.0


def _run_git(argv: list[str], cwd: Path | None) -> str:
    """git with the operator's own credentials (P-4: Trestle stores no token)."""
    try:
        proc = subprocess.run(
            ["git", *argv],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        raise PackError("git is not on PATH: a git install needs it") from None
    if proc.returncode != 0:
        raise GitFailed((proc.stderr or proc.stdout).strip()[:300])
    return proc.stdout.strip()


class GitFailed(PackError):
    pass


def _run_pip(argv: list[str]) -> None:
    """pip in the service's own Python: the interpreter this command runs on, which is the one
    the service and its plugin children run on (`python_argv`)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pip", *argv], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise PackError("pip " + " ".join(argv[:2]) + " failed: " + " | ".join(tail))


def live_plugin_runs(home: Path) -> dict[str, str]:
    """run id -> plugin of every live root run on the home (its spec names the plugin)."""
    import json

    from trestle.server.home import live_run_ids, marked_run_dir, read_marker
    from trestle.server.ledger import evidence_dir

    out: dict[str, str] = {}
    for run_id in live_run_ids(home):
        run_dir = marked_run_dir(home, run_id, read_marker(home, run_id))
        if run_dir is None:
            continue
        try:
            spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(spec, dict) and isinstance(spec.get("plugin"), str):
            out[run_id] = spec["plugin"]
    return out


@dataclass
class Runners:
    git: Callable[[list[str], Path | None], str] = _run_git
    pip: Callable[[list[str]], None] = _run_pip
    which: Callable[[str], str | None] = shutil.which
    live_runs: Callable[[Path], dict[str, str]] = live_plugin_runs
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class Source:
    kind: str
    location: str  # bundled: the name; git: the URL; path: the resolved folder
    ref: str | None = None
    subdir: str | None = None


def parse_spec(spec: str) -> Source:
    """`<name | path | git-url@ref[#subdir]>`."""
    if spec.startswith(GIT_PREFIXES):
        body, _, subdir = spec.partition("#")
        at = body.rfind("@")
        left, ref = body[:at], body[at + 1 :]
        if at < 0 or not ref or ":" in ref or ("/" not in left and ":" not in left):
            raise PackError(
                f"{spec!r}: a git install is pinned to a tag or commit: <url>@<tag|commit>[#subdir]"
            )
        if subdir and (Path(subdir).is_absolute() or ".." in Path(subdir).parts):
            raise PackError(f"{spec!r}: #subdir must be a folder inside the repository")
        return Source(kind=KIND_GIT, location=left, ref=ref, subdir=subdir or None)
    if PACK_NAME.match(spec) and not Path(spec).exists():
        return Source(kind=KIND_BUNDLED, location=spec)
    path = Path(spec).expanduser().resolve()
    if not path.is_dir():
        raise PackError(f"{spec!r} is not a bundled pack name, a folder or a git URL@ref")
    return Source(kind=KIND_PATH, location=str(path))


@dataclass
class Installer:
    home: Path
    runners: Runners = field(default_factory=Runners)
    out: TextIO = field(default_factory=lambda: sys.stdout)
    bundled: Path | None = None
    wait_s: float = DEFAULT_WAIT_S
    no_wait: bool = False

    def _bundled_root(self) -> Path | None:
        return self.bundled if self.bundled is not None else bundled_root()

    def say(self, line: str) -> None:
        print(line, file=self.out)

    # --- install -----------------------------------------------------------------------------

    def install(
        self, spec: str, *, owner: str | None = None, replace_pack: str | None = None
    ) -> LockedPack:
        source = parse_spec(spec)
        with install_session(self.home):
            return self._install(source, owner=owner, replace_pack=replace_pack, chain=())

    def _install(
        self,
        source: Source,
        *,
        owner: str | None,
        replace_pack: str | None,
        chain: tuple[str, ...],
    ) -> LockedPack:
        owner = self._owner(source, owner)
        staging = packs_root(self.home) / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=staging) as tmp:
            pack_dir, commit = self._resolve(source, Path(tmp))
            manifest = load_manifest(pack_dir)
            if source.kind == KIND_BUNDLED and manifest.name != source.location:
                raise PackError(
                    f"bundled pack folder {source.location!r} declares name {manifest.name!r}"
                )
            if manifest.name in chain:
                raise PackError(f"requires.packs cycle: {' -> '.join((*chain, manifest.name))}")
            locked = read_lock(self.home)
            existing = next((p for p in locked if p.name == manifest.name), None)
            displaced = self._displaced(source, manifest, existing, replace_pack, locked)
            self._check_tools(manifest)
            names = wrapper_names(pack_dir / PLUGINS_DIR)
            if not names:
                raise PackError(f"pack {manifest.name}: {PLUGINS_DIR}/ has no plugin files")
            gone = {d.name for d in displaced}
            remaining = [p for p in locked if p.name not in gone]
            for required in manifest.packs:
                if any(p.name == required for p in remaining):
                    continue
                if bundled_dir(required, self._bundled_root()) is None:
                    raise PackError(
                        f"pack {manifest.name} requires pack {required!r}, which is not "
                        "installed and is not a bundled pack of this release; install it first"
                    )
                self.say(f"installing required pack {required} (bundled)")
                self._install(
                    Source(kind=KIND_BUNDLED, location=required),
                    owner=None,
                    replace_pack=None,
                    chain=(*chain, manifest.name),
                )
                remaining = [p for p in read_lock(self.home) if p.name not in gone]
            self._check_collisions(manifest, set(names), remaining)
            self._wait_for(displaced)
            library = read_library(pack_dir, manifest)
            if source.kind == KIND_PATH:
                final_dir = pack_dir
            else:
                final_dir = self._place_copy(pack_dir, owner, manifest.name)
            final_library = None
            if library is not None:
                final_library = final_dir / library.path.relative_to(pack_dir.resolve())
                self._install_library(source.kind, final_library)
            for old in displaced:
                self._drop_copy(old, keep=final_dir)
            version = (library.version if library is not None else None) or (
                trestle.__version__
                if source.kind == KIND_BUNDLED
                else (commit[:12] if commit else "path")
            )
            entry = LockedPack(
                name=manifest.name,
                owner=owner,
                kind=source.kind,
                location=source.location,
                version=version,
                pack_dir=str(final_dir),
                plugins_dir=str(final_dir / PLUGINS_DIR),
                ref=source.ref,
                commit=commit,
                subdir=source.subdir,
                library_dir=None if final_library is None else str(final_library),
                library_dist=None if library is None else library.dist,
                replaces=manifest.replaces,
                overrides=manifest.overrides,
                requires_packs=manifest.packs,
                config_files=manifest.config_files,
                installed_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
                plugins=tuple(sorted(names)),
            )
            self._write(entry, displaced)
            what = "upgraded" if existing is not None and existing in displaced else "installed"
            self.say(f"{what} {entry.label}: {', '.join(entry.plugins)} ({final_dir})")
            for old in displaced:
                if old.name != entry.name:
                    self.say(f"replaced {old.label}")
            return entry

    def _owner(self, source: Source, owner: str | None) -> str:
        if source.kind == KIND_BUNDLED:
            if owner not in (None, OWNER_TRESTLE):
                raise PackError("a bundled pack's owner is always trestle (P-5)")
            return OWNER_TRESTLE
        if owner is None:
            return OWNER_USER
        if owner == OWNER_TRESTLE:
            raise PackError("owner trestle is only for bundled packs installed by name (P-5)")
        if owner == OWNER_LOCAL:
            raise PackError("owner local is for loose plugin files, not packs (P-5)")
        if owner not in INSTALL_OWNERS:
            raise PackError(f"--owner must be one of {', '.join(INSTALL_OWNERS)}")
        return owner

    def _resolve(self, source: Source, tmp: Path) -> tuple[Path, str | None]:
        if source.kind == KIND_BUNDLED:
            found = bundled_dir(source.location, self._bundled_root())
            if found is None:
                available = ", ".join(sorted(available_bundled(self._bundled_root()))) or "none"
                raise PackError(
                    f"no bundled pack {source.location!r} in this release (available: {available})"
                )
            return found, None
        if source.kind == KIND_PATH:
            return Path(source.location), None
        clone = tmp / "clone"
        try:
            self.runners.git(
                ["clone", "--quiet", "--no-checkout", source.location, str(clone)], None
            )
        except GitFailed as exc:
            raise PackError(f"git clone {source.location} failed: {exc}") from None
        commit = self._pinned_commit(clone, str(source.ref))
        self.runners.git(["checkout", "--quiet", "--detach", commit], clone)
        pack_dir = clone / source.subdir if source.subdir else clone
        if not pack_dir.is_dir():
            raise PackError(f"{source.location}@{source.ref} has no folder {source.subdir!r}")
        return pack_dir, commit

    def _pinned_commit(self, clone: Path, ref: str) -> str:
        """A tag or a commit; a branch is refused (P-4): a pinned pack runs one known commit."""

        def verify(name: str) -> str | None:
            try:
                out = self.runners.git(["rev-parse", "--verify", "--quiet", name], clone)
            except GitFailed:
                return None
            return out or None

        tagged = verify(f"refs/tags/{ref}^{{commit}}")
        if tagged is not None:
            return tagged
        if verify(f"refs/remotes/origin/{ref}^{{commit}}") is not None:
            raise PackError(f"{ref!r} is a branch: pin the install to a tag or a commit (P-4)")
        if _COMMIT.match(ref):
            found = verify(f"{ref}^{{commit}}")
            if found is not None:
                return found
        raise PackError(f"{ref!r} is not a tag or commit of the repository")

    def _displaced(
        self,
        source: Source,
        manifest: PackManifest,
        existing: LockedPack | None,
        replace_pack: str | None,
        locked: list[LockedPack],
    ) -> list[LockedPack]:
        """The installed packs this install takes the place of: the same pack from the same
        place (an upgrade), and the `--replace` target (P-13)."""
        out: list[LockedPack] = []
        if replace_pack is not None:
            target = next((p for p in locked if p.name == replace_pack), None)
            if target is None:
                raise PackError(f"--replace {replace_pack}: no installed pack has that name")
            out.append(target)
        if existing is not None and existing not in out:
            same_place = (
                existing.kind == source.kind
                and existing.location == source.location
                and existing.subdir == source.subdir
            )
            if not same_place:
                raise PackError(
                    f"pack {manifest.name} is installed already, as {existing.label} from "
                    f"{existing.location}; pass --replace {manifest.name} to swap it out"
                )
            out.append(existing)
        return out

    def _check_tools(self, manifest: PackManifest) -> None:
        missing = [tool for tool in manifest.tools if self.runners.which(tool) is None]
        if missing:
            raise PackError(
                f"pack {manifest.name} needs {', '.join(missing)} on the service PATH "
                f"({os.environ.get('PATH', '')[:200]})"
            )

    def _check_collisions(
        self, manifest: PackManifest, names: set[str], installed: list[LockedPack]
    ) -> None:
        """P-11: two packs claiming one plugin name are refused, naming both, unless one of them
        declares it (`replaces` the other pack, or `overrides` the name)."""
        for other in installed:
            for name in sorted(names & set(other.plugins)):
                declared = (
                    manifest.replaces == other.name
                    or name in manifest.overrides
                    or other.replaces == manifest.name
                    or name in other.overrides
                )
                if not declared:
                    raise PackError(
                        f"pack {manifest.name} and installed pack {other.label} both provide "
                        f"plugin {name!r}; declare it in pack.toml [pack] (replaces = "
                        f'"{other.name}", or overrides = ["{name}"]) or rename the plugin'
                    )

    def _wait_for(self, displaced: list[LockedPack]) -> None:
        """Wait for live runs of the plugins being replaced only; nothing else waits (unattended
        runs come first). `--no-wait` refuses while one is live."""
        plugins = {name for pack in displaced for name in pack.plugins}
        if not plugins:
            return
        deadline = self.runners.clock() + self.wait_s
        announced = False
        while True:
            live = sorted(
                f"{run} ({plugin})"
                for run, plugin in self.runners.live_runs(self.home).items()
                if plugin in plugins
            )
            if not live:
                return
            if self.no_wait:
                raise PackError(f"live runs use the plugins being replaced: {', '.join(live)}")
            if self.runners.clock() >= deadline:
                raise PackError(
                    f"gave up after {self.wait_s:.0f} s waiting for live runs: {', '.join(live)}"
                )
            if not announced:
                self.say(f"waiting for live runs of the replaced plugins: {', '.join(live)}")
                announced = True
            self.runners.sleep(WAIT_POLL_S)

    def _place_copy(self, pack_dir: Path, owner: str, name: str) -> Path:
        """The pinned copy (P-7), swapped in whole: the new copy is written beside the old and
        renamed over it. Runs never read it (their snapshot and the installed library are what
        runs), so no live run is disturbed."""
        dest = packs_root(self.home) / owner / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        fresh = dest.with_name(f".{name}.new")
        old = dest.with_name(f".{name}.old")
        for path in (fresh, old):
            if path.exists():
                shutil.rmtree(path)
        shutil.copytree(pack_dir, fresh, ignore=_COPY_IGNORE)
        if dest.exists():
            dest.rename(old)
        fresh.rename(dest)
        if old.exists():
            shutil.rmtree(old)
        return dest

    def _install_library(self, kind: str, library: Path) -> None:
        if kind == KIND_PATH:
            self.runners.pip(["install", "--quiet", "-e", str(library)])
            return
        if kind == KIND_BUNDLED:
            # bundled libraries are `trestle*` names pip must never look for on PyPI
            self.runners.pip(["install", "--quiet", "--no-deps", "--force-reinstall", str(library)])
            return
        # the library's own dependencies, then the library itself rebuilt from the copy (a path
        # requirement of an unchanged version would otherwise be left as it was)
        self.runners.pip(["install", "--quiet", str(library)])
        self.runners.pip(["install", "--quiet", "--no-deps", "--force-reinstall", str(library)])

    def _drop_copy(self, old: LockedPack, *, keep: Path | None) -> None:
        """A displaced pinned pack's copy goes (an owner change moves it); a path install's
        folder is the operator's and is never touched."""
        if old.kind == KIND_PATH:
            return
        path = Path(old.pack_dir)
        root = packs_root(self.home).resolve()
        if keep is not None and path.resolve() == keep.resolve():
            return
        if root not in path.resolve().parents:
            return
        shutil.rmtree(path, ignore_errors=True)

    def _write(self, entry: LockedPack, displaced: list[LockedPack]) -> None:
        gone = {pack.name for pack in displaced}
        packs = read_lock(self.home)
        position = next((i for i, p in enumerate(packs) if p.name in gone), len(packs))
        kept = [p for p in packs if p.name not in gone]
        position = min(position, len(kept))
        kept.insert(position, entry)
        write_lock(self.home, kept)

    # --- remove and upgrade ------------------------------------------------------------------

    def remove(self, name: str) -> LockedPack:
        with install_session(self.home):
            packs = read_lock(self.home)
            target = next((p for p in packs if p.name == name), None)
            if target is None:
                raise PackError(f"no installed pack {name!r}")
            needed_by = [p.name for p in packs if name in p.requires_packs]
            if needed_by:
                raise PackError(f"pack {name} is required by {', '.join(needed_by)}")
            self._wait_for([target])
            write_lock(self.home, [p for p in packs if p.name != name])
            if target.library_dist is not None:
                try:
                    self.runners.pip(["uninstall", "--quiet", "-y", target.library_dist])
                except PackError as exc:
                    self.say(f"warning: {exc}")
            self._drop_copy(target, keep=None)
            self.say(f"removed {target.label}")
            return target

    def upgrade(self, names: list[str]) -> list[LockedPack]:
        """Install each named pack (all, when none is named) again from where it was installed:
        a bundled pack from the running release, a path from its folder, a git pack at its
        recorded ref."""
        packs = read_lock(self.home)
        chosen = [p for p in packs if not names or p.name in names]
        unknown = sorted(set(names) - {p.name for p in packs})
        if unknown:
            raise PackError(f"no installed pack {unknown[0]!r}")
        out: list[LockedPack] = []
        for pack in chosen:
            spec = pack.location if pack.kind != KIND_GIT else f"{pack.location}@{pack.ref}"
            if pack.kind == KIND_GIT and pack.subdir:
                spec += f"#{pack.subdir}"
            source = (
                parse_spec(spec) if pack.kind != KIND_BUNDLED else Source(KIND_BUNDLED, pack.name)
            )
            with install_session(self.home):
                out.append(self._install(source, owner=pack.owner, replace_pack=None, chain=()))
        return out
