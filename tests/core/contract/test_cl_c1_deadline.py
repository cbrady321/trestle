"""L.CL-C1.5: the declared deadline is minted at admission and enforced; the ceiling is published
(A5.2, WR-DEADLINE-1). The over-300 s test carries marker `long`: it is deselected from the default
session and runs in CI job `long-gate` (gate ci-long)."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.proof import harness, tolerances
from trestle.common import clock, codes
from trestle.common.types import AdmitRequest, PublishView, RequestOutcome, RunView
from trestle.server.ledger import evidence_dir, run_dir_for

# WR-COMPAT-10: an undeclared plugin keeps the 300 s deadline.
DEFAULT_DEADLINE_S = 300
# The long test: a plugin declares a deadline past the default and outlives it.
LONG_DECLARED_DEADLINE_S = 310
LONG_SLEEP_S = 305
LONG_WAIT_MS = 330_000
# The fast twin scales the same shape down: the snapshot default is patched to a short value,
# the plugin declares a longer one and sleeps between the two.
SCALED_DEFAULT_S = 1
SCALED_DECLARED_DEADLINE_S = 20
SCALED_SLEEP_S = 2.5
SCALED_WAIT_MS = 15_000
CLOCK_SLACK = timedelta(seconds=5)

SLEEPER = """\
import time

from trestle.plugin.surface import Context, trestle


@trestle(deadline={deadline})
def sleeper(ctx: Context) -> dict[str, bool]:
    end = time.monotonic() + {sleep}
    while time.monotonic() < end:
        time.sleep(0.05)
    return {{"done": True}}
"""

UNDECLARED_SLEEPER = """\
import time

from trestle.plugin.surface import Context, trestle


@trestle
def undeclared_sleeper(ctx: Context) -> dict[str, bool]:
    end = time.monotonic() + {sleep}
    while time.monotonic() < end:
        time.sleep(0.05)
    return {{"done": True}}
"""

DECLARING = """\
from trestle.plugin.surface import Context, trestle


@trestle(deadline={deadline})
def declaring(ctx: Context) -> dict[str, int]:
    return {{"n": 1}}
