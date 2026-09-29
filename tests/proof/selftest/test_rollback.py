"""Selftest for the rollback-boundary register (MC-31; L.P0-0d.1)."""

from __future__ import annotations

from tests.proof import register as register_mod


def test_cs1_sv3_preregistered():
    boundaries = register_mod.load_rollback()
    by_merge = {b["merge"]: b for b in boundaries}
    assert by_merge["CS-1"]["class"] == "forward_only"
    assert by_merge["SV-3"]["class"] == "drain"
    for b in boundaries:
        assert b["evidence"].startswith("pending:")
        assert b["authorized_by"]


def test_landed_boundary_without_evidence_fails():
    boundaries = [{"merge": "CS-1", "class": "forward_only", "evidence": "pending:CS-1"}]
    violations = register_mod.rollback_violations(boundaries, landed_check=lambda _m: True)
    assert violations
    assert "CS-1" in violations[0]

    violations = register_mod.rollback_violations(boundaries, landed_check=lambda _m: False)
    assert violations == []
