"""Tests for pack plugin import validation and throwaway subprocess (R-PLUG-16)."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.types import PublishView, RequestOutcome
from trestle.server.main import create_kernel
from trestle.server.plugin_validate import (
    PluginValidationError,
    plugin_imports_packs,
    validate_plugin,
    validate_plugin_imports,
)
from trestle.server.snapshots import materialize_snapshot

REPO = Path(__file__).resolve().parents[1]
ECHO = REPO / "examples" / "plugins" / "echo.py"


def _plugin(body: str) -> str:
    return (
        "from __future__ import annotations\n\n"
        "from trestle.plugin.surface import Context, trestle\n\n"
        + textwrap.dedent(body).strip()
        + "\n"
    )


def test_plugin_imports_packs_detects_from_import(tmp_path: Path) -> None:
    path = tmp_path / "plugin.py"
    path.write_text(
        "from trestle_packs.docker.runner import StackRunner\n",
        encoding="utf-8",
    )
    assert plugin_imports_packs(path) is True


def test_plugin_imports_packs_ignores_echo(tmp_path: Path) -> None:
    path = tmp_path / "plugin.py"
    path.write_text(
        "from trestle.plugin.surface import Context, trestle\n",
        encoding="utf-8",
    )
    assert plugin_imports_packs(path) is False


def test_validate_plugin_imports_none_for_echo(tmp_path: Path) -> None:
    path = tmp_path / "plugin.py"
    path.write_text("import json\n", encoding="utf-8")
    assert validate_plugin_imports(path) is None


def test_validate_plugin_accepts_echo() -> None:
    assert validate_plugin(ECHO) is None


def test_validate_plugin_rejects_kernel_import(tmp_path: Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text(
        _plugin(
            """
            import trestle.server.registry  # noqa: F401

            @trestle
            def bad(ctx: Context) -> dict[str, str]:
                return {"ok": "no"}
            """
        ),
        encoding="utf-8",
    )
    error = validate_plugin(path)
    assert error is not None
    assert "trestle.server" in error
    assert "R-PLUG-6" in error


def test_validate_plugin_rejects_missing_import(tmp_path: Path) -> None:
    path = tmp_path / "missing.py"
    path.write_text(
        _plugin(
            """
            import definitely_not_a_trestle_module  # noqa: F401

            @trestle
            def missing(ctx: Context) -> dict[str, str]:
                return {"ok": "no"}
            """
        ),
        encoding="utf-8",
    )
    error = validate_plugin(path)
    assert error is not None
    assert "definitely_not_a_trestle_module" in error


def test_materialize_snapshot_requires_validation(tmp_path: Path) -> None:
    src = tmp_path / "bad.py"
    src.write_text(
        _plugin(
            """
            from trestle.wrapper.spawn import spawn_child  # noqa: F401

            @trestle
            def bad(ctx: Context) -> dict[str, str]:
                return {"ok": "no"}
            """
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    with pytest.raises(PluginValidationError):
        materialize_snapshot(src, "bad", home=home)
    snap_root = home / "snapshots"
    assert not snap_root.exists() or not any(snap_root.iterdir())


def test_publish_rejects_kernel_import_keeps_previous(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = create_kernel(home=tmp_path / "trestle", plugin_dirs=[plugin_dir], skip_recovery=True)
    good = _plugin(
        """
        @trestle
        def greeter(ctx: Context, message: str = "hi") -> dict[str, str]:
            return {"message": message}
        """
    )
    first = kernel.control.publish_plugin(good)
    assert isinstance(first, PublishView)
    bad = _plugin(
        """
        import fastmcp  # noqa: F401

        @trestle
        def greeter(ctx: Context, message: str = "hi") -> dict[str, str]:
            return {"message": message}
        """
    )
    refused = kernel.control.publish_plugin(bad)
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.PUBLICATION_VALIDATION_FAILED
    desc = kernel.control.describe_plugin("greeter")
    assert isinstance(desc, dict)
    assert desc["source_sha256"] == first.source_sha256


def test_validate_child_does_not_import_kernel() -> None:
    source = (REPO / "trestle" / "child" / "validate.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = (
        "trestle.server",
        "trestle.wrapper",
        "fastmcp",
        "mcp",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(
                    alias.name == prefix or alias.name.startswith(f"{prefix}.")
                    for prefix in forbidden
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not any(
                node.module == prefix or node.module.startswith(f"{prefix}.")
                for prefix in forbidden
            )


def test_validate_plugin_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trestle.server.plugin_validate as pv

    monkeypatch.setattr(pv, "VALIDATE_TIMEOUT_S", 0.3)
    path = tmp_path / "hang.py"
    path.write_text(
        _plugin(
            """
            import time

            time.sleep(30)

            @trestle
            def hang(ctx: Context) -> dict[str, str]:
                return {"ok": "no"}
            """
        ),
        encoding="utf-8",
    )
    error = validate_plugin(path)
    assert error is not None
    assert "timed out" in error