"""


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    return harness.fresh_kernel([plugin_dir], home=tmp_path / "home")


def _publish(kernel, source: str) -> None:  # type: ignore[no-untyped-def]
    published = kernel.control.publish_plugin(source)
    assert isinstance(published, PublishView), published


def _spec(kernel, run_id: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    path = evidence_dir(run_dir_for(kernel.home, run_id)) / "spec.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _admit_deadline(kernel, plugin: str) -> tuple[datetime, datetime, dict[str, object]]:  # type: ignore[no-untyped-def]
    """Admission alone: the bracket of wall-clock moments around it and the minted spec."""
    before = datetime.now(tz=UTC)
    result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args={}))
    after = datetime.now(tz=UTC)
    assert result.tag == "admitted", result
    return before, after, _spec(kernel, result.run_id)


@pytest.mark.long
@pytest.mark.proves("A5.2", "A5.2", "A", "core", "PROC", "CI")
@pytest.mark.proves(
    "WR-DEADLINE-1",
    "WR-DEADLINE-1:declared-over-300-not-killed",
    "core",
    "core",
    "PROC",
    "CI",
)
def test_declared_310s_plugin_not_killed_at_300(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    _publish(kernel, SLEEPER.format(deadline=LONG_DECLARED_DEADLINE_S, sleep=LONG_SLEEP_S))
    view = kernel.control.run(plugin="sleeper", wait_ms=LONG_WAIT_MS)
    assert isinstance(view, RunView), view
    assert view.state == "succeeded", view
    assert view.summary == {"done": True}


def test_scaled_declared_deadline_outlives_default(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    _publish(kernel, SLEEPER.format(deadline=SCALED_DECLARED_DEADLINE_S, sleep=SCALED_SLEEP_S))
    _publish(kernel, UNDECLARED_SLEEPER.format(sleep=SCALED_SLEEP_S))

    with harness.patch_snapshot(kernel, "sleeper", timeout_s=SCALED_DEFAULT_S):
        view = kernel.control.run(plugin="sleeper", wait_ms=SCALED_WAIT_MS)
    assert isinstance(view, RunView), view
    assert view.state == "succeeded", view

    # control: the same sleep under the same (patched) default and no declaration is stopped
    with harness.patch_snapshot(kernel, "undeclared_sleeper", timeout_s=SCALED_DEFAULT_S):
        stopped = kernel.control.run(plugin="undeclared_sleeper", wait_ms=SCALED_WAIT_MS)
    assert isinstance(stopped, RunView), stopped
    assert stopped.state == "timed_out", stopped


@pytest.mark.proves(
    "WR-DEADLINE-1", "WR-DEADLINE-1:undeclared-default-300", "core", "core", "PROC", "CI"
)
@pytest.mark.proves(
    "WR-DEADLINE-1",
    "WR-DEADLINE-1:visible-describe-provenance",
    "core",
    "core",
    "PROC",
    "CI",
)
def test_undeclared_default_300_and_describe_shows_duration(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    _publish(kernel, UNDECLARED_SLEEPER.format(sleep=SCALED_SLEEP_S))
    _publish(kernel, SLEEPER.format(deadline=LONG_DECLARED_DEADLINE_S, sleep=SCALED_SLEEP_S))

    # undeclared: the minted deadline is 300 s after admission, and describe says so
    before, after, spec = _admit_deadline(kernel, "undeclared_sleeper")
    minted = datetime.fromisoformat(str(spec["deadline"]))
    assert before + timedelta(seconds=DEFAULT_DEADLINE_S) <= minted
    assert minted <= after + timedelta(seconds=DEFAULT_DEADLINE_S) + CLOCK_SLACK
    assert spec["timeout_s"] == DEFAULT_DEADLINE_S
    described = kernel.control.describe_plugin("undeclared_sleeper")
    assert isinstance(described, dict), described
    assert described["deadline_s"] == DEFAULT_DEADLINE_S
    assert described["deadline_source"] == "default"
    assert described["timeout_s"] == DEFAULT_DEADLINE_S

    # declared: the minted deadline is the declared one, and describe shows it as declared
    before, after, spec = _admit_deadline(kernel, "sleeper")
    minted = datetime.fromisoformat(str(spec["deadline"]))
    declared = timedelta(seconds=LONG_DECLARED_DEADLINE_S)
    assert before + declared <= minted <= after + declared + CLOCK_SLACK
    assert spec["timeout_s"] == LONG_DECLARED_DEADLINE_S
    described = kernel.control.describe_plugin("sleeper")
    assert isinstance(described, dict), described
    assert described["deadline_s"] == LONG_DECLARED_DEADLINE_S
    assert described["deadline_source"] == "declared"
    assert described["timeout_s"] == LONG_DECLARED_DEADLINE_S


def test_deadline_above_ceiling_refused_before_any_run_id(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    ceiling = math.floor(clock.deadline_ceiling)
    _publish(kernel, DECLARING.format(deadline=ceiling + 1))

    result = kernel.control.admission.admit(AdmitRequest(plugin="declaring", args={}))
    assert result.tag == "refused", result
    assert result.outcome.code == codes.BUDGET_DOES_NOT_FIT
    assert result.outcome.origin == "admission"
    assert not result.outcome.retryable
    assert codes.BUDGET_DOES_NOT_FIT == "admission.budget_does_not_fit"
    # nothing was minted: no run directory exists
    assert list(kernel.home.rglob("spec.json")) == []
    # through the call surface it is the same refusal
    refused = kernel.control.run(plugin="declaring", wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(refused, RequestOutcome), refused
    assert refused.code == codes.BUDGET_DOES_NOT_FIT

    # exactly at the ceiling is admitted
    _publish(kernel, DECLARING.replace("declaring", "at_ceiling").format(deadline=ceiling))
    ok = kernel.control.admission.admit(AdmitRequest(plugin="at_ceiling", args={}))
    assert ok.tag == "admitted", ok


def test_ceiling_is_published_on_clock_and_read_through_tolerances() -> None:
    assert clock.deadline_ceiling > DEFAULT_DEADLINE_S
    assert tolerances.deadline_ceiling() == float(clock.deadline_ceiling)
