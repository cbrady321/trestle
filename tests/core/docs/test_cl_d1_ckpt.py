"""L.CL-D1.4: planted self-tests of `tests/proof/ckpt/core.py` over synthetic worlds.

The live conditions run only under `meta ckpt core` (DM-80). Here every condition reads a synthetic
`World` through the module's `make_world` seam, so each planted failure and pass stays true at every
later head (DM-11).
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tests.proof import ckpt as ckpt_mod
from tests.proof import meta as meta_mod
from tests.proof.ckpt import core
from tests.proof.host import record as record_mod

CLAIM = core.CLAIM_LABEL
BOTH_LABEL = "WR-OWN-6:both-label"
VARIANT_NODES = {
    "TM-C4b": ("tests/core/contract/test_ck34_records.py::test_variant_refuse", "WR-PLAN-9"),
}
STRADDLE_NODE = "tests/pins/a_lifecycle/test_straddle.py::test_target_s0_straddle"

DECLINES = {
    "CK-8": {
        "merge": "CK-8",
        "k": "K-8",
        "switch": {
            "module": "trestle.server.conductor",
            "name": "REAP_ON_SUCCESS",
            "declined": False,
        },
        "labels": [
            "WR-PROOF-10:K-8",
            "WR-OWN-3:success-no-survivor",
            "WR-CANCEL-2:success-no-bytes-after-finalization",
        ],
        "clauses": ["A8.2"],
    },
    "CK-14": {
        "merge": "CK-14",
        "k": "K-14",
        "switch": {
            "command": {"from": "mypy", "to": "python -m tests.proof.meta mypy-ratchet --max 1"}
        },
        "restores": ["TM-P0-1"],
        "labels": ["WR-PROOF-10:K-14", "WR-PROOF-8:mypy-zero-3.12"],
    },
    "CK-1": {
        "merge": "CK-1",
        "k": "K-1",
        "switch": {
            "module": "trestle.server.admission",
            "name": "JOIN_ACROSS_REPUBLISH",
            "declined": False,
        },
        "labels": [
            "WR-PROOF-10:K-1",
            "WR-IDEM-1:join-after-republish",
            "WR-IDEM-1:window-covers-run-life",
            "WR-IDEM-1:answer-names-identity",
        ],
        "clauses": ["A3.1"],
    },
    "CK-3/4": {
        "merge": "CK-3/4",
        "k": ["K-3", "K-4"],
        "switch": {"module": "trestle.plugin._codec", "name": "TYPED_RECORDS", "declined": False},
        "restores": ["T-3"],
        "labels": [
            "WR-PLAN-9:dataclass-arg-typed",
            "WR-PLAN-9:dataclass-return-succeeds",
            "WR-PROOF-10:K-3",
            "WR-PROOF-10:K-4",
        ],
    },
}
DECLINE_ONLY_ROWS = {"WR-IDEM-1", "WR-PLAN-9"}  # rows whose only claims are a CK item's


def _label(label_id: str, *, venue: str = "CI", posture: str = "claim") -> dict:
    return {
        "id": label_id,
        "row": label_id.split(":")[0],
        "step": "core",
        "slice": "core",
        "tier": "PROC",
        "venue": venue,
        "posture": posture,
        "declared_by": "planted",
    }


def _record(key: str) -> dict:
    return {
        "nodeid": f"t::{key}",
        "outcome": "passed",
        "gate": "ci-test",
        "venue": "CI",
        "interpreter": "3.12.9",
        "labels": [key],
    }


def green_world() -> core.World:
    """A synthetic world every condition passes (no CK item declined)."""
    labels = [_label(f"{row}:base") for row in core.CORE_ROWS if row not in DECLINE_ONLY_ROWS]
    for decline in DECLINES.values():
        labels += [_label(label_id) for label_id in decline["labels"]]
    labels += [
        _label(CLAIM, venue="BOTH"),
        _label(BOTH_LABEL, venue="BOTH"),
        _label("WR-PLAN-9:variant-refuse-written", posture="shape"),
    ]
    parts_clauses = [{"id": "A1.1", "parts": [{"name": "core"}], "rows": []}]
    parts_clauses += [{"id": "A1.3", "parts": [{"name": "core"}, {"name": "single"}], "rows": []}]
    parts_clauses += [{"id": "A6.1", "parts": [{"name": "core"}], "rows": []}]
    parts_clauses += [{"id": "A6.2", "parts": [{"name": "core"}], "rows": []}]
    for clause_id in (
        "A1.4",
        "A2.2",
        "A3.1",
        "A5.1",
        "A5.2",
        "A8.2",
        "A9.1",
        "A9.2",
        "A9.3",
        "A9.4",
    ):
        parts_clauses.append({"id": clause_id, "step": "core", "rows": []})
    keys = (
        set(core.CORE_PARTS)
        | {lb["id"] for lb in labels if lb["posture"] == "claim"}
        | {f"review:{rv}" for rv in core.REVIEWS}
    )
    report = {k: {"status": "PROVEN", "corroborating_314": False, "n_results": 1} for k in keys}
    both = core.BOTH_PARTS + [CLAIM, BOTH_LABEL]
    nodes = [{"nodeid": STRADDLE_NODE, "labels": [CLAIM], "gap": "G-A3", "strict_xfail": False}]
    for _entry, (nodeid, row) in VARIANT_NODES.items():
        label = f"{row}:variant-refuse-written"
        nodes.append({"nodeid": nodeid, "labels": [label], "gap": None, "strict_xfail": True})
    run_outcomes = {nodeid: "xfailed" for nodeid, _row in VARIANT_NODES.values()}
    return core.World(
        report=report,
        records=[_record(k) for k in keys],
        labels=labels,
        clauses=parts_clauses,
        row_owners={row: core.P0_OWNER_NODE for row in core.P0_OWNED_ROWS},
        deferrals=[
            {
                "label": "WR-OWN-8:environment-lease",
                "from_step": "core",
                "closes_at": "SL-8",
                "citation": "design §8.2",
                "declared_by": "L.CL-D1.3",
            }
        ],
        core_merges={
            "CS-1",
            "CS-2",
            "CK-8",
            "CS-3",
            "CS-4",
            "CK-14",
            "CL-D1",
            "CL-A1",
            "CL-C1",
            "CK-1",
            "CL-C2",
            "CK-3/4",
            "CL-B2",
            "CL-B1",
            "CL-B3",
            "CL-A2",
            "CL-PX2",
            "J-CORE",
        },  # fmt: skip
        declines=copy.deepcopy(DECLINES),
        declined=set(),
        host_record={
            "status": "PASSED",
            "results": [{"nodeid": f"t::{k}", "outcome": "PASSED", "labels": [k]} for k in both],
        },
        target_audit={"nodes": [], "outcomes": {}},
        gap_entries={gap: "passed" for gap in core.CORE_GAPS},
        nodes=nodes,
        straddle_nodeids={STRADDLE_NODE},
        run_nodes=lambda ids: {i: run_outcomes.get(i, "passed") for i in ids},
        register={
            "TM-C1": {"present": True, "removed_by": "L.SV-3.6"},
            "TM-C2": {"present": True, "removed_by": "L.SV-3.6"},
            "TM-C3": {"present": True, "removed_by": "L.TR-6.8"},
            **{i: {"present": False, "removed_by": "L.X"} for i in core.REGISTER_ABSENT},
        },
        reviews={
            rv: {
                "record": {"outcome": "pass", "sha": "abc1234"},
                "error": None,
                "ancestor": True,
                "shown": True,
            }
            for rv in core.REVIEWS
        },
        divergence=[{"id": "K-3", "facet": "internal", "row_or_k": "K-3"}],
    )


def decline(w: core.World, merge: str, *, restored: bool = True) -> core.World:
    """`w` after `merge`'s decline patch: its switch declined, its labels na, its entry restored,
    its clauses and labels no longer rendered PROVEN."""
    d = w.declines[merge]
    k = d["k"] if isinstance(d["k"], str) else d["k"][0]
    w.declined.add(merge)
    for label in w.labels:
        if label["id"] in d.get("labels", []):
            label["posture"], label["reason"] = "na", f"{k} declined"
    for key in list(d.get("labels", [])) + list(d.get("clauses", [])):
        w.report.pop(key, None)
    if restored:
        for entry_id in d.get("restores", []):
            w.register[entry_id] = {
                "present": True,
                "removed_by": core.NAMED_NOT_REMOVED,
                "citation": f"{k} declined",
                "serves": [],
            }
    return w


def _digest(report: dict) -> str:
    return hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch):
    """Install a synthetic world as the module's `make_world` (and its ledger as the digest's
    source); the test edits `holder["w"]`."""
    holder = {"w": green_world()}
    monkeypatch.setattr(core, "make_world", lambda commit: holder["w"])
    monkeypatch.setattr(ckpt_mod, "ledger_digest", lambda report=None: _digest(holder["w"].report))
    return holder


def _ckpt(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = meta_mod.main(["ckpt", "core", *argv])
    return code, capsys.readouterr().out


# ---------------------------------------------------------------------------
# planted failures: each makes `meta ckpt core` exit non-zero with that reason
# ---------------------------------------------------------------------------


def _unflipped_target(w: core.World) -> None:
    node = "tests/pins/a_lifecycle/test_g_a1.py::test_target_no_attributable_survivor"
    w.target_audit = {
        "nodes": [{"nodeid": node, "gap": "G-A1", "strict_xfail": True}],
        "outcomes": {node: {"outcome": "failed"}},
    }


def _uncited_deferral(w: core.World) -> None:
    w.deferrals.append(
        {"label": "WR-X-1:uncited", "from_step": "core", "closes_at": "SL-9", "citation": " "}
    )


def _closes_in_core_band(w: core.World) -> None:
    w.deferrals.append(
        {
            "label": "WR-OWN-8:closes-in-core",
            "from_step": "core",
            "closes_at": "CS-4",
            "citation": "planted",
            "declared_by": "L.CL-D1.3",
        }
    )


def _label_neither_proven_nor_deferred(w: core.World) -> None:
    w.labels.append(_label("WR-OWN-5:orphan"))


def _row_without_claim(w: core.World) -> None:
    w.labels = [lb for lb in w.labels if lb["row"] != "WR-EVID-9"]


def _withdrawn_label_registered(w: core.World) -> None:
    w.nodes.append(
        {"nodeid": "tests/x.py::test_x", "labels": [core.WITHDRAWN_LABEL], "strict_xfail": False}
    )


def _straddle_node_registers_identity_label(w: core.World) -> None:
    label = "WR-CANCEL-3:process-alive-after-recovery"
    w.nodes.append({"nodeid": "tests/y.py::test_uses_straddle", "labels": [CLAIM, label]})
    w.straddle_nodeids.add("tests/y.py::test_uses_straddle")


def _missing_rv5(w: core.World) -> None:
    del w.reviews["RV-5"]


def _declined_item_rendered_proven(w: core.World) -> None:
    decline(w, "CK-8")
    w.report["WR-PROOF-10:K-8"] = {"status": "PROVEN", "corroborating_314": False, "n_results": 1}


def _t3_present_without_decline(w: core.World) -> None:
    w.register["T-3"] = {"present": True, "removed_by": "L.CK-3/4.3"}


def _host_record_lacks_both_clause(w: core.World) -> None:
    assert w.host_record is not None
    w.host_record["results"] = [
        r for r in w.host_record["results"] if "A6.2:core" not in r["labels"]
    ]


PLANTED = [
    ("unflipped defect:G-A1 target", _unflipped_target, "J-CORE-d", "G-A1"),
    ("uncited deferral", _uncited_deferral, "J-CORE-c", "WR-X-1:uncited: no citation"),
    (
        "closes_at in the core band, not PROVEN",
        _closes_in_core_band,
        "J-CORE-c",
        "WR-OWN-8:closes-in-core",
    ),
    (
        "core label neither PROVEN nor deferred",
        _label_neither_proven_nor_deferred,
        "J-CORE-c",
        "WR-OWN-5:orphan",
    ),
    ("core row with no claim", _row_without_claim, "J-CORE-b", "core row WR-EVID-9"),
    ("withdrawn CSC-14 gated label", _withdrawn_label_registered, "J-CORE-e", core.WITHDRAWN_LABEL),
    (
        "S node registering an identity-run label",
        _straddle_node_registers_identity_label,
        "J-CORE-e",
        "process-alive-after-recovery",
    ),
    ("missing RV-5", _missing_rv5, "J-CORE-i", "RV-5-core.toml is absent"),
    (
        "declined CK item rendered PROVEN",
        _declined_item_rendered_proven,
        "J-CORE-k",
        "renders PROVEN, not na",
    ),
    (
        "T-3 present while K-3 is not declined",
        _t3_present_without_decline,
        "J-CORE-f",
        "T-3 is still present",
    ),
    (
        "BOTH clause absent from the host record",
        _host_record_lacks_both_clause,
        "J-CORE-a",
        "A6.2:core",
    ),
]


@pytest.mark.parametrize(("name", "plant", "cond", "reason"), PLANTED, ids=[p[0] for p in PLANTED])
def test_ckpt_core_blocks_each_planted_failure(name, plant, cond, reason, world, capsys) -> None:
    plant(world["w"])
    code, out = _ckpt(["--preview"], capsys)
    assert code == 1, out
    line = next(ln for ln in out.splitlines() if f": {cond}:" in ln)
    assert reason in line, line


def test_stale_host_record_blocks_a_both_clause(tmp_path: Path, world, capsys) -> None:
    """A record whose sha precedes a non-record change is inadmissible (CM-6), so `select`
    returns none and (a) reports it; a record-only change after the sha leaves it admissible."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "code.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    sha = git("rev-parse", "HEAD")
    host = repo / "tests" / "proof" / "host" / "host-proc"
    host.mkdir(parents=True)
    (host / f"{sha}.json").write_text(json.dumps({"gate": "host-proc", "sha": sha}))
    git("add", "-A")
    git("commit", "-q", "-m", "record only")
    assert record_mod.select("host-proc", "HEAD", cwd=repo) is not None
    (repo / "code.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-q", "-m", "a non-record path changed after the record's sha")
    stale = record_mod.select("host-proc", "HEAD", cwd=repo)
    assert stale is None

    world["w"].host_record = stale
    code, out = _ckpt(["--preview"], capsys)
    assert code == 1
    assert "no admissible host-proc record" in out


