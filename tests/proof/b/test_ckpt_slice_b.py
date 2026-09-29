"""L.RB-0.5: planted self-tests of `tests/proof/ckpt/slice_b.py` over synthetic worlds.

The live conditions run only under `meta ckpt slice-b` (DM-80). Here every condition reads a
synthetic `World` through the module's `make_world` seam, so each planted failure and pass stays
true at every later head (DM-11, CM-5). L.RB-12.4 adds condition (i) and its planted defect here.
"""

from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path

import pytest

from tests.proof import ckpt as ckpt_mod
from tests.proof import meta as meta_mod
from tests.proof.b import stub_labels as sl
from tests.proof.ckpt import slice_b
from tests.proof.host import record as record_mod

SUFFIX = sl.TWIN_SUFFIX
DOCKER_LABEL = "WR-ENV-10:docker-claim"
CI_LABEL = "WR-ENV-1:ci-claim"
STUB_LABEL = "WR-ENV-3:stubbed"
GATED = {"OPEN-MISE-HOST": 1, "OQ-25": 1, "OQ-26": 2, "F-12": 1, "F-B3-2": 1, "F-13(d)": 1}
HOST_NODE = "packages/trestle-env/tests/host/test_x.py::test_a"
TWIN_NODE = "packages/trestle-env/tests/twin/test_x_twin.py::test_a"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _label(label_id, *, tier="LOGIC", venue="CI", posture="claim", **extra):
    return {
        "id": label_id,
        "row": label_id.split(":")[0],
        "step": "B",
        "slice": "B",
        "tier": tier,
        "venue": venue,
        "posture": posture,
        "declared_by": "L.RB-4.3",
        **extra,
    }


def green_world() -> slice_b.World:
    """A synthetic world every condition passes."""
    clause_ids = slice_b.B_CLAUSE_IDS
    labels = [
        _label(CI_LABEL),
        _label(DOCKER_LABEL, tier="DOCKER", venue="HOST"),
        _label(DOCKER_LABEL + SUFFIX, tier="STUB", posture="stub_proven"),
        _label(STUB_LABEL, tier="STUB", posture="stub_proven"),
    ]
    labels += [_label(x, tier="LOGIC") for x in slice_b.DEFERRAL_LABELS]
    n = 0
    for oq, count in GATED.items():
        for _ in range(count):
            n += 1
            labels.append(_label(f"WR-ENV-9:gated-{n}", posture="gated_on", oq=oq))
    labels.append(_label("WR-ENV-9:na-one", posture="na", reason="not applicable here"))
    clauses = [
        {"id": c, "step": "B", "stub_label_required": c == "B4.6"}
        for c in clause_ids
    ]  # fmt: skip
    nodes = []
    for c in clause_ids:
        extra = [STUB_LABEL] if c == "B4.6" else []
        nodes.append({"nodeid": f"t::{c}", "labels": [c, CI_LABEL, *extra], "docker_host": False})
    # B1.1 is proven on the docker binding: a docker_host node and its CI twin
    nodes.append(
        {"nodeid": HOST_NODE, "labels": ["B1.1", DOCKER_LABEL], "docker_host": True},
    )
    nodes.append(
        {"nodeid": TWIN_NODE, "labels": [DOCKER_LABEL + SUFFIX], "docker_host": False},
    )
    report = {
        key: {"status": "PROVEN"}
        for key in [*clause_ids, *(lb["id"] for lb in labels if lb["posture"] != "gated_on")]
    }
    docker = {
        "sha": "a" * 40,
        "status": "PASSED",
        "diff": {"unattributed": [], "engine_state_changed": False},
        "results": [
            {"nodeid": HOST_NODE, "outcome": "PASSED", "labels": ["B1.1", DOCKER_LABEL]},
            {"nodeid": slice_b.LEGACY_LIVE_NODE, "outcome": "PASSED", "labels": []},
        ],
    }
    proc = {"sha": "a" * 40, "status": "PASSED", "results": []}
    deferrals = [
        {"label": slice_b.DEFERRAL_LABELS[0], "closes_at": "NW-2", "citation": "c"},
        {"label": slice_b.DEFERRAL_LABELS[1], "closes_at": "RB-12", "citation": "c"},
        {"label": slice_b.DEFERRAL_LABELS[2], "closes_at": "RB-12", "citation": "c"},
        {"label": slice_b.DEFERRAL_LABELS[3], "closes_at": "RB-12", "citation": "c"},
        {"label": "WR-OWN-8:environment-lease", "closes_at": "SL-8", "citation": "c"},  # not B4's
    ]
    stub = sl.StubLabels(
        twin_suffix=SUFFIX,
        rows=[{"label": STUB_LABEL, "group": "D-1", "text": "t", "deferral": "OPEN-MISE-HOST"}],
    )
    return slice_b.World(
        report=report,
        labels=labels,
        clauses=clauses,
        deferrals=deferrals,
        nodes=nodes,
        host_proc=proc,
        host_docker=docker,
        stub=stub,
        landed={"L.RB-4.5"},
        d8=(0, ""),
        open_question_ids=set(GATED),
    )


