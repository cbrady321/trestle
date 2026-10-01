"""CI twins of `host/test_provisioning.py` (L.RB-6.3; MC-B-03; STUB · CI): the same plugin, catalog
and calls on the fake binding, whose record store (`fake_binding.DurableFakeProvision`) persists to
a file for as long as the test, as the environment's own Postgres does on the HOST."""

from __future__ import annotations

from pathlib import Path

import pytest

from twin import fake_binding, harness, provisioning_case

CASE = provisioning_case


def environ(tmp_path: Path) -> dict[str, str | None]:
    world = CASE.world(tmp_path / "world-dir")
    records = tmp_path / "records.json"
    return {**world.environ(tmp_path / "engine.json"), fake_binding.RECORDS_ENV: str(records)}


def store(tmp_path: Path) -> dict:
    return fake_binding.read_records(tmp_path / "records.json")


@pytest.mark.stub_proven("WR-ENV-5:after-provisioning@stub-twin")
def test_probe_first_submit_once_verified_by_db(tmp_path: Path) -> None:
    with harness.reference_host(tmp_path / "home", environ(tmp_path)) as host:
        _, entries, where = CASE.run(host, "provision-twin")
    assert CASE.submits(entries) == 1
    CASE.assert_probe_first(where, entries)
    CASE.assert_never_released(entries)
    held = store(tmp_path)
    assert [v[0] for v in held["records"].values()] == ["postgres"] and held["submits"] == 1


@pytest.mark.stub_proven("WR-ENV-5:after-provisioning@stub-twin")
def test_second_equivalent_run_reuses_no_submit(tmp_path: Path) -> None:
    with harness.reference_host(tmp_path / "home", environ(tmp_path)) as host:
        _, first, _ = CASE.run(host, "provision-twin")
        _, second, _ = CASE.run(host, "provision-twin")
    assert (CASE.submits(first), CASE.submits(second)) == (1, 0)
    CASE.assert_never_released(first + second)
    held = store(tmp_path)
    assert len(held["records"]) == 1 and held["submits"] == 1


@pytest.mark.stub_proven("WR-ENV-5:after-provisioning@stub-twin")
def test_system_test_after_provisioning_postcondition(tmp_path: Path) -> None:
    with harness.reference_host(tmp_path / "home", environ(tmp_path)) as host:
        _, entries, _ = CASE.run(host, "provision-twin")
    CASE.assert_test_after_postcondition(entries)
