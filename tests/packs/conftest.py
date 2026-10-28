"""Pack fixtures: small packs written to a temp folder, and fake host runners."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from trestle.packs.install import Installer, Runners

PLUGIN_SOURCE = """from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle{decorator}
def {name}(ctx: Context) -> dict[str, str]:
    return {{"from": "{tag}"}}
"""


def plugin_source(name: str, tag: str = "", decorator: str = "") -> str:
    return PLUGIN_SOURCE.format(name=name, tag=tag or name, decorator=decorator)


def make_pack(
    root: Path,
    name: str,
    plugins: list[str] | dict[str, str],
    *,
    folder: str | None = None,
    library: bool = False,
    extra_pack: str = "",
    extra: str = "",
) -> Path:
    """A pack folder: pack.toml, one wrapper per plugin name (or name -> source), and with
    `library` a pyproject.toml beside it."""
    pack_dir = root / (folder or name)
    (pack_dir / "plugins").mkdir(parents=True, exist_ok=True)
    lib = 'library = "."\n' if library else ""
    (pack_dir / "pack.toml").write_text(
        f'[pack]\nname = "{name}"\nsummary = "test pack"\n{lib}{extra_pack}\n{extra}',
        encoding="utf-8",
    )
    sources = plugins if isinstance(plugins, dict) else {p: plugin_source(p, name) for p in plugins}
    for plugin, source in sources.items():
        (pack_dir / "plugins" / f"{plugin}.py").write_text(source, encoding="utf-8")
    if library:
        (pack_dir / "pyproject.toml").write_text(
            f'[project]\nname = "pack-{name}"\nversion = "1.2.3"\n', encoding="utf-8"
        )
    return pack_dir


@dataclass
class FakeHost:
    pip_calls: list[list[str]] = field(default_factory=list)
    tools: set[str] = field(default_factory=lambda: {"git"})
    live: dict[str, str] = field(default_factory=dict)
    # each sleep ends one live run (the oldest), so a wait finishes
    sleeps: int = 0

    def runners(self) -> Runners:
        def pip(argv: list[str]) -> None:
            self.pip_calls.append(argv)

        def which(tool: str) -> str | None:
            return f"/usr/bin/{tool}" if tool in self.tools else None

        def sleep(_s: float) -> None:
            self.sleeps += 1
            if self.live:
                self.live.pop(sorted(self.live)[0])

        def git(argv: list[str], cwd: Path | None) -> str:
            raise AssertionError(f"unexpected git call {argv}")

        return Runners(
            git=git, pip=pip, which=which, live_runs=lambda _h: dict(self.live), sleep=sleep
        )


@pytest.fixture
def host() -> FakeHost:
    return FakeHost()


@pytest.fixture
def pack_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def bundled(tmp_path: Path) -> Path:
    root = tmp_path / "release" / "packages"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def installer(pack_home: Path, host: FakeHost, bundled: Path) -> Installer:
    return Installer(home=pack_home, runners=host.runners(), out=io.StringIO(), bundled=bundled)
