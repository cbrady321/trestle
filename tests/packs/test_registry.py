"""The registry reads packs.lock: folder order, rescan without restart, the winner chosen after
validation, declared overrides, row fields (P-1, P-5, P-7, P-10, P-11, P-13, P-14)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trestle.packs.install import Installer
from trestle.packs.lock import LockedPack, read_lock, write_lock
from trestle.server.main import create_kernel
from trestle.server.registry import Registry

from .conftest import make_pack, plugin_source


@pytest.fixture
def local_dir(tmp_path: Path) -> Path:
    path = tmp_path / "local"
    path.mkdir()
    return path


def _registry(home: Path, local_dir: Path) -> Registry:
    registry = Registry(home=home, plugin_dirs=[local_dir])
    registry.refresh()
    return registry


def _row(registry: Registry, name: str) -> dict[str, object]:
    rows = {row["name"]: row for row in registry.catalog().to_dict()["items"]}
    return rows[name]


def _bump(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))


def test_install_publishes_without_restart(
    installer: Installer, bundled: Path, pack_home: Path, local_dir: Path
) -> None:
    registry = _registry(pack_home, local_dir)
    assert registry.get("docker_stack") is None
    make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker")
    installer.install("docker")
    registry.maybe_refresh()  # the lock's mtime is in the scan signature
    snap = registry.get("docker_stack")
    assert snap is not None
    row = _row(registry, "docker_stack")
    assert row["owner"] == "trestle"
    assert str(row["pack"]).startswith("docker@") and str(row["pack"]).endswith("(trestle)")
    view = registry.describe("docker_stack")
    assert view is not None
    copy = pack_home / "packs" / "trestle" / "docker" / "plugins" / "docker_stack.py"
    assert view["source_path"] == str(copy)  # P-7: the copy agents may read
    assert view["pack"] == row["pack"]
    manifest = Path(snap.source_path).with_name("manifest.json").read_text()
    assert '"owner": "trestle"' in manifest  # P-1: stamped at publication
    installer.remove("docker")
    registry.maybe_refresh()
    assert registry.get("docker_stack") is None


def test_loose_file_overrides_pack_plugin(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    installer.install(str(make_pack(tmp_path, "github", ["gh_pr"])))
    (local_dir / "gh_pr.py").write_text(plugin_source("gh_pr", "local"), encoding="utf-8")
    registry = _registry(pack_home, local_dir)
    row = _row(registry, "gh_pr")
    assert row["owner"] == "local"
    assert "pack" not in row
    assert row["overrides"] == "github@path (user)"
    view = registry.describe("gh_pr")
    assert view is not None and view["source_path"] == str(local_dir / "gh_pr.py")


def test_broken_override_never_hides_the_valid_pack_plugin(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    pack = make_pack(tmp_path, "github", ["gh_pr"])
    installer.install(str(pack))
    (local_dir / "gh_pr.py").write_text(
        plugin_source("gh_pr", "local").replace("-> dict[str, str]", "-> object"),
        encoding="utf-8",
    )
    (local_dir / "gh_pr.py").write_text(
        "from trestle.plugin.surface import trestle\nimport not_a_module_anywhere\n\n"
        "@trestle\ndef gh_pr(ctx) -> dict[str, str]:\n    return {}\n",
        encoding="utf-8",
    )
    registry = _registry(pack_home, local_dir)
    view = registry.describe("gh_pr")
    assert view is not None
    assert view["source_path"] == str(pack / "plugins" / "gh_pr.py")
    failed = registry.catalog().to_dict()["failed"]
    assert [(f["name"], f["source_path"]) for f in failed] == [
        ("gh_pr", str(local_dir / "gh_pr.py"))
    ]


def test_declared_override_between_packs(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    installer.install(str(make_pack(tmp_path, "github", ["gh_pr", "gh_status"])))
    installer.install(
        str(make_pack(tmp_path, "acme", ["gh_pr"], extra_pack='overrides = ["gh_pr"]\n'))
    )
    registry = _registry(pack_home, local_dir)
    assert _row(registry, "gh_pr")["pack"] == "acme@path (user)"
    assert _row(registry, "gh_pr")["overrides"] == "github@path (user)"
    assert _row(registry, "gh_status")["pack"] == "github@path (user)"


def test_replaces_takes_over_the_whole_pack(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    installer.install(str(make_pack(tmp_path, "github", ["gh_pr", "gh_status"])))
    installer.install(
        str(make_pack(tmp_path, "acme", ["gh_pr", "gh_status"], extra_pack='replaces = "github"\n'))
    )
    registry = _registry(pack_home, local_dir)
    for name in ("gh_pr", "gh_status"):
        assert _row(registry, name)["pack"] == "acme@path (user)"


def test_undeclared_collision_in_a_hand_edited_lock_is_reported(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    installer.install(str(make_pack(tmp_path, "first", ["shared"])))
    second = make_pack(tmp_path, "second", ["shared"])
    entry = LockedPack(
        name="second",
        owner="user",
        kind="path",
        location=str(second),
        version="path",
        pack_dir=str(second),
        plugins_dir=str(second / "plugins"),
        plugins=("shared",),
    )
    write_lock(pack_home, [*read_lock(pack_home), entry])
    registry = _registry(pack_home, local_dir)
    assert _row(registry, "shared")["pack"] == "first@path (user)"
    failed = registry.catalog().to_dict()["failed"]
    assert failed[0]["name"] == "shared" and "second" in failed[0]["reason"]


def test_runtime_folder_comes_last(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    runtime = pack_home / "plugins" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "gh_pr.py").write_text(plugin_source("gh_pr", "agent"), encoding="utf-8")
    (runtime / "mine.py").write_text(plugin_source("mine", "agent"), encoding="utf-8")
    installer.install(str(make_pack(tmp_path, "github", ["gh_pr"])))
    registry = _registry(pack_home, local_dir)
    assert _row(registry, "gh_pr")["pack"] == "github@path (user)"
    assert "overrides" not in _row(registry, "gh_pr")
    assert _row(registry, "mine")["owner"] == "local"
    assert str(runtime) in registry.catalog().to_dict()["plugin_search_paths"]


def test_path_install_library_edit_republishes(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    pack = make_pack(tmp_path, "mine", ["my_tool"], library=True)
    (pack / "mylib").mkdir()
    (pack / "mylib" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    installer.install(str(pack))
    registry = _registry(pack_home, local_dir)
    signature = registry._scan_signature_now()
    assert any(entry[0].endswith("mylib/__init__.py") for entry in signature)
    (pack / "mylib" / "__init__.py").write_text("X = 22\n", encoding="utf-8")
    _bump(pack / "mylib" / "__init__.py")
    assert registry._scan_signature_now() != signature  # P-13: no restart needed


def test_adapted_from_on_describe(pack_home: Path, local_dir: Path) -> None:
    (local_dir / "my_pr.py").write_text(
        plugin_source("my_pr", decorator='(adapted_from="gh_pr")'), encoding="utf-8"
    )
    view = _registry(pack_home, local_dir).describe("my_pr")
    assert view is not None and view["adapted_from"] == "gh_pr"


def test_restricted_profile_flags_overridden_allowlisted_names(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    (pack_home / "config.toml").write_text(
        '[profile]\nmode = "restricted"\nallowlist = ["gh_pr", "gh_status"]\n', encoding="utf-8"
    )
    installer.install(str(make_pack(tmp_path, "github", ["gh_pr", "gh_status"])))
    (local_dir / "gh_pr.py").write_text(plugin_source("gh_pr", "local"), encoding="utf-8")
    kernel = create_kernel(home=pack_home, plugin_dirs=[local_dir], skip_recovery=True)
    rows = {row["name"]: row for row in kernel.control.list_plugins()["items"]}
    assert rows["gh_pr"]["override_flagged"] is True
    assert "override_flagged" not in rows["gh_status"]
    log = (pack_home / "service.log").read_text(encoding="utf-8")
    assert "allowlisted plugin 'gh_pr' is served by an override" in log
