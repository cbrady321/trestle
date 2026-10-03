"""CI twin of `host/test_services_selection.py` (C-3; MC-B-03; STUB · CI): the same plugin, the
same Compose definition and the same call on the fake binding. A closed selection (`http_support`)
starts that one service and lists no `postgres` vertex."""

from __future__ import annotations

from pathlib import Path

from trestle_env import tree
from twin import closure_case, fake_binding, harness


def test_a_closed_selection_starts_only_its_services(tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    environ = closure_case.environ(harness.twin_environ(state))
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = closure_case.call(host, "selection-twin", [tree.HTTP_SUPPORT_SERVICE])
        assert answer["state"] == "succeeded", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    assert closure_case.started(answer, entries, run_id) == {tree.HTTP_SUPPORT_SERVICE}
    listed = {tuple(item["path"]) for item in answer["answer"]["listed"]}
    assert (tree.POSTGRES_SERVICE,) not in listed, listed
    after = fake_binding.read_state(state)
    prefix = harness.selector_prefix(run_id)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]  # all released