@pytest.fixture
def world(monkeypatch):
    holder = {"w": green_world()}
    monkeypatch.setattr(slice_b, "make_world", lambda commit: holder["w"])
    return holder


def evaluate() -> dict[str, tuple[bool, str]]:
    return {r.id: (r.ok, r.reason) for r in ckpt_mod.evaluate(slice_b, "HEAD")}


def failing() -> dict[str, str]:
    return {cid: reason for cid, (ok, reason) in evaluate().items() if not ok}


def _label_of(w, label_id):
    return next(lb for lb in w.labels if lb["id"] == label_id)


def test_green_world_passes_every_condition(world):
    assert failing() == {}


def test_conditions_are_the_25_clauses_the_labels_and_b_to_h():
    ids = [c.id for c in slice_b.CONDITIONS]
    assert [i for i in ids if i.startswith("J-SLICE-B-a:B")] == [
        f"J-SLICE-B-a:{c}" for c in slice_b.B_CLAUSE_IDS
    ]
    assert len(slice_b.B_CLAUSE_IDS) == 25
    assert ids[-8:] == [
        "J-SLICE-B-a:labels",
        *(f"J-SLICE-B-{x}" for x in "bcdefgh"),
    ]
    assert len(ids) == len(set(ids))


def test_conditions_fail_on_planted_defects(world):
    """(a)-(h), one planted defect each; every other condition stays green."""
    # (a) a DOCKER-tier label and clause with CI-only evidence
    w = green_world()
    w.host_docker["results"] = [w.host_docker["results"][1]]
    world["w"] = w
    got = failing()
    assert "DOCKER-tier" in got["J-SLICE-B-a:labels"]
    assert "DOCKER-tier" in got["J-SLICE-B-a:B1.1"]
    assert "J-SLICE-B-a:B2.1" not in got

    w = green_world()
    w.host_docker = None
    world["w"] = w
    assert "no admissible host-docker record" in failing()["J-SLICE-B-a:labels"]

    # (b) a record inadmissible under CM-6
    w = green_world()
    w.admissible = lambda record: (False, "not an ancestor")
    world["w"] = w
    got = failing()
    assert set(got) == {"J-SLICE-B-b"} and "not admissible" in got["J-SLICE-B-b"]

    # (c) a record with a non-empty diff, one that changed the engine, one that omits a node
    w = green_world()
    w.host_docker["diff"]["unattributed"] = ["c1"]
    world["w"] = w
    assert "diff.unattributed" in failing()["J-SLICE-B-c"]
    w = green_world()
    w.host_docker["diff"]["engine_state_changed"] = True
    world["w"] = w
    assert "engine_state_changed" in failing()["J-SLICE-B-c"]
    w = green_world()
    w.host_docker["results"] = [r for r in w.host_docker["results"] if r["nodeid"] != HOST_NODE]
    world["w"] = w
    assert HOST_NODE in failing()["J-SLICE-B-c"]
    w = green_world()
    w.host_docker["results"] = [
        r for r in w.host_docker["results"] if r["nodeid"] != slice_b.LEGACY_LIVE_NODE
    ]
    world["w"] = w
    assert "test_stack_runner_live_compose" in failing()["J-SLICE-B-c"]
    w = green_world()
    w.host_docker["results"][0]["outcome"] = "FAILED"
    world["w"] = w
    assert "pass set" in failing()["J-SLICE-B-c"]
    w = green_world()
    w.host_docker["status"] = "PRECONDITION_UNMET"
    world["w"] = w
    assert "PRECONDITION_UNMET" in failing()["J-SLICE-B-c"]
    w = green_world()
    w.host_proc = None
    w.host_docker = None
    world["w"] = w
    assert "host-proc" in failing()["J-SLICE-B-c"]

    # (d) a docker_host node without a twin
    w = green_world()
    w.nodes = [n for n in w.nodes if n["nodeid"] != TWIN_NODE]
    world["w"] = w
    got = failing()
    assert "not collected" in got["J-SLICE-B-d"]

    # (e) a twin label without posture stub_proven
    w = green_world()
    _label_of(w, DOCKER_LABEL + SUFFIX)["posture"] = "claim"
    world["w"] = w
    assert "stub_proven" in failing()["J-SLICE-B-e"]
    # (e) a stub_proven label missing from stub_labels.toml
    w = green_world()
    w.stub = sl.StubLabels(twin_suffix=SUFFIX, rows=[])
    world["w"] = w
    assert STUB_LABEL in failing()["J-SLICE-B-e"]
    # (e) B4.5 carrying a label
    w = green_world()
    next(n for n in w.nodes if "B4.5" in n["labels"])["labels"].append(STUB_LABEL)
    world["w"] = w
    assert "B4.5" in failing()["J-SLICE-B-e"]

    # (f) a deferral closing in B4 whose label is not PROVEN
    w = green_world()
    w.report[slice_b.DEFERRAL_LABELS[2]]["status"] = "UNPROVEN"
    world["w"] = w
    got = failing()
    assert slice_b.DEFERRAL_LABELS[2] in got["J-SLICE-B-f"]
    assert slice_b.DEFERRAL_LABELS[2] in got["J-SLICE-B-a:labels"]

    # (g) d8 shows a divergence
    w = green_world()
    w.d8 = (1, "d8: diverged")
    world["w"] = w
    assert set(failing()) == {"J-SLICE-B-g"}

    # (h) a gated registration is absent
    w = green_world()
    w.labels = [lb for lb in w.labels if lb.get("oq") != "OQ-26"][:]
    world["w"] = w
    assert "OQ-26" in failing()["J-SLICE-B-h"]


