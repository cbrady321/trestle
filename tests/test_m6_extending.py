"""M6 extending tests — hot reload, registry_version, promotion budget."""

from __future__ import annotations

import asyncio
import textwrap
import threading
import time
from pathlib import Path

import pytest
from fastmcp import FastMCP

from trestle.server import registry as registry_module
from trestle.server.main import attach_registry_version_mirror, create_kernel
from trestle.server.registry import PromotionBudget, Registry

DROP_IN_PLUGIN = textwrap.dedent(
    """
    from trestle.plugin.surface import Context, trestle


    @trestle
    def drop_in(ctx: Context, value: str = "ok") -> dict[str, str]:
        return {"value": value}
    """
)


def _plugin_names(catalog: dict[str, object]) -> set[str]:
    items = catalog["items"]
    assert isinstance(items, list)
    return {str(row["name"]) for row in items}


def test_list_plugins_hot_reload_on_drop_in(
    trestle_home: Path,
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    echo_src = Path(__file__).resolve().parent / "fixtures" / "plugins" / "echo.py"
    (plugin_dir / "echo.py").write_text(echo_src.read_text(encoding="utf-8"), encoding="utf-8")

    kernel = create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    before = kernel.control.list_plugins()
    version_before = before["registry_version"]
    assert _plugin_names(before) == {"echo"}

    (plugin_dir / "drop_in.py").write_text(DROP_IN_PLUGIN, encoding="utf-8")

    after = kernel.control.list_plugins()
    assert after["registry_version"] > version_before
    assert "drop_in" in _plugin_names(after)


def test_describe_plugin_triggers_hot_reload(trestle_home: Path, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "drop_in.py").write_text(DROP_IN_PLUGIN, encoding="utf-8")
    kernel = create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)

    result = kernel.control.describe_plugin("drop_in")
    assert isinstance(result, dict)
    assert result["name"] == "drop_in"
    assert kernel.registry.get("drop_in") is not None


def test_registry_version_monotonic_without_changes(kernel) -> None:
    first = kernel.control.list_plugins()
    second = kernel.control.list_plugins()
    assert second["registry_version"] == first["registry_version"]


def test_registry_version_increments_when_plugin_removed(kernel, plugin_dir: Path) -> None:
    extra = plugin_dir / "temp_extra.py"
    extra.write_text(
        textwrap.dedent(
            """
            from trestle.plugin.surface import Context, trestle


            @trestle
            def temp_extra(ctx: Context) -> dict[str, str]:
                return {"ok": "yes"}
            """
        ),
        encoding="utf-8",
    )
    with_extra = kernel.control.list_plugins()
    version_with = with_extra["registry_version"]
    assert "temp_extra" in _plugin_names(with_extra)

    extra.unlink()
    without = kernel.control.list_plugins()
    assert without["registry_version"] > version_with
    assert "temp_extra" not in _plugin_names(without)


def test_promotion_budget_limits_concurrent_snapshot_promotions(
    trestle_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    monkeypatch.setenv("TRESTLE_PROMOTION_BUDGET", "1")
    for index in range(3):
        (plugin_dir / f"budget_{index}.py").write_text(
            textwrap.dedent(
                f"""
                from trestle.plugin.surface import Context, trestle


                @trestle
                def budget_{index}(ctx: Context) -> dict[str, int]:
                    return {{"n": {index}}}
                """
            ),
            encoding="utf-8",
        )

    active = 0
    peak = 0
    lock = threading.Lock()
    original = registry_module.materialize_snapshot

    def slow_materialize(*args: object, **kwargs: object):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)
            return original(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(registry_module, "materialize_snapshot", slow_materialize)

    registry = Registry(
        home=trestle_home,
        plugin_dirs=[plugin_dir],
        promotion_budget=PromotionBudget(max_concurrent=1),
    )
    registry.refresh()

    assert peak == 1
    assert len(registry.snapshots) == 3


def test_promotion_budget_peak_tracks_registry(trestle_home: Path, plugin_dir: Path) -> None:
    budget = PromotionBudget(max_concurrent=2)
    budget.reset_peak()
    registry = Registry(home=trestle_home, plugin_dirs=[plugin_dir], promotion_budget=budget)
    registry.refresh()
    assert budget.peak_active() <= 2


def test_tools_list_registry_version_mirror(kernel) -> None:
    async def _run() -> None:
        mcp = FastMCP("trestle-test")
        attach_registry_version_mirror(mcp, kernel)

        @mcp.tool
        def echo_probe(x: str) -> str:
            return x

        catalog = kernel.control.list_plugins()
        result = await mcp._list_tools_mcp(None)
        assert result.meta is not None
        assert result.meta["registry_version"] == catalog["registry_version"]

    asyncio.run(_run())
