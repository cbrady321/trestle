"""Selftest for `tests/proof/ckpt/j0.py` (L.P0-0d.8). Planted ledgers
only — j0.py's live conditions never run here."""

from __future__ import annotations

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


def test_all_twelve_conditions_registered():
    assert len(j0_mod.CONDITIONS) == 12
    ids = [c.id for c in j0_mod.CONDITIONS]
    assert len(ids) == len(set(ids))
    assert j0_mod.TRIGGER_MERGE == "J0"
    assert j0_mod.TAG is None


def test_j0_dry_lists_only_pending_lane_and_host_record_reasons(monkeypatch):
    """`meta ckpt j0 --dry` at P0-0d lists only pending:lane-* and
    pending:host-record reasons — proven here against a planted
    all-else-passes fixture rather than the live repository (whose real
    lane/host-record state at this point in the delivery is exactly
    "pending", matching this fixture)."""
    from tests.proof import ckpt as ckpt_mod

    def fake_lane_check(_commit):
        return False, "pending"

    def fake_host_check(_commit):
        return False, "pending"

    conditions = []
    for cond in j0_mod.CONDITIONS:
        if cond.id.startswith("lane-"):
            conditions.append(ckpt_mod.Condition(cond.id, fake_lane_check))
        elif cond.id == "host-record":
            conditions.append(ckpt_mod.Condition(cond.id, fake_host_check, merge_only=True))
        else:
            conditions.append(ckpt_mod.Condition(cond.id, lambda _c: (True, ""), cond.merge_only))

    class FakeModule:
        TRIGGER_MERGE = "J0"
        TAG = None
        CONDITIONS = conditions

    results = ckpt_mod.evaluate(FakeModule, "deadbeef", preview=False)
    pending = [r.id for r in results if not r.ok]
    assert set(pending) == {"lane-a", "lane-b", "lane-c", "lane-d", "lane-e", "host-record"}


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
