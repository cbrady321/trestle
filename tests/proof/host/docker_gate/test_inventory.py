"""L.NW-2.1: the engine inventory snapshot and the WR-PROOF-6 diff with CSC-10's attribution rule.
Every test drives the absolute-path `fake_docker.py` shim over a state file (no real engine)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof.host.docker_gate import fake_docker, inventory

FAKE = str(Path(fake_docker.__file__).resolve())
ENDPOINT = "unix:///fake/desktop-linux.sock"
SELECTOR = "trwr-r_0abc123def-db"
FIXTURE_LABELS = {inventory.FIXTURE_LABEL: "found-postgres"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv(fake_docker.STATE_ENV, str(state))
    monkeypatch.setenv(fake_docker.LOG_ENV, str(log))
    # the gate must not lean on an ambient endpoint or context
    monkeypatch.setenv("DOCKER_HOST", "unix:///ambient-default-that-must-not-be-used")
    monkeypatch.setenv("DOCKER_CONTEXT", "default")

    class Rig:
        pass

    r = Rig()
    r.state, r.log = state, log
    r.write = lambda **kw: fake_docker.write_state(state, **kw)
    r.snap = lambda: inventory.snapshot(FAKE, ENDPOINT)
    r.base = {
        "reachable": True,
        "server_version": "29.8.0",
        "containers": [{"id": "c0", "names": ["keeper"], "image": "alpine", "state": "running"}],
        "images": [{"id": "sha256:aa", "repository": "alpine", "tag": "3.20"}],
        "volumes": [{"name": "keep-vol", "labels": {}}],
        "networks": [{"id": "n0", "name": "bridge", "labels": {}}],
    }
    return r


def _with(base: dict, **extra_rows) -> dict:
    out = {k: list(v) if isinstance(v, list) else v for k, v in base.items()}
    for key, rows in extra_rows.items():
        out[key] = out[key] + rows
    return out


@pytest.mark.proves("WR-PROOF-6", "WR-PROOF-6:inventory-diff-rule", "B", "B", "LOGIC", "CI")
def test_unattributed_container_fails_diff(rig):
    rig.write(**rig.base)
    before = rig.snap()
    rig.write(
        **_with(
            rig.base,
            containers=[{"id": "c9", "names": ["stray"], "image": "alpine", "state": "running"}],
        )
    )
    diff = inventory.compute_diff(before, rig.snap())
    assert [(e["kind"], e["change"], e["name"]) for e in diff["unattributed"]] == [
        ("containers", "added", "stray")
    ]
    assert inventory.diff_failed(diff)
    # a pre-existing container whose state moved is a difference too, and it is not attributable
    changed = {**rig.base, "containers": [{**rig.base["containers"][0], "state": "exited"}]}
    rig.write(**rig.base)
    before = rig.snap()
    rig.write(**changed)
    diff = inventory.compute_diff(before, rig.snap())
    assert [e["change"] for e in diff["unattributed"]] == ["changed"]
    # and one that vanished
    rig.write(**{**rig.base, "containers": []})
    diff = inventory.compute_diff(before, rig.snap())
    assert [e["change"] for e in diff["unattributed"]] == ["removed"]


@pytest.mark.proves("WR-PROOF-6", "WR-PROOF-6:inventory-diff-rule", "B", "B", "LOGIC", "CI")
def test_unattributed_volume_or_network_fails_diff(rig):
    rig.write(**rig.base)
    before = rig.snap()
    rig.write(**_with(rig.base, volumes=[{"name": "stray-vol", "labels": {}}]))
    diff = inventory.compute_diff(before, rig.snap())
    assert [(e["kind"], e["name"]) for e in diff["unattributed"]] == [("volumes", "stray-vol")]
    assert inventory.diff_failed(diff)

    rig.write(**_with(rig.base, networks=[{"id": "n9", "name": "stray-net", "labels": {}}]))
    diff = inventory.compute_diff(before, rig.snap())
    assert [(e["kind"], e["name"]) for e in diff["unattributed"]] == [("networks", "stray-net")]
    assert inventory.diff_failed(diff)

    # a name that only resembles the selector, and a label that only resembles the fixture key
    rig.write(
        **_with(
            rig.base,
            volumes=[{"name": "trwr_r_x-db", "labels": {"trestle.proof.other": "x"}}],
            networks=[{"id": "n8", "name": "my-trwr-r_x-net", "labels": {}}],
        )
    )
    diff = inventory.compute_diff(before, rig.snap())
    assert len(diff["unattributed"]) == 2 and diff["residue"] == []

    # a pulled image is a difference nobody can attribute (images carry neither selector nor label)
    rig.write(**_with(rig.base, images=[{"id": "sha256:bb", "repository": "nginx", "tag": "1"}]))
    diff = inventory.compute_diff(before, rig.snap())
    assert [(e["kind"], e["name"]) for e in diff["unattributed"]] == [("images", "nginx")]


@pytest.mark.proves("WR-CON-3", "WR-CON-3:engine-state-captured", "B", "B", "LOGIC", "CI")
def test_engine_stopped_fails(rig):
    rig.write(**rig.base)
    before = rig.snap()
    assert before["engine"] == {
        "reachable": True,
        "server_version": "29.8.0",
        "id": "FAKE-ENGINE-ID",
    }
    rig.write(**{**rig.base, "reachable": False})
    after = rig.snap()
    assert after["engine"]["reachable"] is False and "Cannot connect" in after["engine"]["error"]
    assert all(after[k] == [] for k in inventory.KINDS)
    diff = inventory.compute_diff(before, after)
    assert diff["engine_state_changed"] is True
    assert inventory.diff_failed(diff)
    # an engine that answers but is not the same engine (restarted, upgraded) also fails
    rig.write(**{**rig.base, "server_version": "30.0.0"})
    diff = inventory.compute_diff(before, rig.snap())
    assert diff["engine_state_changed"] is True and inventory.diff_failed(diff)
    rig.write(**{**rig.base, "engine_id": "OTHER"})
    assert inventory.compute_diff(before, rig.snap())["engine_state_changed"] is True
    # never a clean pass while the engine is down at both ends
    rig.write(**{**rig.base, "reachable": False})
    down = rig.snap()
    assert inventory.diff_failed(inventory.compute_diff(down, down))
    # an unchanged engine and inventory is a clean diff
    rig.write(**rig.base)
    same = inventory.compute_diff(before, rig.snap())
    assert same == {"unattributed": [], "residue": [], "engine_state_changed": False}
    assert not inventory.diff_failed(same)


@pytest.mark.proves("WR-CON-3", "WR-CON-3:engine-state-captured", "B", "B", "LOGIC", "CI")
def test_snapshot_is_read_only_through_the_explicit_endpoint(rig):
    rig.write(**rig.base)
    rig.snap()
    calls = fake_docker.read_log(rig.log)
    assert [c["args"][:2] for c in calls] == [
        ["info", "--format"],
        ["ps", "-a"],
        ["image", "ls"],
        ["volume", "ls"],
        ["network", "ls"],
    ]
    for call in calls:
        assert call["host"] == ENDPOINT  # every argv carries the desktop-linux endpoint
        assert call["env"]["DOCKER_HOST"] is None and call["env"]["DOCKER_CONTEXT"] is None
        assert call["stdin"] in ("eof", "closed")
        assert not set(call["args"]) & {"pull", "run", "start", "stop", "rm", "create", "up"}
    assert Path(calls[0]["argv"][0]).name != "docker"  # invoked by absolute path, not through PATH


@pytest.mark.proves("WR-PROOF-6", "WR-PROOF-6:inventory-diff-rule", "B", "B", "LOGIC", "CI")
def test_attributable_residue_allowed_and_recorded(rig):
    rig.write(**rig.base)
    before = rig.snap()
    rig.write(
        **_with(
            rig.base,
            containers=[
                {"id": "c1", "names": [SELECTOR], "image": "alpine", "state": "running"},
                {
                    "id": "c2",
                    "names": ["found-pg"],
                    "image": "alpine",
                    "state": "running",
                    "labels": FIXTURE_LABELS,
                },
            ],
            volumes=[{"name": "seeded", "labels": FIXTURE_LABELS}],
            networks=[
                {"id": "n1", "name": "trwr-r_0abc123def-net", "labels": {}},
                {"id": "n2", "name": "fixture-net", "labels": FIXTURE_LABELS},
            ],
        )
    )
    diff = inventory.compute_diff(before, rig.snap())
    assert diff["unattributed"] == []
    assert diff["engine_state_changed"] is False
    assert not inventory.diff_failed(diff)  # residue does not fail the diff ...
    assert sorted((e["kind"], e["name"], e["change"]) for e in diff["residue"]) == [
        ("containers", "found-pg", "added"),
        ("containers", SELECTOR, "added"),
        ("networks", "fixture-net", "added"),
        ("networks", "trwr-r_0abc123def-net", "added"),
        ("volumes", "seeded", "added"),
    ]  # ... it is counted and recorded

    # mixed: the stray is still reported next to the residue
    rig.write(
        **_with(
            rig.base,
            containers=[
                {"id": "c1", "names": [SELECTOR], "image": "alpine", "state": "running"},
                {"id": "c7", "names": ["stray"], "image": "alpine", "state": "running"},
            ],
        )
    )
    diff = inventory.compute_diff(before, rig.snap())
    assert [e["name"] for e in diff["unattributed"]] == ["stray"]
    assert [e["name"] for e in diff["residue"]] == [SELECTOR]


def test_selector_and_label_rules_are_csc10():
    ok = inventory.is_attributable
    assert ok({"names": ["trwr-r_0abc-db"]}) and ok({"names": ["/trwr-r_0abc-net"]})
    assert ok({"name": "n", "labels": FIXTURE_LABELS})
    assert not ok({"names": ["trwr-"]}) and not ok({"names": ["trwr--x"]})
    assert not ok({"names": ["web"], "labels": {inventory.FIXTURE_LABEL: ""}})
    assert not ok({"repository": "alpine", "tag": "3.20"})


def test_unparseable_or_failing_read_is_not_a_clean_snapshot(rig):
    rig.write(**rig.base)

    class Done:
        def __init__(self, rc, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, "boom"

    def runner(argv, env):
        if "info" in argv:
            return Done(0, '{"ServerVersion": "1", "ID": "e"}')
        if argv[argv.index("--host") + 2] == "ps":
            return Done(1)
        return Done(0)

    snap = inventory.snapshot(FAKE, ENDPOINT, runner=runner)
    assert snap["engine"]["reachable"] is False and "ps: exit 1" in snap["engine"]["error"]

    snap = inventory.snapshot(FAKE, ENDPOINT, runner=lambda a, e: Done(0, "not json"))
    assert snap["engine"]["reachable"] is False

    def broken(argv, env):
        raise FileNotFoundError("no docker")

    assert inventory.snapshot(FAKE, ENDPOINT, runner=broken)["engine"]["reachable"] is False