def test_clause_status_is_stub_proven_only_with_its_label(world):
    w = world["w"]
    assert slice_b.render_status(w, "B4.6") == slice_b.STUB_PROVEN
    assert slice_b.render_status(w, "B2.1") == slice_b.PROVEN_CI
    assert slice_b.render_status(w, "B1.1") == slice_b.PROVEN_HOST
    assert slice_b.render_status(w, DOCKER_LABEL) == slice_b.PROVEN_HOST
    assert slice_b.render_status(w, DOCKER_LABEL + SUFFIX) == slice_b.STUB_PROVEN
    assert slice_b.render_status(w, "WR-ENV-9:gated-1") == slice_b.DECLARED
    # B4.6 requires a STUB label
    next(n for n in w.nodes if "B4.6" in n["labels"])["labels"].remove(STUB_LABEL)
    assert "requires a STUB label" in failing()["J-SLICE-B-a:B4.6"]


def test_gated_on_without_open_question_and_na_without_reason_are_not_declared(world):
    w = world["w"]
    _label_of(w, "WR-ENV-9:gated-1")["oq"] = ""
    _label_of(w, "WR-ENV-9:na-one")["reason"] = ""
    reason = failing()["J-SLICE-B-a:labels"]
    assert "gated_on with no open question" in reason and "na with no reason" in reason


