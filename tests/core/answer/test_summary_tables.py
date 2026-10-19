"""T6: a `summary_fields` entry `name[col,...]` naming a list-of-objects field shows it in the
run summary as a compact table, one row per object, within the run's summary budget."""

from __future__ import annotations

import pytest

from tests.proof import harness, tolerances
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.plugin_schema import DeclarationError, parse_summary_field

PR_BATCH = """\
from dataclasses import dataclass

from trestle.plugin.surface import Context, trestle


@dataclass
class Target:
    id: str
    pr_url: str
    head_ref: str
    checks: str
    verdict: str
    log: str


@dataclass
class Batch:
    aaa: str
    survey: str
    targets: list[Target]


@trestle(summary_fields=["survey", "targets[id,pr_url,checks,verdict]"])
def pr_batch(ctx: Context, count: int = 6) -> Batch:
    targets = [
        Target(
            id=f"t{n}",
            pr_url=f"https://example.test/pr/{n}",
            head_ref="feature/" + "x" * 300,
            checks="passing" if n % 2 else "failing",
            verdict="ok" if n % 2 else "blocked",
            log="l" * 2000,
        )
        for n in range(count)
    ]
    return Batch(aaa="a" * 6000, survey="partial", targets=targets)
"""


def _kernel(tmp_path):  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    assert isinstance(kernel.control.publish_plugin(PR_BATCH), PublishView)
    return kernel


def _run(kernel, **args: object) -> RunView:  # type: ignore[no-untyped-def]
    view = kernel.control.run(plugin="pr_batch", args=args, wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(view, RunView) and view.state == "succeeded", view
    return view


def test_a_declared_table_shows_one_row_per_object(tmp_path) -> None:  # type: ignore[no-untyped-def]
    view = _run(_kernel(tmp_path))
    assert view.truncated is True
    assert isinstance(view.summary, dict)
    assert view.summary["survey"] == "partial"
    table = view.summary["targets"]
    assert table["columns"] == ["id", "pr_url", "checks", "verdict"]
    assert table["row_count"] == 6
    assert table["rows"][1] == ["t1", "https://example.test/pr/1", "passing", "ok"]
    assert len(table["rows"]) == 6
    assert "targets" not in (view.omitted or [])
    assert "aaa" in (view.omitted or [])
    assert not [m for m in view.limits_exceeded or [] if m.get("field") == "targets"]


def test_rows_past_the_budget_are_left_out_and_marked(tmp_path) -> None:  # type: ignore[no-untyped-def]
    view = _run(_kernel(tmp_path), count=200)
    table = view.summary["targets"]
    assert table["row_count"] == 200
    assert 0 < len(table["rows"]) < 200
    assert table["rows"][0][0] == "t0"
    (marker,) = [m for m in view.limits_exceeded or [] if m.get("field") == "targets"]
    assert marker["reason"] == "rows_truncated"


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("status", ("status", None)),
        ("targets[id,pr_url]", ("targets", ("id", "pr_url"))),
        ("targets[ id , verdict ]", ("targets", ("id", "verdict"))),
    ],
)
def test_summary_field_entries_parse(entry: str, expected: object) -> None:
    assert parse_summary_field(entry) == expected


@pytest.mark.parametrize("entry", ["[id]", "targets[]", "targets[id,,x]", "t[a,a]", "t]", "t[a"])
def test_malformed_table_entries_are_refused(entry: str) -> None:
    with pytest.raises(DeclarationError):
        parse_summary_field(entry)


def test_a_field_named_twice_is_refused_at_publication(tmp_path) -> None:  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    source = PR_BATCH.replace('["survey", ', '["targets", ').replace("def pr_batch", "def twice")
    refused = kernel.control.publish_plugin(source)
    assert isinstance(refused, RequestOutcome), refused
    assert "summary_fields" in refused.message
