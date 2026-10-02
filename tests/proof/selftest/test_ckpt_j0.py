"""Selftest for `tests/proof/ckpt/j0.py` (L.P0-0d.8). Planted ledgers
only — j0.py's live conditions never run here."""

from __future__ import annotations

import os
import subprocess

import pytest

from tests.proof import fence as fence_mod
from tests.proof import ledger as ledger_mod
from tests.proof.ckpt import j0 as j0_mod


def _r(nodeid, gap, outcome, exc_type=None, unmet_gap=None):
    return j0_mod.AuditReport(nodeid, gap, outcome, exc_type, unmet_gap)


def test_planted_target_failing_by_importerror_rejected():
    reports = [_r("t::test_target_x", "G-A1", "xfailed", exc_type="ImportError")]
    ok, reason = j0_mod.audit_report(reports)
    assert not ok
    assert "ImportError" in reason


def test_planted_xpass_rejected():
    reports = [_r("t::test_target_x", "G-A1", "xpassed")]
    ok, reason = j0_mod.audit_report(reports)
    assert not ok
    assert "XPASS" in reason


def test_planted_missing_gap_rejected():
    reports = [_r("t::test_target_x", "G-A1", "xfailed", exc_type="TargetUnmet", unmet_gap="G-A2")]
    ok, reason = j0_mod.audit_report(reports)
    assert not ok
    assert "G-A2" in reason and "G-A1" in reason


def test_planted_gated_node_skipped_passes():
    reports = [
        _r("t::test_target_x", "G-A1", "xfailed", exc_type="TargetUnmet", unmet_gap="G-A1"),
        _r("t::test_target_gated", "G-B1", "skipped"),
    ]
    ok, _reason = j0_mod.audit_report(reports)
    assert ok


def test_planted_skip_without_reason_rejected():
    reports = [j0_mod.AuditReport("t::test_target_x", "G-B1", "skipped", None, None, False)]
    ok, reason = j0_mod.audit_report(reports)
    assert not ok
    assert "reason" in reason


def _node(nodeid, gap, has_reason=False):
    return {
        "nodeid": nodeid,
        "gap": gap,
        "labels": [],
        "strict_xfail": True,
        "has_reason": has_reason,
    }


def _out(outcome, exc=None, gap=None):
    return {"outcome": outcome, "exc_type": exc, "unmet_gap": gap}


def test_audit_run_reads_real_per_node_outcomes():
    data = {
        "nodes": [_node("a", "G-A1"), _node("b", "G-A2"), _node("plain", None)],
        "outcomes": {
            "a": _out("failed", "TargetUnmet", "G-A1"),
            "b": _out("failed", "TargetUnmet", "G-A2"),
            "plain": _out("passed"),
        },
    }
    assert j0_mod.audit_run(data, ["G-A1", "G-A2"]) == (True, "")


def test_audit_run_rejects_other_exception_pass_and_missing_gap():
    data = {
        "nodes": [_node("a", "G-A1"), _node("b", "G-A2"), _node("c", "G-A3")],
        "outcomes": {
            "a": _out("failed", "ImportError"),
            "b": _out("passed"),
            "c": _out("failed", "TargetUnmet", "G-A3"),
        },
    }
    ok, reason = j0_mod.audit_run(data, ["G-A1", "G-A2", "G-A3", "G-A4"])
    assert not ok
    assert "ImportError" in reason and "XPASS" in reason and "G-A4" in reason


def test_audit_run_node_with_no_outcome_fails():
    data = {"nodes": [_node("a", "G-A1")], "outcomes": {}}
    ok, _reason = j0_mod.audit_run(data, [])
    assert not ok


# --- the plan's J0-1..J0-12, planted data only ------------------------------


