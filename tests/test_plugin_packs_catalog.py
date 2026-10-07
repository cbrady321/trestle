"""Tests for pack plugin catalog validation."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from trestle.server.main import create_kernel
from trestle.server.plugin_paths import CATALOG_HINT_PACKS_MISSING


@pytest.fixture
def packs_plugin_dir(tmp_path: Path) -> Path:
    repo = Path(__file__).resolve().parents[1]
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    for path in (repo / "examples" / "packs").glob("*.py"):
        shutil.copy(path, packs_dir / path.name)
    return packs_dir


def test_pack_plugins_valid_when_trestle_packs_installed(
    tmp_path: Path,
    packs_plugin_dir: Path,
) -> None:
    home = tmp_path / "trestle"
    kernel = create_kernel(home=home, plugin_dirs=[packs_plugin_dir], skip_recovery=True)
    catalog = kernel.registry.catalog().to_dict()
    assert catalog["items"]
    assert all(row["valid"] for row in catalog["items"])
    assert catalog.get("catalog_hint") != CATALOG_HINT_PACKS_MISSING
    names = {row["name"] for row in catalog["items"]}
    fields = {
        "docker_stack": "spec",
        "integration_pipeline": "stack_spec",
        "pytest_run": "path",
        "migrate_apply": "backend",
    }
    for name, field in fields.items():
        assert name in names
        described = kernel.control.describe_plugin(name)
        assert isinstance(described, dict)
        assert field in described["input_schema"]["properties"]
        assert isinstance(described.get("return_schema"), dict)
        assert "type" in described["return_schema"] or "anyOf" in described["return_schema"]
    for row in catalog["items"]:
        assert "input_schema" not in row
        assert "return_schema" not in row


def test_pack_plugins_hint_when_trestle_packs_missing(
    tmp_path: Path,
    packs_plugin_dir: Path,
) -> None:
    home = tmp_path / "trestle"
    kernel = create_kernel(home=home, plugin_dirs=[packs_plugin_dir], skip_recovery=True)
    with patch(
        "trestle.server.plugin_validate.packs_import_error",
        return_value="No module named 'trestle_packs'",
    ):
        catalog = kernel.registry.catalog().to_dict()
    assert catalog["catalog_hint"] == CATALOG_HINT_PACKS_MISSING
    assert any(not row["valid"] for row in catalog["items"])


def test_pack_plugins_import_failed_at_admission(
    tmp_path: Path,
    packs_plugin_dir: Path,
) -> None:
    from trestle.common import codes
    from trestle.common.types import RequestOutcome

    home = tmp_path / "trestle"
    kernel = create_kernel(home=home, plugin_dirs=[packs_plugin_dir], skip_recovery=True)
    with patch(
        "trestle.server.plugin_validate.packs_import_error",
        return_value="No module named 'trestle_packs'",
    ):
        outcome = kernel.control.run(plugin="pytest_run", args={"path": "."}, wait_ms=0)
    assert isinstance(outcome, RequestOutcome)
    assert outcome.code == codes.IMPORT_FAILED
    assert "trestle-packs" in outcome.message