# ---------------------------------------------------------------------------
# planted passes
# ---------------------------------------------------------------------------


def test_all_green_synthetic_ledger_exits_zero_with_stable_digest(world, capsys) -> None:
    code, out = _ckpt(["--preview"], capsys)
    assert code == 0, out
    assert "pass; digest=" in out
    code2, out2 = _ckpt(["--preview"], capsys)
    assert (code2, out2) == (code, out)
    assert out.strip().endswith(_digest(world["w"].report))


def test_straddle_set_with_only_the_claim_label_passes_e(world) -> None:
    w = world["w"]
    assert w.straddle_nodeids == {STRADDLE_NODE}
    assert core.verdict_e(w) == (True, "")


def test_declined_k3_path_passes_a_b_d_f_j_k(world) -> None:
    """K-3 declined on HEAD: its switch at the declined value, T-3 present again, G-B2's dataclass
    target and the CK-3/4 labels na, a d1 complaint that is only K-3's divergence; WR-PLAN-9 has
    no claimed label (its variant label is `shape`); A9.3 stays PROVEN."""
    w = decline(world["w"], "CK-3/4")
    w.target_audit = {
        "nodes": [
            {"nodeid": "tests/pins/b_contract/test_g_b2.py::t", "gap": "G-B2", "strict_xfail": True}
        ],
        "outcomes": {"tests/pins/b_contract/test_g_b2.py::t": {"outcome": "failed"}},
    }
    w.gap_entries["G-B2"] = "failed"
    w.d1 = (1, "d1: MISSING DUE DIFF: divergence entry K-3 (internal)")
    assert core.excused(w).labels >= set(DECLINES["CK-3/4"]["labels"])
    for verdict in (core.verdict_a, core.verdict_b, core.verdict_c, core.verdict_d, core.verdict_f):
        assert verdict(w) == (True, ""), verdict.__name__
    for verdict in (core.verdict_g, core.verdict_h, core.verdict_j, core.verdict_k):
        assert verdict(w) == (True, ""), verdict.__name__
    assert core.excused(w).ks == {"K-3", "K-4"}
    assert "WR-PLAN-9" in core.CORE_ROWS
    assert core.row_claims(w, "WR-PLAN-9")[0] == []


