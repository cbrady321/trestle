"""The `examples/packs` migration (P-16): `trestle init --upgrade` turns an `examples/packs` entry
of `[plugins] paths` into the installed bundled packs docker, pytest, migrate and stack, with no
manual edit, and leaves the config alone when it cannot do so safely."""

from __future__ import annotations

import io
import tomllib
from pathlib import Path

import pytest

from trestle.packs import PackError
from trestle.packs.install import Installer
from trestle.packs.lock import read_lock
from trestle.packs.migrate_home import drop_plugin_paths, migrate_examples_packs

from .conftest import FakeHost, make_pack

CONFIG = """# operator's config
[server]
port = 8765

[plugins]
# loose files first, then the tool plugins
paths = [
    "~/.trestle/plugins",   # mine
    "/src/trestle/examples/packs",
    "/src/project/tools",
]

[plugins.env]
TRESTLE_DOCKER_PATH = "/usr/local/bin/docker"
"""


def _write(home: Path, text: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _drop(raw: str) -> bool:
    return raw.endswith("examples/packs")


def test_drop_keeps_everything_else_byte_for_byte() -> None:
    out = drop_plugin_paths(CONFIG, _drop)
    assert out is not None
    assert tomllib.loads(out)["plugins"]["paths"] == ["~/.trestle/plugins", "/src/project/tools"]
    before, after = CONFIG.split("paths = ["), out.split("paths = [")
    assert before[0] == after[0]
    assert out.endswith(CONFIG[CONFIG.index("\n\n[plugins.env]") :])


@pytest.mark.parametrize(
    "text",
    [
        'plugins.paths = ["/src/trestle/examples/packs"]\n',  # a dotted key, not the table
        '[plugins]\npaths = ["/a"]\n',  # nothing to drop
        "[plugins\n",  # not TOML
    ],
)
def test_drop_refuses_what_it_cannot_rewrite(text: str) -> None:
    assert drop_plugin_paths(text, _drop) is None


def test_bracket_and_hash_inside_strings_are_not_structure() -> None:
    text = "[plugins]\npaths = [\"/x]#y\", '/z/examples/packs'] # done\n[other]\nk = 1\n"
    out = drop_plugin_paths(text, _drop)
    assert out == '[plugins]\npaths = ["/x]#y"] # done\n[other]\nk = 1\n'


def _migrate(home: Path, install, installed=lambda: set()) -> tuple[bool, list[str]]:  # noqa: ANN001
    said: list[str] = []
    return migrate_examples_packs(home, install, installed, said.append), said


def test_entry_becomes_installed_packs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = _write(home, CONFIG)
    installs: list[str] = []
    changed, said = _migrate(home, installs.append)
    assert changed and installs == ["stack"]
    assert (
        "/src/trestle/examples/packs" not in tomllib.loads(config.read_text())["plugins"]["paths"]
    )
    assert "docker, pytest, migrate, stack" in said[-1]


def test_already_installed_packs_only_drop_the_entry(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write(home, CONFIG)
    installs: list[str] = []
    every = {"docker", "pytest", "migrate", "stack"}
    changed, _ = _migrate(home, installs.append, lambda: every)
    assert changed and installs == []


def test_failed_install_leaves_the_config_and_names_the_command(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = _write(home, CONFIG)

    def install(_name: str) -> None:
        raise PackError("no bundled packs in this build")

    changed, said = _migrate(home, install)
    assert not changed and config.read_text() == CONFIG
    assert said[-1].endswith("The operator runs: trestle pack install stack")


def test_no_entry_no_change(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write(home, '[plugins]\npaths = ["~/.trestle/plugins"]\n')
    assert _migrate(home, lambda _n: pytest.fail("installed")) == (False, [])
    assert _migrate(tmp_path / "bare", lambda _n: pytest.fail("installed")) == (False, [])


def test_with_the_installer_stack_brings_its_tool_packs(
    pack_home: Path, bundled: Path, host: FakeHost
) -> None:
    for name, plugin in (("docker", "docker_stack"), ("pytest", "pytest_run")):
        make_pack(bundled, name, [plugin], folder=f"trestle-pack-{name}", library=True)
    make_pack(bundled, "migrate", ["migrate_apply"], folder="trestle-pack-migrate")
    make_pack(
        bundled,
        "stack",
        ["stack_pipeline"],
        folder="trestle-pack-stack",
        extra='[requires]\npacks = ["docker", "pytest", "migrate"]\n',
    )
    _write(pack_home, CONFIG)
    installer = Installer(
        home=pack_home, runners=host.runners(), out=io.StringIO(), bundled=bundled
    )
    changed, _ = _migrate(
        pack_home, installer.install, lambda: {p.name for p in read_lock(pack_home)}
    )
    assert changed
    assert {p.name for p in read_lock(pack_home)} == {"docker", "pytest", "migrate", "stack"}


def test_init_upgrade_runs_the_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trestle.packs import migrate_home
    from trestle.server.init_cmd import run_init

    home = tmp_path / "home"
    _write(home, CONFIG)
    seen: list[Path] = []
    monkeypatch.setattr(migrate_home, "migrate_home", lambda h, say: seen.append(h) or False)
    assert run_init(home=home, seed=False, upgrade=True) == 0
    assert seen == [home]
    seen.clear()
    assert run_init(home=home, seed=False) == 0
    assert seen == []
