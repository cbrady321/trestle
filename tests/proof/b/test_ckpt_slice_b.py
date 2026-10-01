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
from tests.proof import transcribe as transcribe_mod
from tests.proof.b import planted_history as ph
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
    cells = {str(c["id"]): str(c["cell"]) for c in transcribe_mod.load_matrix_map()}
    clauses = [
        {"id": c, "step": "B", "cell": cells[c], "stub_label_required": c == "B4.6"}
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


def test_conditions_are_the_25_clauses_the_labels_and_b_to_i():
    ids = [c.id for c in slice_b.CONDITIONS]
    assert [i for i in ids if i.startswith("J-SLICE-B-a:B")] == [
        f"J-SLICE-B-a:{c}" for c in slice_b.B_CLAUSE_IDS
    ]
    assert len(slice_b.B_CLAUSE_IDS) == 25
    assert ids[-9:] == [
        "J-SLICE-B-a:labels",
        *(f"J-SLICE-B-{x}" for x in "bcdefghi"),
    ]
    assert len(ids) == len(set(ids))


@pytest.mark.proves("WR-PROOF-3", "WR-PROOF-3:b-stub-labels-registered", "B", "B", "LOGIC", "CI")
@pytest.mark.proves("WR-PROOF-6", "WR-PROOF-6:ckpt-requires-clean-records", "B", "B", "LOGIC", "CI")
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
    # (a) and column B (i) read only admissible records: a DOCKER-tier label or clause resting on
    # an inadmissible one fails there too
    assert set(got) == {"J-SLICE-B-b", "J-SLICE-B-i", "J-SLICE-B-a:B1.1", "J-SLICE-B-a:labels"}
    assert "not admissible" in got["J-SLICE-B-b"]
    assert "not admissible for the anchor" in got["J-SLICE-B-a:B1.1"]

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


@pytest.mark.proves("WR-PROOF-6", "WR-PROOF-6:ckpt-requires-clean-records", "B", "B", "LOGIC", "CI")
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


# ---------------------------------------------------------------------------
# L.RB-12.4: every condition, (c) through the record resolver, (i) column B, the part-marker check
# ---------------------------------------------------------------------------

LEGACY_NODE = slice_b.LEGACY_LIVE_NODE
ALL_CONDITION_IDS = [
    *(f"J-SLICE-B-a:{c}" for c in slice_b.B_CLAUSE_IDS),
    "J-SLICE-B-a:labels",
    *(f"J-SLICE-B-{x}" for x in "bcdefghi"),
]


def _plant_clause_without_marker(clause_id):
    def plant(w):
        w.nodes = [n for n in w.nodes if clause_id not in n["labels"]]

    return plant


def _plant_labels(w):
    w.report[CI_LABEL]["status"] = "UNPROVEN"


def _plant_b(w):
    w.admissible = lambda record: (False, "not an ancestor")


def _plant_c(w):
    w.host_docker["diff"]["unattributed"] = ["c1"]


def _plant_d(w):
    w.nodes = [n for n in w.nodes if n["nodeid"] != TWIN_NODE]


def _plant_e(w):
    _label_of(w, DOCKER_LABEL + SUFFIX)["posture"] = "claim"


def _plant_f(w):
    w.report[slice_b.DEFERRAL_LABELS[2]]["status"] = "UNPROVEN"


def _plant_g(w):
    w.d8 = (1, "d8: diverged")


def _plant_h(w):
    w.labels = [lb for lb in w.labels if lb.get("oq") != "OQ-26"]


def _plant_i(w):
    w.report["B2.1"]["status"] = "UNPROVEN"


PLANTED = {
    "J-SLICE-B-a:labels": _plant_labels,
    "J-SLICE-B-b": _plant_b,
    "J-SLICE-B-c": _plant_c,
    "J-SLICE-B-d": _plant_d,
    "J-SLICE-B-e": _plant_e,
    "J-SLICE-B-f": _plant_f,
    "J-SLICE-B-g": _plant_g,
    "J-SLICE-B-h": _plant_h,
    "J-SLICE-B-i": _plant_i,
    **{f"J-SLICE-B-a:{c}": _plant_clause_without_marker(c) for c in slice_b.B_CLAUSE_IDS},
}


def test_all_conditions_registered(world):
    """(a)-(i) are all present and each fails on its own planted defect (the green world passes)."""
    assert [c.id for c in slice_b.CONDITIONS] == ALL_CONDITION_IDS
    assert set(PLANTED) == set(ALL_CONDITION_IDS)
    assert failing() == {}
    for condition_id in ALL_CONDITION_IDS:
        w = green_world()
        PLANTED[condition_id](w)
        world["w"] = w
        assert condition_id in failing(), f"{condition_id} passed on its planted defect"


# ---- (c) over planted histories ------------------------------------------------------------------


def _results(*, host=True, legacy=True, extra=()):
    rows = []
    if host:
        rows.append({"nodeid": HOST_NODE, "outcome": "PASSED", "labels": ["B1.1", DOCKER_LABEL]})
    if legacy:
        rows.append({"nodeid": LEGACY_NODE, "outcome": "PASSED", "labels": []})
    return [*rows, *extra]


def _pair_fields(results=None, **diff):
    """The fields of a planted host-docker record: a result list, and the given diff keys."""
    fields = {"results": _results() if results is None else results}
    if diff:
        fields["diff"] = {"unattributed": [], "engine_state_changed": False, **diff}
    return fields


def _live(repo, commit):
    """A `LiveWorld` over a planted repository, with the static inputs of the green world."""
    green = green_world()
    world = slice_b.LiveWorld(commit, repo.path)
    world.labels = green.labels
    world.nodes = green.nodes
    return world


@pytest.fixture
def lenient_cm6(monkeypatch):
    """Both role-2 pairs admissible (the sha is an ancestor of the anchor): CM-6 itself makes a
    re-run's older pair inadmissible, and the resolver must still return the newer by ancestry."""
    monkeypatch.setattr(record_mod, "is_admissible", ph.ancestry_only)


def _verdict_c(repo, commit):
    return slice_b.verdict_c(_live(repo, commit))


def test_condition_c_selects_newest_admissible_pair(tmp_path, lenient_cm6, monkeypatch):
    clean = _pair_fields()
    dirty = _pair_fields(unattributed=["c1"])
    failed_node = _pair_fields(
        _results(extra=[{"nodeid": "t::gated", "outcome": "FAILED", "labels": []}])
    )
    unregistered_skip = _pair_fields(
        _results(extra=[{"nodeid": "t::skipped", "outcome": "SKIPPED", "labels": ["NOT-A-LABEL"]}])
    )
    # the newer pair (the failure-exit re-run) decides: clean over an older dirty one passes
    repo, h = ph.history(tmp_path, "-newer-clean", older=dirty, newer=clean)
    assert _verdict_c(repo, h["p2"])[0]
    assert _verdict_c(repo, h["k"])[0]  # at the re-run's carrier (B5-2)
    # a first attempt's clean pair never excuses a dirty newer one, whatever the defect is
    for name, newer in {
        "unattributed": dirty,
        "failed": failed_node,
        "skipped": unregistered_skip,
        "engine": _pair_fields(engine_state_changed=True),
    }.items():
        repo, h = ph.history(tmp_path, f"-newer-{name}", older=clean, newer=newer)
        for anchor in (h["p2"], h["k"]):
            ok, reason = _verdict_c(repo, anchor)
            assert not ok, name
        assert {
            "unattributed": "unattributed",
            "failed": "pass set",
            "skipped": "pass set",
            "engine": "engine_state_changed",
        }[name] in reason

    # a re-run PR that fixes a fossil in its role-1 commit judges the re-run's pair at its carrier
    repo, h = ph.history(tmp_path, "-rerun", older=clean, newer=clean)
    assert slice_b.LiveWorld(h["k"], repo.path).host_proc["sha"] == h["r2"]

    # a triage record admissible at the anchor is never read: a dirty record at a later product
    # commit is not a role-1 run record, so the tag anchor's clean pair is judged instead
    monkeypatch.undo()
    repo, h = ph.history(tmp_path, "-triage", older=clean, newer=clean)
    triage = repo.product("3")
    repo.records(triage, proc={}, docker=dirty)
    ok, reason = _verdict_c(repo, "HEAD")
    assert ok, reason


def test_condition_c_judges_through_p0_pass_set_not_a_copy(tmp_path, monkeypatch):
    repo, h = ph.history(tmp_path, "-p0", older=_pair_fields(), newer=_pair_fields())
    assert _verdict_c(repo, h["p2"])[0]
    monkeypatch.setattr(
        record_mod, "pass_set_violations", lambda record, labels=None: ["planted violation"]
    )
    ok, reason = _verdict_c(repo, h["p2"])
    assert not ok and "planted violation" in reason


def test_condition_c_decided_at_pr_head_under_preview(tmp_path, monkeypatch, capsys):
    """B6-3: on the PR head H, `--preview` and `--dry` decide (c): a pair whose host-docker record
    omits the legacy live node (resp. a docker_host node) fails at anchor H, never pending; a
    complete clean pair passes at H and at the planted landing merge commit alike."""
    incomplete = {
        "legacy node": _pair_fields(_results(legacy=False)),
        "docker_host node": _pair_fields(_results(host=False)),
    }

    def run(repo, head, *, dry):
        monkeypatch.setattr(meta_mod, "ROOT", repo.path)
        monkeypatch.setattr(slice_b, "make_world", lambda commit: _live(repo, commit))
        args = type("A", (), {"name": "slice-b", "dry": dry, "preview": not dry, "commit": head})()
        rc = meta_mod.cmd_ckpt(args)
        return rc, capsys.readouterr().out

    for name, docker in incomplete.items():
        repo, h = ph.history(tmp_path, f"-h-{name[:3]}", older=docker, newer=docker)
        rc, out = run(repo, h["p2"], dry=False)
        assert rc == 1 and "J-SLICE-B-c" in out and "results omit" in out, (name, out)
        assert f"anchor {h['p2'][:12]}" in out
        _rc, out = run(repo, h["p2"], dry=True)
        assert "pending:J-SLICE-B-c" in out

    repo, h = ph.history(tmp_path, "-h-clean", older=_pair_fields(), newer=_pair_fields())
    merge = ph.land(repo, h["p2"], h["x"])
    for commit in (h["p2"], merge):
        ok, reason = _verdict_c(repo, commit)
        assert ok, (commit, reason)
    _rc, out = run(repo, h["p2"], dry=True)
    assert "pending:J-SLICE-B-c" not in out
    assert slice_b.LiveWorld(merge, repo.path).host_proc["sha"] == h["r2"]


# ---- (i) column B --------------------------------------------------------------------------------


@pytest.mark.proves("WR-PROOF-1", "WR-PROOF-1:column-B", "B", "B", "LOGIC", "CI")
def test_column_b_condition_fails_on_planted_defect(world):
    """Over a planted ledger and planted records, (i) fails for a B cell with one UNPROVEN clause,
    for a DOCKER-tier B clause evidenced only at CI, and for a clause whose host record is
    inadmissible (CM-6); it passes when every cell is green or STUB-PROVEN with its label."""
    assert slice_b.verdict_i(world["w"])[0]
    assert len(slice_b.column_b_cells(world["w"].clauses)) == 9  # B1..B9

    # a cell with one UNPROVEN clause
    w = green_world()
    w.report["B2.1"]["status"] = "UNPROVEN"
    ok, reason = slice_b.verdict_i(w)
    assert not ok and "B2.1" in reason

    # a DOCKER-tier clause evidenced only at CI: no host-docker record, or one that omits its node
    w = green_world()
    w.host_docker = None
    ok, reason = slice_b.verdict_i(w)
    assert not ok and "B1.1" in reason and "DOCKER-tier" in reason
    w = green_world()
    w.host_docker["results"] = [w.host_docker["results"][1]]
    ok, reason = slice_b.verdict_i(w)
    assert not ok and "B1.1" in reason and "DOCKER-tier" in reason

    # a clause whose host record is inadmissible (CM-6)
    w = green_world()
    w.admissible = lambda record: (False, "a product path changed since its sha")
    ok, reason = slice_b.verdict_i(w)
    assert not ok and "B1.1" in reason and "no admissible host-docker record" in reason

    # a B4.6 that lost its STUB label is not STUB-PROVEN
    w = green_world()
    next(n for n in w.nodes if "B4.6" in n["labels"])["labels"].remove(STUB_LABEL)
    ok, reason = slice_b.verdict_i(w)
    assert not ok and "B4.6" in reason

    # a cell with no clause has no n/a reason in the map: not green
    w = green_world()
    w.clauses = [
        *[c for c in w.clauses if not c["cell"].startswith("one_terminal_answer")],
        {"id": "A1.1", "cell": "one_terminal_answer:A"},
    ]
    ok, reason = slice_b.verdict_i(w)
    assert not ok and "one_terminal_answer:B" in reason and "no Slice B clause" in reason

    # an empty map
    assert not slice_b.verdict_i(slice_b.World())[0]

    # green: every cell PROVEN or STUB-PROVEN with its label (B4.6), including through the world
    # the module builds: the condition reads only admissible records
    w = green_world()
    ok, reason = slice_b.verdict_i(w)
    assert ok, reason
    assert slice_b.render_status(w, "B4.6") == slice_b.STUB_PROVEN


# ---- the part-marker preflight of (a) ---------------------------------------------------------


def test_a_part_no_marker_names_is_listed_before_any_carrier(world):
    """J-SINGLE-R2 rule 2: a B clause part that no collected non-twin node names in a `proves()`
    marker is reported by (a) itself, whatever the ledger says, so `--dry` finds it before a carrier
    is spent."""
    w = world["w"]
    assert failing() == {}
    w.nodes = [n for n in w.nodes if "B4.3" not in n["labels"]]
    got = failing()
    assert "J-SLICE-B-a:B4.3" in got and "names it in a proves() marker" in got["J-SLICE-B-a:B4.3"]
    assert "J-SLICE-B-i" in got  # column B reads the same evidence
    # a row label that only composes the part is not the part id
    w.nodes.append({"nodeid": "t::composes", "labels": [CI_LABEL], "docker_host": False})
    assert "J-SLICE-B-a:B4.3" in failing()
    # a twin never counts as naming a part (CSC-8, `twin_audit`)
    twin = {
        "nodeid": "packages/trestle-env/tests/twin/test_b43_twin.py::t",
        "labels": ["B4.3", STUB_LABEL + SUFFIX],
        "docker_host": False,
    }
    w.nodes.append(twin)
    assert "J-SLICE-B-a:B4.3" in failing()
    assert slice_b.part_registrants(w, "B4.3") == []
    # a non-twin node naming it restores the part
    w.nodes.append({"nodeid": "t::b43", "labels": ["B4.3", CI_LABEL], "docker_host": False})
    assert "J-SLICE-B-a:B4.3" not in failing()
    assert [n["nodeid"] for n in slice_b.part_registrants(w, "B4.3")] == ["t::b43"]


# ---- (a): a HOST key is PROVEN@HOST through the admissible record, never the CI ledger -----------

HOST_PROC_NODE = "packages/trestle-env/tests/proc/test_x.py::test_p"
DOCKER_NODE_B83 = "packages/trestle-env/tests/host/test_y.py::test_b"


def _host_keys_world():
    """The green world with every HOST key absent from the ledger, as in the ckpt job (CI
    deselects docker_host / host_only nodes): B1.1 on the docker_host node only (its CI registrant
    dropped), B8.3 on a docker_host node that declares no label (its marker says DOCKER · HOST),
    B4.1 on a node whose marker says PROC · BOTH, and B9.2 on a host_only node (PROC · HOST)."""
    w = green_world()
    for clause in ("B1.1", "B8.3", "B4.1", "B9.2"):
        w.nodes = [n for n in w.nodes if n["nodeid"] != f"t::{clause}"]
    w.nodes.append(
        {
            "nodeid": DOCKER_NODE_B83,
            "labels": ["B8.3"],
            "docker_host": True,
            "marked": {"B8.3": [["DOCKER+PROC+STUB", "HOST"]]},
        }
    )
    w.nodes.append(
        {
            "nodeid": "packages/trestle-env/tests/twin/test_y_twin.py::test_b",
            "labels": [],
            "docker_host": False,
        }
    )
    w.nodes.append(
        {
            "nodeid": HOST_PROC_NODE,
            "labels": ["B4.1"],
            "docker_host": False,
            "marked": {"B4.1": [["PROC", "BOTH"]]},
        }
    )
    w.nodes.append(
        {
            "nodeid": "packages/trestle-env/tests/proc/test_z.py::test_q",
            "labels": ["B9.2"],
            "docker_host": False,
            "host_only": True,
            "marked": {"B9.2": [["PROC", "HOST"]]},
        }
    )
    w.host_docker["results"].append(
        {"nodeid": DOCKER_NODE_B83, "outcome": "PASSED", "labels": ["B8.3"]}
    )
    w.host_proc["results"] = [
        {"nodeid": HOST_PROC_NODE, "outcome": "PASSED", "labels": ["B4.1"]},
        {"nodeid": w.nodes[-1]["nodeid"], "outcome": "PASSED", "labels": ["B9.2"]},
    ]
    for key in ("B1.1", "B8.3", "B9.2", DOCKER_LABEL):
        del w.report[key]
    return w


@pytest.mark.proves("WR-PROOF-6", "WR-PROOF-6:ckpt-requires-clean-records", "B", "B", "LOGIC", "CI")
def test_host_keys_proven_at_host_through_the_admissible_record_alone(world):
    """CI deselects docker_host / host_only nodes, so the ckpt job's ledger never holds a HOST key:
    a DOCKER or HOST key is PROVEN@HOST from the committed role-2 record alone (the slice-a rule);
    a BOTH key needs the ledger too; nothing is PROVEN@HOST from a record that omits the key, has
    it failed, or is inadmissible for the anchor."""
    w = _host_keys_world()
    world["w"] = w
    assert failing() == {}
    for key in ("B1.1", "B8.3", "B9.2", DOCKER_LABEL, "B4.1"):
        assert slice_b.render_status(w, key) == slice_b.PROVEN_HOST, key

    # a docker_host registrant makes the clause DOCKER whatever its labels say
    w = _host_keys_world()
    w.host_docker["results"] = [r for r in w.host_docker["results"] if "B8.3" not in r["labels"]]
    world["w"] = w
    assert "B8.3 is DOCKER-tier: not PASSED" in failing()["J-SLICE-B-a:B8.3"]

    # a venue-HOST marker reads the host-proc record: omitted, or not PASSED
    w = _host_keys_world()
    w.host_proc["results"][1]["outcome"] = "SKIPPED"
    world["w"] = w
    got = failing()
    assert (
        "B9.2 is venue HOST: not PASSED in the admissible host-proc record"
        in got["J-SLICE-B-a:B9.2"]
    )
    assert "J-SLICE-B-i" in got

    # venue BOTH: the record alone is not enough, the CI half must be PROVEN in the ledger ...
    w = _host_keys_world()
    w.report["B4.1"]["status"] = "UNPROVEN"
    world["w"] = w
    assert "B4.1 is not PROVEN in the ledger" in failing()["J-SLICE-B-a:B4.1"]
    # ... and the ledger alone is not enough either
    w = _host_keys_world()
    w.host_proc["results"] = w.host_proc["results"][1:]
    world["w"] = w
    assert "B4.1 is venue HOST" in failing()["J-SLICE-B-a:B4.1"]

    # an inadmissible record is no evidence for any HOST key
    w = _host_keys_world()
    w.admissible = lambda record: (False, "a product path changed since its sha")
    world["w"] = w
    got = failing()
    for clause in ("B1.1", "B8.3", "B9.2", "B4.1"):
        assert "not admissible for the anchor" in got[f"J-SLICE-B-a:{clause}"], clause
    assert "not admissible for the anchor" in got["J-SLICE-B-a:labels"]

    # no record at all: CI-only evidence never makes a HOST key PROVEN
    w = _host_keys_world()
    w.host_docker = None
    w.host_proc = None
    world["w"] = w
    got = failing()
    assert "no admissible host-docker record" in got["J-SLICE-B-a:B8.3"]
    assert "no admissible host-proc record" in got["J-SLICE-B-a:B9.2"]


def test_collector_records_each_markers_tier_and_venue():
    """The node collector keeps the `[tier, venue]` of each `proves()` marker per id: a clause
    part's venue lives only in its marker (no label declares it)."""
    from tests.proof.b import twin_audit

    def mark(*args, **kwargs):
        return pytest.mark.proves(*args, **kwargs).mark

    class Item:
        def __init__(self, marks):
            self._marks = marks

        def iter_markers(self, name=None):
            return iter(self._marks)

    item = Item(
        [
            mark("WR-OWN-9", "B8.3", "B", "B", "DOCKER+PROC+STUB", "HOST"),
            mark("WR-VERIFY-2", "B4.1", "B", "B", "PROC", "BOTH"),
            mark("WR-VERIFY-2", "B4.1", "B", "B", "PROC", "BOTH"),
            mark(row="WR-ENV-1", tier="LOGIC", venue="CI"),
        ]
    )
    assert twin_audit.marked(item) == {
        "B8.3": [["DOCKER+PROC+STUB", "HOST"]],
        "B4.1": [["PROC", "BOTH"]],
        "WR-ENV-1": [["LOGIC", "CI"]],
    }
