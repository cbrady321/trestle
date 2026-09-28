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
