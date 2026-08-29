"""Pytest runner plugin — structured test results as artifacts."""

from __future__ import annotations

from pathlib import Path

from trestle_packs.pytest.runner import PytestSpec, run_pytest

from trestle.plugin.surface import Context, trestle


@trestle
def pytest_run(
    ctx: Context,
    path: str = ".",
    markers: str | None = None,
    keyword: str | None = None,
) -> dict[str, object]:
    """Run pytest in-process and attach a JSON report artifact."""
    ctx.log(f"pytest: collecting from {path}")
    spec = PytestSpec(path=path, markers=markers, keyword=keyword)
    result = run_pytest(spec, report_dir=ctx.outputs)
    if result.report_path:
        ctx.attach(Path(result.report_path), name="pytest-report.json")
    summary = result.to_dict()
    ctx.log(
        f"pytest: collected={result.collected} passed={result.passed} failed={result.failed}"
    )
    if result.exit_code != 0:
        msg = f"pytest failed with exit code {result.exit_code}"
        raise RuntimeError(msg)
    return summary