def test_a_variant_that_runs_unxfailed_fails_g(world) -> None:
    w = world["w"]
    node = VARIANT_NODES["TM-C4b"][0]
    w.run_nodes = lambda ids: {i: "xfailed" for i in ids}
    assert core.verdict_g(w) == (True, "")
    w.run_nodes = lambda ids: {i: "passed" if i == node else "xfailed" for i in ids}
    ok, reason = core.verdict_g(w)
    assert not ok and "TM-C4b" in reason


def test_na_without_the_decline_patch_on_head_fails_k(world) -> None:
    w = world["w"]
    for label in w.labels:
        if label["id"] == "WR-PROOF-10:K-1":
            label["posture"], label["reason"] = "na", "K-1 declined"
    ok, reason = core.verdict_k(w)
    assert not ok and "decline patch is not on HEAD" in reason


def _history(tmp_path: Path, subjects: list[str]) -> tuple[Path, list[str]]:
    """A throwaway repo with one empty commit per subject; returns it and the shas, oldest first.
    The carrier question is asked of these commits, never of the checkout's own HEAD, which on a
    `J-CORE` carrier's own CI run IS the carrier (DM-11: the answer must hold at every head)."""
    repo = tmp_path / "carrier-repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q", "-b", "master")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    shas = []
    for subject in subjects:
        git("commit", "-q", "--allow-empty", "-m", subject)
        shas.append(git("rev-parse", "HEAD"))
    return repo, shas


