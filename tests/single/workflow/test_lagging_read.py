"""L.SL-5.3: a lagging read never re-submits a step that must not be re-submitted, and an accepted
claim is not reported as completion (WR-IDEM-5 `A-lagging-read-no-resubmit`; B1-E2, V-3.1 J-16,
J-17, J-17a, J-18..J-20).

The unit is the published fixture `tests/fixtures/workflows/lagging_leaf.py` over the fake marker
under the loop tests' manual clock (`loopkit`). Two lags, both on `trestle_packs.fakes.FakeMarker`:
`lag` (`lag_polls=k`: the create is confirmed and visible, the readiness check says "not yet" k
times) and `hide` (the create answers UNKNOWN and the next k readbacks show nothing). k is 1, 3 and
"beyond": more than the polls the wait policy allows before `max_wait` elapses."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.single.workflow import loopkit as kit
from tests.single.workflow.loopkit import Rig
from trestle.workflow import codes, ports
from trestle.workflow.declarations import Repeat

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "workflows" / "lagging_leaf.py"
MAX_WAIT_S = 5.0  # a flat 1 s poll: about five polls fit before max_wait elapses
BEYOND = 60  # more lag than the wait policy allows polls for
LAGS = [1, 3, BEYOND]


def _fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lagging_leaf_fixture", FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _rig(
    tmp_path: Path, repeat: Repeat, *, lag: int = 0, hide: int = 0, max_attempts: int = 3
) -> tuple[Rig, Any, Any]:
    fixture = _fixture()
    unit = fixture.make_unit(
        fixture.declaration(
            repeat, max_attempts=max_attempts, max_wait_s=MAX_WAIT_S, env_key_field=None
        )
    )
    marker = fixture.LaggingMarker(tmp_path / "markers", "run", lag_polls=lag, hide=hide)
    rig = kit.build(
        tmp_path,
        unit,
        ports={
            ports.ResourceReads: marker,
            ports.ResourceCreate: marker,
            ports.ResourceOwned: marker,
        },
    )
    return rig, unit, marker


def _claims(rig: Rig) -> int:
    """Claims (tickets) for the create effect; the release pass's stop ticket is not one."""
    return sum(1 for t in rig.rows("issue") if t["effect"] == "up")


@pytest.mark.proves(
    "WR-IDEM-5", "WR-IDEM-5:A-lagging-read-no-resubmit", "A", "single", "LOGIC", "CI"
)
@pytest.mark.parametrize("lag", LAGS)
@pytest.mark.parametrize("mode", ["lag", "hide"])
def test_lagging_read_no_second_claim_once(tmp_path: Path, mode: str, lag: int) -> None:
    """A ONCE step is claimed exactly once however long the read lags: while the read has not
    caught up the loop only polls, and when `max_wait` elapses it stops the node instead of going
    again (a hidden resource ends BLOCKED `effect_unconfirmed`, a visible but unready one FAILED
    `postcondition_timeout`)."""
    rig, unit, marker = _rig(tmp_path, Repeat.ONCE, **{mode: lag})
    rig.run()
    assert _claims(rig) == 1, "one claim for the ONCE effect"
    assert marker.creates == 1 and unit.advances == 1, "the port was asked to create once"
    assert rig.cancel.waits, "the lag was waited out by polling"
    (end,) = rig.ends()
    if lag == BEYOND:
        assert end["condition"] != "satisfied"
        want = codes.EFFECT_UNCONFIRMED if mode == "hide" else codes.POSTCONDITION_TIMEOUT
        assert end["code"] == want
    else:
        assert (end["condition"], end["code"]) == ("satisfied", None)


@pytest.mark.parametrize("lag", LAGS)
@pytest.mark.parametrize("mode", ["lag", "hide"])
def test_acceptance_not_reported_completion(tmp_path: Path, mode: str, lag: int) -> None:
    """The create was accepted (a ticket, a confirmation) before the read shows the postcondition:
    the node is not satisfied until the read observes it, and never reports completion on the
    acceptance alone."""
    rig, unit, _ = _rig(tmp_path, Repeat.SAFE, **{mode: lag}, max_attempts=1)
    rig.run()
    (end,) = rig.ends()
    assert rig.rows("confirmation"), "the claim was accepted"
    if lag == BEYOND:
        assert end["condition"] != "satisfied", "acceptance alone is not completion"
        assert end["cut"] is None
    else:
        assert end["condition"] == "satisfied"
        # satisfied only after the readback showed it: the run polled at least `lag` times
        assert len(rig.cancel.waits) >= lag, "one poll per lagging read before it was observed"
        assert unit.observes >= 1 + lag


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_resubmittable_step_may_resubmit_within_attempts(tmp_path: Path, attempts: int) -> None:
    """A RESUBMITTABLE (SAFE) step whose claim was accepted but never became visible may go again
    when `max_wait` elapses (J-17), and never more than `max_attempts` times."""
    rig, unit, marker = _rig(tmp_path, Repeat.SAFE, hide=BEYOND, max_attempts=attempts)
    rig.run()
    assert _claims(rig) == attempts and marker.creates == attempts
    assert [t["attempt"] for t in rig.rows("issue") if t["effect"] == "up"] == list(
        range(1, attempts + 1)
    )
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", codes.EFFECT_UNCONFIRMED)


def test_resubmittable_step_lag_shorter_than_wait_needs_one_claim(tmp_path: Path) -> None:
    """A read that catches up inside the wait needs no second claim even for a SAFE step."""
    rig, unit, marker = _rig(tmp_path, Repeat.SAFE, hide=2, max_attempts=3)
    rig.run()
    assert _claims(rig) == 1 and marker.creates == 1
    assert rig.ends()[0]["condition"] == "satisfied"
