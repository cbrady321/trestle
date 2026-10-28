"""L.RB-6.4: the reference workflow's environment key is the Compose project, and the lease
serializes by it (WR-OWN-8 B half; MC-16, MC-19, MC-30). PROC, CI: no Docker.

The plugin is the unmodified `reference_env` on the fake binding (`twin.lease_binding`, whose
container creation takes wall time). The plugin declares `env_arg="env"` and its tree declares
`env_key_field = env`, so a request's `env` is the environment key, compared as opaque bytes. The
falsifier is SL-8's: read each run's lane (the proof court's oracle), a run's mutation interval
runs from its first APPLIED claim to its last release, and two runs of one environment must never
hold overlapping intervals while two runs of different environments do. The waiter is a copy of
the plugin with a later declared deadline (admission refuses a same-deadline request behind a
holder whose release walk it could not outlast, `admission.environment_busy`, L.SL-8.2).

Strength as registered by SL-8: OQ-29 (does the exclusion reach a root that declines to declare its
environment) stays open, both variants written by SL-8 and inherited here, never counted as a
pass; the reference workflow declares its environment, so the delivered exclusion applies to it."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from tests.core.spine import support as spine
from tests.proof import harness, records, tolerances
from trestle.common.types import RunView
from trestle.server.main import Kernel
from twin import harness as twin_harness
from twin import lease_binding

from trestle_env import schema, tree
from trestle_env.plugins import reference_env

PLUGIN = Path(reference_env.__file__)
LATE_DEADLINE_S = 600  # the waiter's: it needs the holder's release walk to fit before its own
RUN_BOUND_S = tolerances.JOIN_WAIT_S * 6
LABEL = "WR-OWN-8:b-env-key-compose-project"

Interval = tuple[datetime, datetime]


@pytest.fixture(autouse=True)
def fake_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plugin processes bind the slow fake engine and see this checkout's packages."""
    monkeypatch.setenv("TRESTLE_ENV_PORTS", lease_binding.SEAM)
    monkeypatch.setenv("PYTHONPATH", twin_harness.plugin_pythonpath())
    monkeypatch.delenv("TRESTLE_ENV_FAKE_STATE", raising=False)


def _late_source() -> str:
    source = PLUGIN.read_text(encoding="utf-8")
    declared = f"deadline={tree.DEADLINE_S}"
    assert source.count(declared) == 1 and source.count("def reference_env(") == 1
    late = source.replace(declared, f"deadline={LATE_DEADLINE_S}")
    return late.replace("def reference_env(", "def reference_env_late(")


def _kernel(tmp_path: Path) -> Kernel:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / PLUGIN.name).write_text(PLUGIN.read_text(encoding="utf-8"), encoding="utf-8")
    (plugins / "reference_env_late.py").write_text(_late_source(), encoding="utf-8")
    return harness.fresh_kernel(plugin_dirs=[plugins])


def _start(kernel: Kernel, plugin: str, env: str) -> Path:
    view = kernel.control.run(plugin, {"env": env}, wait_ms=0)
    assert isinstance(view, RunView), view
    return spine.run_dir_of(kernel, view.run_id)


def _terminal(run_dir: Path) -> str | None:
    return records.node_record(run_dir).terminal


def _two_runs(kernel: Kernel, first_env: str, second_env: str) -> tuple[Path, Path]:
    """Start two runs back to back (the holder, then the waiter) and wait for both to end."""
    one = _start(kernel, "reference_env", first_env)
    try:
        two = _start(kernel, "reference_env_late", second_env)
    except BaseException:
        spine.ancestry.reap(spine.marked(one.name))
        raise
    with spine.reaping(one.name), spine.reaping(two.name):
        assert spine.wait_until(lambda: all(_terminal(d) for d in (one, two)), RUN_BOUND_S), [
            spine.kinds(d) for d in (one, two)
        ]
    assert [_terminal(d) for d in (one, two)] == ["succeeded", "succeeded"]
    return one, two


def _at(text: object) -> datetime:
    assert isinstance(text, str), text
    return datetime.fromisoformat(text)


def interval(run_dir: Path) -> Interval:
    """The run's mutation interval, read from the lane alone (a claim is stamped `issued_at`, a
    release `released_at`)."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    issued = {
        (r.entry["effect"], r.entry["attempt"]): r.entry for r in lane.rows if r.cls == "issue"
    }
    applied = [
        _at(issued[(r.entry["effect"], r.entry["attempt"])]["issued_at"])
        for r in lane.rows
        if r.cls == "confirmation" and r.entry["status"] == "applied"
    ]
    released = [_at(r.entry["released_at"]) for r in lane.rows if r.cls == "released"]
    assert applied, "the run applied no effect: there is nothing to compare"
    return min(applied), max([*applied, *released])


def overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]


@pytest.mark.proves("WR-OWN-8", LABEL, "B", "B", "PROC", "CI")
def test_two_roots_same_project_never_overlap_mutations(tmp_path: Path) -> None:
    assert tree.ENTRY.units[tree.ROOT_UNIT].env_key_field == schema.ENV_ARG  # type: ignore[union-attr]
    kernel = _kernel(tmp_path)
    first, second = _two_runs(kernel, "compose-a", "compose-a")
    one, two = interval(first), interval(second)
    # the environment key the runs took is the request's `env`, the Compose project, as bytes
    for run_dir in (first, second):
        created = records.ledger_rows(run_dir).rows[0]
        assert "compose-a" in str(created["lease_key"])
    # the waiter's first mutation follows the holder's release: the intervals never overlap
    assert one[1] <= two[0], (one, two)
    assert not overlaps(one, two)
    assert (
        one[1] - one[0]
    ).total_seconds() >= lease_binding.HOLD_S  # the hold made a real interval
    kinds = spine.kinds(second)
    assert kinds.index("created") < kinds.index("started")  # admitted first, started after


@pytest.mark.proves("WR-OWN-8", LABEL, "B", "B", "PROC", "CI")
def test_different_projects_run_concurrently(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    first, second = _two_runs(kernel, "compose-east", "compose-west")
    # the falsifier has teeth: the same fake and plugin hold overlapping intervals when the
    # projects differ, and their lease keys differ
    assert overlaps(interval(first), interval(second))
    keys = {str(records.ledger_rows(d).rows[0]["lease_key"]) for d in (first, second)}
    assert len(keys) == 2