NON_CARRIERS = {
    # no J-CORE carrier in history at all
    "no-carrier": (["WR-Merge: CL-PX2"], -1),
    # a later master commit after the carrier (the carrier is reachable, not HEAD)
    "after-carrier": (["WR-Merge: CL-PX2", "WR-Merge: J-CORE", "WR-Fix: CS-1"], -1),
    # a WR-Fix naming the checkpoint is never a landing (CM-1)
    "wr-fix": (["WR-Merge: CL-PX2", "WR-Fix: J-CORE"], -1),
}


@pytest.mark.parametrize(("subjects", "pick"), NON_CARRIERS.values(), ids=NON_CARRIERS.keys())
def test_non_trigger_commit_is_not_evaluated(
    subjects, pick, tmp_path: Path, monkeypatch, capsys
) -> None:
    repo, shas = _history(tmp_path, subjects)

    def boom(commit: str):
        raise AssertionError("a non-carrier commit must not be evaluated")

    monkeypatch.setattr(core, "make_world", boom)
    monkeypatch.setattr(meta_mod, "ROOT", repo)
    code, out = _ckpt(["--commit", shas[pick]], capsys)
    assert code == 0, out
    assert "no-op" in out


@pytest.mark.parametrize("newest_of_two", [False, True], ids=["first-carrier", "newest-carrier"])
def test_carrier_commit_is_evaluated(
    newest_of_two: bool, tmp_path: Path, world, monkeypatch, capsys
) -> None:
    """The newest `WR-Merge: J-CORE` carrier is evaluated: the world is built and a green one
    passes with the ledger digest; a planted failure on the carrier exits 1."""
    subjects = ["WR-Merge: CL-PX2", "WR-Merge: J-CORE"]
    if newest_of_two:
        subjects += ["WR-Fix: CS-1", "WR-Merge: J-CORE"]
    repo, shas = _history(tmp_path, subjects)
    built: list[str] = []
    monkeypatch.setattr(core, "make_world", lambda commit: built.append(commit) or world["w"])
    monkeypatch.setattr(meta_mod, "ROOT", repo)

    code, out = _ckpt(["--commit", shas[-1]], capsys)
    assert code == 0, out
    assert out.strip().endswith(f"pass; digest={_digest(world['w'].report)}")
    assert built and set(built) == {shas[-1]}

    _unflipped_target(world["w"])
    code, out = _ckpt(["--commit", shas[-1]], capsys)
    assert code == 1, out
    assert "not the newest" not in out


