"""Agent publishes land in the runtime folder; a pack's (or the operator's) name is refused with
the way forward (P-10); `plugin_not_found` names a bundled pack (design § Agent surface)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.fsutil import sha256_bytes
from trestle.common.types import PublishView, RequestOutcome
from trestle.packs.install import Installer
from trestle.server.main import create_kernel
from trestle.server.snapshots import record_agent_publication

from .conftest import make_pack, plugin_source


@pytest.fixture
def local_dir(tmp_path: Path) -> Path:
    path = tmp_path / "local"
    path.mkdir()
    return path


def _kernel(home: Path, local_dir: Path):  # type: ignore[no-untyped-def]
    return create_kernel(home=home, plugin_dirs=[local_dir], skip_recovery=True)


def test_publish_writes_the_runtime_folder(pack_home: Path, local_dir: Path) -> None:
    kernel = _kernel(pack_home, local_dir)
    result = kernel.control.publish_plugin(plugin_source("mine", "agent"))
    assert isinstance(result, PublishView), result
    assert (pack_home / "plugins" / "runtime" / "mine.py").is_file()
    assert list(local_dir.iterdir()) == []
    view = kernel.control.describe_plugin("mine")
    assert view["source_path"] == str(pack_home / "plugins" / "runtime" / "mine.py")
    assert view["owner"] == "local"


def test_publish_over_a_pack_plugin_redirects(
    installer: Installer, tmp_path: Path, pack_home: Path, local_dir: Path
) -> None:
    pack = make_pack(tmp_path, "github", ["gh_pr"])
    installer.install(str(pack))
    kernel = _kernel(pack_home, local_dir)
    refused = kernel.control.publish_plugin(plugin_source("gh_pr", "agent"))
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.PUBLICATION_NAME_TAKEN
    assert "pack github@path (user)" in refused.message
    assert str(pack / "plugins" / "gh_pr.py") in refused.message  # the source to read (P-7)
    assert "gh_pr_adapted" in refused.message
    assert 'adapted_from="gh_pr"' in refused.message
    assert not (pack_home / "plugins" / "runtime" / "gh_pr.py").exists()
    # the way forward works in one more call
    adapted = plugin_source("gh_pr_adapted", "agent", decorator='(adapted_from="gh_pr")')
    assert isinstance(kernel.control.publish_plugin(adapted), PublishView)
    view = kernel.control.describe_plugin("gh_pr_adapted")
    assert view["adapted_from"] == "gh_pr"
    assert kernel.control.describe_plugin("gh_pr")["pack"] == "github@path (user)"


def test_publish_over_the_operators_file_refused(pack_home: Path, local_dir: Path) -> None:
    (local_dir / "repo_tests.py").write_text(plugin_source("repo_tests", "op"), encoding="utf-8")
    kernel = _kernel(pack_home, local_dir)
    refused = kernel.control.publish_plugin(plugin_source("repo_tests", "agent"))
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.PUBLICATION_NAME_TAKEN
    assert "the operator's file" in refused.message
    assert "op" in (local_dir / "repo_tests.py").read_text()


def test_an_earlier_agent_publish_in_the_operators_folder_moves(
    pack_home: Path, local_dir: Path
) -> None:
    """Before the runtime folder, publishes went to the first watched folder: an agent's own,
    unchanged file there moves to the runtime folder on its next publish."""
    old = plugin_source("mine", "v1")
    (local_dir / "mine.py").write_text(old, encoding="utf-8")
    record_agent_publication(pack_home, "mine", sha256_bytes(old.encode()))
    kernel = _kernel(pack_home, local_dir)
    result = kernel.control.publish_plugin(plugin_source("mine", "v2"))
    assert isinstance(result, PublishView), result
    assert not (local_dir / "mine.py").exists()
    view = kernel.control.describe_plugin("mine")
    assert view["source_path"] == str(pack_home / "plugins" / "runtime" / "mine.py")


def test_plugin_not_found_names_the_bundled_pack(
    installer: Installer,
    bundled: Path,
    pack_home: Path,
    local_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRESTLE_BUNDLED_PACKS", str(bundled))
    make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker")
    kernel = _kernel(pack_home, local_dir)
    refused = kernel.control.run(plugin="docker_stack", args={})
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.PLUGIN_NOT_FOUND
    assert "the operator runs: trestle pack install docker" in refused.message
    other = kernel.control.run(plugin="nothing_like_it", args={})
    assert isinstance(other, RequestOutcome)
    assert other.message == "plugin not found: nothing_like_it"
