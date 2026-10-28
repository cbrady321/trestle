"""L.RB-6.1: the provisioning family's conformance suite, run UNMODIFIED against the stdlib fake and
the real `ProvisionPort` (WR-PROOF-4:b-provision-suite, WR-ENV-4:authoritative-probe-read-only,
SA-14). One suite file (`tests/conformance/provision_cases.py`), registered through
`register_family`, run by `run_family`. Three bindings:

- `fake` (CI): the stdlib `FakeProvision`;
- `real-shim` (CI): the real adapter over the real `CommandPort` and the `psql_double` launcher, no
  engine, nothing started: `docker exec ... psql` answered by sqlite with a password check;
- `real` (`docker_host`, the host-docker gate): the real adapter over the operator's docker and a
  real Postgres container of the MC-B-10 image the gate exports as `TRESTLE_IMAGE_POSTGRES`.

Planted defects prove the suite is not vacuous.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conformance import provision_cases
from tests.proof.suites.ports import core

from trestle_packs.fakes.command import ConfirmationStatus, durable
from trestle_packs.fakes.provision import FakeProvision

from . import rig

BINDINGS = [
    pytest.param(
        "fake",
        id="fake",
        marks=[
            pytest.mark.proves(
                "WR-PROOF-4", "WR-PROOF-4:b-provision-suite", "B", "B", "LOGIC", "CI"
            ),
            pytest.mark.proves(
                "WR-ENV-4", "WR-ENV-4:authoritative-probe-read-only", "B", "B", "LOGIC", "CI"
            ),
            # the fake twins of the two HOST labels below
            pytest.mark.stub_proven("WR-PROOF-4:b-provision-suite@host@stub-twin"),
            pytest.mark.stub_proven("WR-ENV-4:authoritative-probe-read-only@host@stub-twin"),
        ],
    ),
    pytest.param("real-shim", id="real-shim"),
    pytest.param(
        "real",
        id="real",
        marks=[
            pytest.mark.docker_host,
            pytest.mark.proves(
                "WR-PROOF-4", "WR-PROOF-4:b-provision-suite@host", "B", "B", "DOCKER", "HOST"
            ),
            pytest.mark.proves(
                "WR-ENV-4",
                "WR-ENV-4:authoritative-probe-read-only@host",
                "B",
                "B",
                "DOCKER",
                "HOST",
            ),
        ],
    ),
]


def factory(binding: str, base: Path) -> Callable[[], core.Implementation]:
    if binding == "fake":
        return rig.fake_provision()
    if binding == "real-shim":
        return rig.real_shim_provision(base)
    return rig.real_docker_provision(base)


@pytest.mark.parametrize("binding", BINDINGS)
def test_provision_suite(binding: str, tmp_path: Path) -> None:
    build = factory(binding, tmp_path)
    try:
        run = core.run_family(provision_cases.FAMILY, build)
    finally:
        cleanup = getattr(build, "cleanup", None)  # the real binding's one Postgres container
        if cleanup is not None:
            cleanup()
    assert run.cases_run == tuple(c.name for c in provision_cases.CASES)
    assert run.suite_sha256 == core.sha256_of(Path(provision_cases.__file__))


# planted defects: each fake has one, and the suite (unmodified) names the case that catches it


class _RunFormAccepted(FakeProvision):
    def release_descriptor(self, call: Any) -> Any:
        return durable("environment")  # a RUN call is answered, not refused


class _CreateNotIdempotent(FakeProvision):
    def create(self, spec: Any, ticket: Any) -> Any:
        confirmation = super().create(spec, ticket)
        if ticket.attempt > 1:
            self.plant_found(spec.logical_system)  # a second record on the second attempt
        return confirmation


class _FoundIsPresent(FakeProvision):
    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        seen = super().observe(spec, lineage, effect)
        if seen.found and not seen.selector_present:
            return type(seen)(True, seen.selector_ref, True, True, (), (), None)  # occupancy
        return seen


class _DownReadsAbsent(FakeProvision):
    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        seen = super().observe(spec, lineage, effect)
        return type(seen)(False, None, False, False, (), (), None) if not self.readable else seen


class _ObserveWrites(FakeProvision):
    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        self.plant_found("side-effect")  # a read that changes the store
        return super().observe(spec, lineage, effect)


class _AuthoritativeLags(FakeProvision):
    """The authoritative probe lags the submit: completion would be read from the wrong read."""

    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        seen = super().observe(spec, lineage, effect)
        if self._pending.get(seen.selector_ref.selector if seen.selector_ref else "", 0):
            return type(seen)(False, None, False, False, (), seen.found, None)
        return seen


class _LeaksSecret(FakeProvision):
    def check(self, check: str, target: Any) -> Any:
        result = super().check(check, target)
        return type(result)(result.satisfied, result.code, f"{result.detail} {rig.PASSWORD}")


class _CreateSucceedsDownAsApplied(FakeProvision):
    def create(self, spec: Any, ticket: Any) -> Any:
        confirmation = super().create(spec, ticket)
        if not self.readable:
            return type(confirmation)(ConfirmationStatus.APPLIED, None, "x")
        return confirmation


@pytest.mark.parametrize(
    ("defect", "knobs", "caught_by"),
    [
        (_RunFormAccepted, {}, "run_lifetime_has_no_form_and_is_refused"),
        (_CreateNotIdempotent, {}, "submit_is_idempotent_per_selector"),
        (_FoundIsPresent, {}, "found_record_only_in_found"),
        (_DownReadsAbsent, {}, "unreadable_store_is_could_not_observe_never_absent"),
        (_ObserveWrites, {}, "reads_leave_no_trace"),
        (_AuthoritativeLags, {"lag": 3}, "authoritative_probe_and_convenience_read"),
        (_LeaksSecret, {}, "no_result_carries_the_secret"),
        (_CreateSucceedsDownAsApplied, {}, "unreadable_store_is_could_not_observe_never_absent"),
    ],
)
def test_provision_suite_catches_planted_defects(
    defect: type[FakeProvision], knobs: dict[str, Any], caught_by: str
) -> None:
    with pytest.raises((core.SuiteFailure, core.ReadMutation)) as caught:
        core.run_family(provision_cases.FAMILY, rig.fake_provision(defect, **knobs))
    assert caught_by in str(caught.value)


@pytest.mark.parametrize("lag", [0, 1, 5])
def test_the_lagging_fake_still_passes_the_suite(lag: int) -> None:
    core.run_family(provision_cases.FAMILY, rig.fake_provision(lag=lag))