# ---------------------------------------------------------------------------
# the live readers, on planted files
# ---------------------------------------------------------------------------


def test_switch_declined_reads_a_constant_and_a_command(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("SWITCH: bool = False\n")
    module = {"switch": {"module": "pkg.mod", "name": "SWITCH", "declined": False}}
    assert core.switch_declined(module, tmp_path)
    (tmp_path / "pkg" / "mod.py").write_text("SWITCH: bool = True\n")
    assert not core.switch_declined(module, tmp_path)

    ci = tmp_path / ".github" / "workflows"
    ci.mkdir(parents=True)
    command = {"switch": {"command": {"from": "mypy", "to": "ratchet --max 1"}}}
    (ci / "ci.yml").write_text("steps:\n  - run: mypy\n")
    assert not core.switch_declined(command, tmp_path)
    (ci / "ci.yml").write_text("steps:\n  - run: ratchet --max 1\n")
    assert core.switch_declined(command, tmp_path)


def test_straddle_set_is_found_by_function_body(tmp_path: Path) -> None:
    (tmp_path / "test_s.py").write_text(
        "def test_calls():\n    with spawn_s0_shaped_orphan(run_dir) as o:\n        pass\n\n"
        "def test_reads():\n    p = ROOT / 'tests/fixtures/fossils/s0/straddle'\n\n"
        "def test_plain():\n    assert True\n"
    )
    nodes = [{"nodeid": f"test_s.py::{n}"} for n in ("test_calls", "test_reads", "test_plain")]
    assert core.straddle_nodeids(nodes, tmp_path) == {
        "test_s.py::test_calls",
        "test_s.py::test_reads",
    }


def test_module_shape() -> None:
    assert core.TRIGGER_MERGE == "J-CORE" and core.TAG == "wr-ckpt/core"
    assert len(core.CORE_ROWS) + len(core.P0_OWNED_ROWS) == 49
    assert len(core.CORE_PARTS) == 14 and len(core.CORE_GAPS) == 18
    assert not any(c.merge_only for c in core.CONDITIONS)
    assert ckpt_mod.load_module("core") is core
