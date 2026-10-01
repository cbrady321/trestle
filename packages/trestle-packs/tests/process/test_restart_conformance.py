"""L.RB-8.1: the local-process restart family's conformance suite, run UNMODIFIED against the fake
local process and the real `LocalProcessPort` launching the stdlib override app (WR-PROOF-4:
b-process-restart-suite, WR-ENV-7:restart-mechanism-adapter, SA-10, SA-14). One suite file
(`tests/conformance/process_restart_cases.py`), registered through `register_family` and run by
`run_family`. The real binding starts real children in the run's process group, so it is PROC,
venue BOTH: CI runs it here and `host-proc` runs it on the macOS host. Planted defects prove the
suite is not vacuous.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conformance import process_restart_cases
from tests.proof.suites.ports import core
from trestle.workflow.values import Confirmation, ConfirmationStatus

from trestle_packs.fakes.command import Confirmation as FakeConfirmation
from trestle_packs.fakes.command import ConfirmationStatus as FakeStatus
from trestle_packs.fakes.local_process import FakeLocalProcess
from trestle_packs.process.local import LocalProcessPort

from . import rig

BINDINGS = [
    pytest.param("fake", id="fake"),
    pytest.param(
        "real",
        id="real",
        marks=[
            pytest.mark.proves(
                "WR-PROOF-4", "WR-PROOF-4:b-process-restart-suite", "B", "B", "PROC", "BOTH"
            ),
            pytest.mark.proves(
                "WR-ENV-7", "WR-ENV-7:restart-mechanism-adapter", "B", "B", "PROC", "BOTH"
            ),
        ],
    ),
]


@pytest.mark.parametrize("binding", BINDINGS)
def test_local_process_restart_suite(binding: str, tmp_path: Path) -> None:
    build = rig.fake_restart() if binding == "fake" else rig.real_restart()
    run = core.run_family(process_restart_cases.FAMILY, lambda: build(tmp_path))
    assert run.cases_run == tuple(c.name for c in process_restart_cases.CASES)
    assert run.suite_sha256 == core.sha256_of(Path(process_restart_cases.__file__))


# planted defects: each fake / port has one, and the suite (unmodified) names the case that
# catches it


class _StartsBeforeItStops(FakeLocalProcess):
    def _relaunch(self, selector: str) -> FakeConfirmation:
        held = self._instances.pop(selector, None)
        if held is None:
            return FakeConfirmation(FakeStatus.NOT_APPLIED, None, None)
        self._instances[selector] = (self._launched(held[0].argv), held[1])  # the new one first
        self._ended(held[0])  # and only then the old one
        return FakeConfirmation(FakeStatus.APPLIED, None, selector)


class _LeavesTheOldRunning(FakeLocalProcess):
    def _relaunch(self, selector: str) -> FakeConfirmation:
        held = self._instances.pop(selector, None)
        if held is None:
            return FakeConfirmation(FakeStatus.NOT_APPLIED, None, None)
        self._events.append("start")  # the old process is never ended
        self._instances[selector] = (self._spawn(held[0].argv), held[1])
        return FakeConfirmation(FakeStatus.APPLIED, None, selector)


class _NotAppliedAfterStopping(FakeLocalProcess):
    def _relaunch(self, selector: str) -> FakeConfirmation:
        made = super()._relaunch(selector)
        if made.status is FakeStatus.UNKNOWN:
            return FakeConfirmation(FakeStatus.NOT_APPLIED, made.code, None)
        return made


class _SignalsEveryProcessOfTheCommand(FakeLocalProcess):
    def _relaunch(self, selector: str) -> FakeConfirmation:
        for process in self._table:
            process.alive = False  # a found process with the same command line dies too
        return super()._relaunch(selector)


class _RestartsWhatItDoesNotHold(FakeLocalProcess):
    def _relaunch(self, selector: str) -> FakeConfirmation:
        if selector not in self._instances:
            return FakeConfirmation(FakeStatus.APPLIED, None, selector)
        return super()._relaunch(selector)


class _RestartDescriptorIsFresh(FakeLocalProcess):
    def release_descriptor(self, call: Any) -> Any:
        if call.member == "restart":
            return {"form": "in_run_group", "helpers_disclosed": True}  # not the handle's own
        return super().release_descriptor(call)


@pytest.mark.parametrize(
    ("defect", "caught_by"),
    [
        (_StartsBeforeItStops, "restart_ends_the_old_process_before_the_new_one_starts"),
        (_LeavesTheOldRunning, "restart_ends_the_old_process_before_the_new_one_starts"),
        (_NotAppliedAfterStopping, "a_restart_that_cannot_relaunch_is_never_not_applied"),
        (_SignalsEveryProcessOfTheCommand, "restart_identity_is_the_recorded_command"),
        (_RestartsWhatItDoesNotHold, "restart_of_a_selector_the_port_does_not_hold"),
        (_RestartDescriptorIsFresh, "restart_is_a_repair_carrying_the_handles_own_descriptor"),
    ],
)
def test_restart_suite_catches_planted_fake_defects(
    defect: type[FakeLocalProcess], caught_by: str, tmp_path: Path
) -> None:
    build = rig.fake_restart(defect)
    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(process_restart_cases.FAMILY, lambda: build(tmp_path))
    assert caught_by in str(caught.value)


class _NotAppliedAfterStoppingReal(LocalProcessPort):
    def _relaunch(self, target: Any) -> Confirmation:
        instance = self._instances.pop(target.selector, None)
        if instance is None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        self._end(instance)
        return self._launch(instance.selector, instance.spec)  # NOT_APPLIED after a stop


class _LeavesTheOldRunningReal(LocalProcessPort):
    _orphans: list[Any]

    def _relaunch(self, target: Any) -> Confirmation:
        instance = self._instances.get(target.selector)
        if instance is None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        self.__dict__.setdefault("_orphans", []).append(instance)
        return self._launch(instance.selector, instance.spec)  # the old process is never ended

    def close(self) -> None:
        for orphan in self.__dict__.get("_orphans", []):
            self._end(orphan)
        super().close()


@pytest.mark.parametrize(
    ("defect", "caught_by"),
    [
        (_NotAppliedAfterStoppingReal, "a_restart_that_cannot_relaunch_is_never_not_applied"),
        (_LeavesTheOldRunningReal, "restart_ends_the_old_process_before_the_new_one_starts"),
    ],
)
def test_restart_suite_catches_planted_real_defects(
    defect: type[LocalProcessPort], caught_by: str, tmp_path: Path
) -> None:
    build = rig.real_restart(defect)
    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(process_restart_cases.FAMILY, lambda: build(tmp_path))
    assert caught_by in str(caught.value)