def test_no_declared_b_label_is_not_green(world):
    world["w"].labels = []
    assert "no step-B label is declared" in failing()["J-SLICE-B-a:labels"]


def test_dry_run_of_an_empty_world_lists_every_b_clause(world):
    world["w"] = slice_b.World(clauses=green_world().clauses)
    pending = failing()
    assert all(f"J-SLICE-B-a:{c}" in pending for c in slice_b.B_CLAUSE_IDS)
    assert len([k for k in pending if k.startswith("J-SLICE-B-a:B")]) == 25


def test_unreadable_world_is_a_failing_condition_not_a_pass(monkeypatch):
    def boom(commit):
        raise RuntimeError("cannot read")

    monkeypatch.setattr(slice_b, "make_world", boom)
    assert all(not ok for _cid, (ok, _r) in evaluate().items())
    assert "cannot evaluate" in next(iter(failing().values()))


def _sh(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_condition_b_uses_cm6_admissibility_over_a_real_history(tmp_path, world):
    """A record whose sha is not an ancestor of the anchor, or with a non-record path changed
    since its sha, is inadmissible (CM-6); `record.is_admissible` is the one implementation."""
    repo = tmp_path / "repo"
    (repo / "tests/proof/host/host-docker").mkdir(parents=True)
    _sh(repo, "init", "-q", "-b", "master")
    (repo / "a.txt").write_text("a")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "base")
    sha = _sh(repo, "rev-parse", "HEAD")
    (repo / "tests/proof/host/host-docker/x.json").write_text("{}")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "records only")
    records_only = _sh(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("changed")
    _sh(repo, "commit", "-q", "-am", "product change")
    changed = _sh(repo, "rev-parse", "HEAD")
    _sh(repo, "checkout", "-q", "-b", "side", sha)
    (repo / "b.txt").write_text("b")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "side")
    side = _sh(repo, "rev-parse", "HEAD")

    def record(at):
        return {"gate": "host-docker", "sha": at}

    def verdict(rec, anchor):
        w = green_world()
        w.host_proc = None
        w.host_docker = rec
        w.admissible = lambda r: record_mod.is_admissible(r, anchor, repo)
        return slice_b.verdict_b(w)

    assert verdict(record(sha), records_only)[0]  # only record paths changed since
    assert not verdict(record(sha), changed)[0]  # a product path changed since its sha
    assert not verdict(record(side), changed)[0]  # its sha is not an ancestor of the anchor


def test_trigger_merge_is_j_slice_b(tmp_path, monkeypatch):
    assert slice_b.TRIGGER_MERGE == "J-SLICE-B"
    assert slice_b.TAG == "wr-ckpt/slice-b"
    repo = tmp_path / "repo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "not a trigger")
    sha = _sh(repo, "rev-parse", "HEAD")

    def never(commit):
        raise AssertionError("a live world was built on a non-trigger commit")

    monkeypatch.setattr(slice_b, "make_world", never)
    monkeypatch.setattr(meta_mod, "ROOT", repo)
    args = type("A", (), {"name": "slice-b", "dry": False, "preview": False, "commit": sha})()
    assert meta_mod.cmd_ckpt(args) == 0  # not evaluated: exit 0, no world built

    # the framework resolves the module by its checkpoint name
    assert ckpt_mod.load_module("slice-b") is slice_b


def test_module_and_helpers_never_match_default_collection():
    """DM-80: the live conditions run only under `meta ckpt slice-b`."""
    for name in ("slice_b.py", "../b/stub_labels.py", "../b/twin_audit.py"):
        path = Path(slice_b.__file__).parent / name
        assert path.exists()
        assert not (path.name.startswith("test_") or path.name.endswith("_test.py"))


def test_ledger_snapshot_is_not_mutated_by_planted_worlds():
    w = green_world()
    before = copy.deepcopy(w.report)
    slice_b.render_status(w, "B2.1")
    assert w.report == before
