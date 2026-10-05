"""L.SL-3.3 (WR-PROOF-4, SA-14): one suite over fake and real, and `differ d8`.

The family suite file is one file: its sha256 is recorded at registration and every run, fake or
real, reports the same one. `differ d8 --pair process` then compares the fake and the real
local-process port answer for answer; planted divergences show it is not vacuous."""

from __future__ import annotations

from pathlib import Path

import pytest
from trestle_packs.fakes import FakeLocalProcess

from tests.proof import differ
from tests.proof.differ_modes import d8_fake_real
from tests.proof.host import record as record_mod
from tests.proof.suites.ports import core, families, implementations
from trestle.workflow.values import Confirmation, ConfirmationStatus


@pytest.mark.proves("WR-PROOF-4", "WR-PROOF-4:A-command-runner", "A", "single", "STUB", "BOTH")
def test_one_suite_file_passes_fake_and_real(tmp_path: Path) -> None:
    """The same suite file (sha256 recorded) passes every fake and every real implementation."""
    suite = core.sha256_of(Path(families.__file__))
    pairs = {
        families.COMMAND_EXECUTION: ("fake-command", "real-command"),
        families.LOCAL_PROCESS_SUPERVISION: ("fake-local", "real-local"),
    }
    for family, impl_ids in pairs.items():
        runs = []
        for impl_id in impl_ids:
            _, factory = implementations.IMPLEMENTATIONS[impl_id]
            runs.append(core.run_family(family, lambda f=factory: f(tmp_path)))
        assert [run.implementation for run in runs] == list(impl_ids)
        assert {run.suite_sha256 for run in runs} == {suite}
        assert len({run.cases_run for run in runs}) == 1  # the same cases ran on both


def test_d8_process_pair_has_zero_differences(capsys: pytest.CaptureFixture[str]) -> None:
    assert differ.main(["d8", "--pair", "process"]) == 0
    assert "0 differences" in capsys.readouterr().out


def test_d8_default_runs_every_built_pair(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    passed = _docker_record("PASSED", [RECORD_NODE, COMPOSE_NODE])
    monkeypatch.setattr(record_mod, "select", lambda gate, anchor, cwd=None: passed)
    assert differ.main(["d8"]) == 0
    out = capsys.readouterr().out
    assert "--pair process: " in out and "0 differences" in out
    assert "--pair container: equal on the host" in out
    assert "--pair compose: equal on the host" in out


def test_d8_unbuilt_pair_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert differ.main(["d8", "--pair", "grant"]) == 2
    assert "no builder" in capsys.readouterr().out
    assert "d8" not in differ.UNBUILT_MODES  # built here, no longer listed as unbuilt


# L.NW-2.8: the Docker pairs are read back from the host-docker record admissible for HEAD
RECORD_NODE = d8_fake_real.RECORD_PAIRS["container"]
COMPOSE_NODE = d8_fake_real.RECORD_PAIRS["compose"]


def _docker_record(status: str, passed: list[str], mode: str = "run") -> dict:
    return {
        "sha": "0" * 40,
        "gate": "host-docker",
        "mode": mode,
        "status": status,
        "results": [{"nodeid": n, "outcome": "PASSED"} for n in passed],
    }


@pytest.mark.parametrize(
    "record",
    [
        None,
        _docker_record("PRECONDITION_UNMET", [], mode="preflight"),
        _docker_record("FAILED", [COMPOSE_NODE]),
    ],
    ids=["no-record", "precondition-unmet", "node-not-passed"],
)
def test_d8_docker_pair_without_a_passing_record_is_unproven_never_equal(
    record: dict | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(record_mod, "select", lambda gate, anchor, cwd=None: record)
    assert differ.main(["d8", "--pair", "container"]) == d8_fake_real.UNPROVEN_EXIT
    out = capsys.readouterr().out
    assert "real = UNPROVEN" in out and "equal" not in out


class _StopsWhatItDoesNotHold(FakeLocalProcess):
    def stop(self, target, ticket):
        super().stop(target, ticket)
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)


@pytest.mark.parametrize("defect", [_StopsWhatItDoesNotHold], ids=["stop-unheld-applied"])
def test_d8_catches_a_planted_divergence(
    defect: type[FakeLocalProcess], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        implementations.IMPLEMENTATIONS,
        "fake-local",
        (families.LOCAL_PROCESS_SUPERVISION, lambda base: implementations.fake_local(base, defect)),
    )
    pair = d8_fake_real.PAIRS["process"]
    fake = d8_fake_real.transcript(pair[1], pair[3])
    real = d8_fake_real.transcript(pair[2], pair[3])
    diffs = d8_fake_real.compare(fake, real)
    assert diffs and any("stop" in line for line in diffs)
    assert differ.main(["d8", "--pair", "process"]) == 1
