"""CI twins of `host/test_closure_host.py` (L.RB-1.4; MC-B-03; STUB · CI): the same plugin, the
same Compose definition, the same call and the same facts, on the fake binding, whose closure is
derived by the fake resolver over that definition."""

from __future__ import annotations

from pathlib import Path

import pytest
from trestle_packs.fakes.compose import FakeComposeResolver

from twin import closure_case, fake_binding, harness


def fake_closure(selected: list[str]) -> frozenset[str]:
    resolver = FakeComposeResolver({closure_case.PROJECT: closure_case.COMPOSE})
    derived = resolver.closure(closure_case.PROJECT, frozenset(selected))
    assert not hasattr(derived, "code"), derived  # a closure, not a refusal
    return frozenset(derived.services)


def run(tmp_path: Path, services: list[str] | None) -> tuple[frozenset, dict]:
    state = tmp_path / "engine.json"
    environ = closure_case.environ(harness.twin_environ(state))
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = closure_case.call(host, "closure-twin", services)
        assert answer["state"] == "succeeded", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    got = closure_case.started(answer, entries, run_id)
    after = fake_binding.read_state(state)
    prefix = harness.selector_prefix(run_id)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]  # all released
    return got, after


@pytest.mark.stub_proven("WR-ENV-1:started-set-equals-closure@stub-twin")
def test_started_set_equals_closure(tmp_path: Path) -> None:
    got, after = run(tmp_path, closure_case.SELECTED)
    assert got == fake_closure(closure_case.SELECTED)
    assert len([c for c in after["calls"] if c["member"] == "create"]) == len(got)


@pytest.mark.stub_proven("WR-ENV-1:compose-file-only-works@stub-twin")
def test_compose_file_only_stack_works(tmp_path: Path) -> None:
    got, _ = run(tmp_path, None)
    assert got == fake_closure(closure_case.every_service())