def test_conditions_are_exactly_the_plans_twelve():
    assert [c.id for c in j0_mod.CONDITIONS] == [f"J0-{n}" for n in range(1, 13)]
    assert j0_mod.TRIGGER_MERGE == "J0"
    assert j0_mod.TAG is None
    assert not any(c.merge_only for c in j0_mod.CONDITIONS)


ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo, *args):
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_j0_1_planted_product_change_rejected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "trestle").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "master")
    (repo / "trestle" / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "s0")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "notes.txt").write_text("outside the product paths\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "spine only")
    monkeypatch.setattr(j0_mod, "ROOT", repo)
    monkeypatch.setattr(j0_mod, "S0_BASE", base)
    assert j0_mod._identity_check("HEAD") == (True, "")  # not a carrier: no history yet
    (repo / "trestle" / "a.py").write_text("x = 2\n")
    _git(repo, "commit", "-q", "-am", "product edit")
    ok, reason = j0_mod._identity_check("HEAD")
    assert not ok and "P-ID" in reason


def _data(nodes, outcomes):
    return {"nodes": nodes, "outcomes": outcomes}


def _tn(nodeid, gap):
    return {"nodeid": nodeid, "gap": gap, "pin_gap": None}


def _pn(nodeid, gap):
    return {"nodeid": nodeid, "gap": None, "pin_gap": gap}


def test_j0_3_planted_xpass_missing_gap_and_bad_exit_rejected():
    required = ["G-A1", "G-A2"]
    good = _data(
        [_tn("a", "G-A1"), _tn("b", "G-A2")],
        {"a": _out("xfailed"), "b": _out("xfailed")},
    )
    assert j0_mod.target_xfail_verdict(good, required, 0, total_required=2) == (True, "")
    xpass = _data([_tn("a", "G-A1"), _tn("b", "G-A2")], {"a": _out("xfailed"), "b": _out("passed")})
    ok, reason = j0_mod.target_xfail_verdict(xpass, required, 1, total_required=2)
    assert not ok and "b: passed" in reason and "G-A2" in reason and "exit 1" in reason
    ok, reason = j0_mod.target_xfail_verdict(good, required, 0, total_required=20)
    assert not ok and "expected 20" in reason


def test_j0_4_planted_missing_pin_pending_and_gc4_rejected():
    gaps = [
        {"id": "G-A1", "target": "required", "entry": "m:f"},
        {"id": "G-C4", "target": "none(DM-20)", "entry": "m:g"},
    ]
    nodes = [_pn("p1", "G-A1"), _pn("p2", "G-C4")]
    passed = {"p1": _out("passed"), "p2": _out("passed")}
    assert j0_mod.pin_verdict(_data(nodes, passed), gaps, 0, total=2) == (True, "")
    ok, reason = j0_mod.pin_verdict(_data(nodes[:1], {"p1": _out("passed")}), gaps, 0, total=2)
    assert not ok and "no pin for ['G-C4']" in reason
    pending = [dict(gaps[0], entry="pending"), gaps[1]]
    ok, reason = j0_mod.pin_verdict(_data(nodes, passed), pending, 0, total=2)
    assert not ok and "pending" in reason
    wrong = [gaps[0], dict(gaps[1], target="required")]
    ok, reason = j0_mod.pin_verdict(_data(nodes, passed), wrong, 0, total=2)
    assert not ok and "G-C4" in reason


def _clause(cid, cell, **kw):
    return {"id": cid, "cell": cell, "stub_label_required": False, "adversary_stub": False, **kw}


def _matrix():
    clauses = [_clause(f"A1.{i}", f"c{i}:A") for i in range(1, 4)]
    clauses[0]["stub_label_required"] = True
    clauses += [_clause("B4.5", "c1:B", adversary_stub=True), _clause("B9.1", "c2:B")]
    clauses[-1]["adversary_stub"] = True
    return clauses


@pytest.fixture
def matrix_shape(monkeypatch):
    monkeypatch.setattr(j0_mod, "CLAUSES", 5)
    monkeypatch.setattr(j0_mod, "CELLS", 5)
    monkeypatch.setattr(j0_mod, "PARTS", 5)
    monkeypatch.setattr(j0_mod, "STUB_CLAUSES", 1)


def test_j0_6_planted_proven_clause_and_labelled_adversary_rejected(matrix_shape):
    unproven = {"A1.1:core": {"status": "UNPROVEN"}, "review:RV-5": {"status": "PROVEN"}}
    assert j0_mod.matrix_verdict(unproven, _matrix(), []) == (True, "")
    ok, reason = j0_mod.matrix_verdict({"A1.2:core": {"status": "PROVEN"}}, _matrix(), [])
    assert not ok and "['A1.2']" in reason
    ok, reason = j0_mod.matrix_verdict(unproven, _matrix(), [{"id": "L", "composes": "B4.5:B"}])
    assert not ok and "B4.5" in reason
    ok, reason = j0_mod.matrix_verdict(unproven, _matrix()[:-1], [])
    assert not ok and "clauses" in reason and "adversary" in reason


def test_j0_6_shape_counts_are_the_plans():
    assert (j0_mod.CELLS, j0_mod.CLAUSES, j0_mod.PARTS) == (18, 65, 69)
    assert (j0_mod.STUB_CLAUSES, j0_mod.ADVERSARY_CLAUSES) == (3, {"B4.5", "B9.1"})


def test_j0_7_planted_state_count_and_s0_exception_rejected():
    states = {f"s{i}": ("s0", {"producer": "m:f"}) for i in range(19)}
    assert j0_mod.d2_states_verdict(states, []) == (True, "")
    states["gone"] = ("s0", {"producer": "m:f", "absent": True})
    states["todo"] = ("s0", {"producer": "pending"})
    assert j0_mod.d2_states_verdict(states, [])[0]
    ok, reason = j0_mod.d2_states_verdict(dict(list(states.items())[:5]), [])
    assert not ok and "expected 19" in reason
    ok, reason = j0_mod.d2_states_verdict(states, [{"id": "X", "reader": "s0"}])
    assert not ok and "['X']" in reason
    assert j0_mod.d2_states_verdict(states, [{"id": "X", "reader": "v2"}])[0]


class _Proc:
    def __init__(self, rc, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def test_j0_5_8_9_follow_the_commands_exit_status(monkeypatch):
    calls = []

    def fake(rc_for):
        def run(argv, env=None):
            calls.append(argv)
            return _Proc(rc_for(argv), "boom")

        return run

    monkeypatch.setattr(j0_mod, "_run", fake(lambda argv: 0))
    assert j0_mod._selftest_drift_check("c")[0]
    assert j0_mod._baseline_check("c")[0]
    assert j0_mod._maps_check("c")[0]
    assert [a[-1] for a in calls[-4:]] == ["check-map", "audit-rows", "open-questions", "register"]
    monkeypatch.setattr(j0_mod, "_run", fake(lambda argv: 1))
    assert not j0_mod._selftest_drift_check("c")[0]
    assert not j0_mod._baseline_check("c")[0]
    ok, reason = j0_mod._maps_check("c")
    assert not ok and all(n in reason for n in ("check-map", "audit-rows", "register"))
    monkeypatch.setattr(j0_mod, "_run", fake(lambda argv: 0 if argv[-1] != "register" else 2))
    ok, reason = j0_mod._maps_check("c")
    assert not ok and "register" in reason and "check-map" not in reason


def test_j0_2_3_4_read_the_audit_and_a_missing_document_fails(monkeypatch):
    monkeypatch.setattr(j0_mod, "_audit", lambda args: (None, _Proc(1, "no doc")))
    for check in (j0_mod._run_red_reason_audit, j0_mod._target_xfail, j0_mod._pin_check):
        ok, reason = check("c")
        assert not ok and "no per-node" in reason


def _proc_record(results, status="PASSED"):
    return {"gate": "host-proc", "sha": "a" * 40, "status": status, "results": results}


def _res(nodeid, outcome="PASSED", labels=()):
    return {"nodeid": nodeid, "outcome": outcome, "labels": list(labels)}


def test_j0_10_planted_record_gaps_rejected():
    docker = {"gate": "host-docker", "mode": "preflight", "status": "PRECONDITION_UNMET"}
    full = _proc_record([_res("t::a"), _res("t::b")])
    assert j0_mod.host_record_verdict(full, ["t::a", "t::b"], {}, docker) == (True, "")
    ok, reason = j0_mod.host_record_verdict(None, [], {}, docker)
    assert not ok and "no admissible" in reason
    ok, reason = j0_mod.host_record_verdict(full, ["t::a", "t::c"], {}, docker)
    assert not ok and "t::c" in reason
    failing = _proc_record([_res("t::a", "FAILED")], status="FAILED")
    ok, reason = j0_mod.host_record_verdict(failing, ["t::a"], {}, docker)
    assert not ok and "FAILED" in reason
    skipped = _proc_record([_res("t::a", "SKIPPED", ["L"])])
    assert not j0_mod.host_record_verdict(skipped, ["t::a"], {}, docker)[0]
    gated = {"L": {"posture": "gated_on"}}
    assert j0_mod.host_record_verdict(skipped, ["t::a"], gated, docker)[0]
    ok, reason = j0_mod.host_record_verdict(full, ["t::a"], {}, None)
    assert not ok and "host-docker" in reason


RV = """\
id = "RV-5"
mode = "stage-critic review"
outcome = "pass"
sha = "3e82d1f"
transcribe_log = "ok"

[criteria]
verbatim_fragments = "pass"
row_set = "pass"
owner = "pass"
k_docs = "pass"
"""


def test_j0_11_planted_review_states(tmp_path):
    def verdict(text=None, ancestor=True, render=lambda: {"review:RV-5": {}}):
        d = tmp_path / "reviews"
        d.mkdir(exist_ok=True)
        f = d / j0_mod.REVIEW_FILE
        if text is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(text)
        return j0_mod.review_verdict(d, lambda sha: ancestor, render)

    assert verdict(RV) == (True, "")
    assert "absent" in verdict(None)[1]
    assert "outcome" in verdict(RV.replace('outcome = "pass"', 'outcome = "fail"'))[1]
    assert "mode" in verdict(RV.replace("stage-critic review", "human"))[1]
    assert "ancestor" in verdict(RV, ancestor=False)[1]
    assert "does not show" in verdict(RV, render=lambda: {})[1]

    def vacuous():
        raise ledger_mod.VacuousLedgerError("0 countable results")

    assert "ledger" in verdict(RV, render=vacuous)[1]


def test_j0_12_ckpt_needs_every_other_job():
    jobs = {
        "lint": {},
        "test": {},
        "ledger": {"needs": ["test"]},
        "ckpt": {"needs": ["lint", "ledger"]},
    }
    assert j0_mod.ci_needs_verdict(jobs) == (True, "")  # test is reached through ledger
    ok, reason = j0_mod.ci_needs_verdict({**jobs, "extra": {}})
    assert not ok and "extra" in reason
    assert not j0_mod.ci_needs_verdict({"lint": {}})[0]
    assert j0_mod.ci_needs_verdict({"a": {}, "ckpt": {"needs": "a"}})[0]


def test_j0_12_real_ci_yml_holds():
    assert j0_mod._ci_check("HEAD") == (True, ""), fence_mod.CI_YML_PATH


def test_roots_are_the_repository_root():
    from tests.proof import ckpt as ckpt_mod

    for root in (j0_mod.ROOT, ckpt_mod.ROOT):
        assert (root / "tests" / "proof" / "ckpt" / "j0.py").exists()
