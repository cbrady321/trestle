"""Lane-A goldens (L.P0-1A.7): the committed facet goldens are the S0
projection the fossil producers declare."""

from __future__ import annotations

import json

from tests.pins.a_lifecycle import fossil_producers as fp
from tests.pins.a_lifecycle import golden


def _golden(name: str) -> dict:
    return json.loads((golden.GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_fossil_projection_golden_agrees_with_declared_projection() -> None:
    projection = _golden("fossil_projection")
    for sid, declared in fp.DECLARED.items():
        seam = projection[sid]["seam"]
        assert seam["kinds"] == declared["kinds"], sid
        assert seam["terminal"] == declared["terminal"], sid
        assert seam["torn"] == declared["torn"], sid
        assert (seam["meta_keys"] is not None) == declared["meta"], sid
    assert projection["crashed"] == {"absent": True}


def test_ledger_kinds_golden_sequences_are_the_declared_kinds() -> None:
    sequences = _golden("ledger_kinds")["sequences"]
    for sid, declared in fp.DECLARED.items():
        assert sequences[sid] == declared["kinds"], sid


def test_meta_fields_golden_is_the_six_field_s0_meta() -> None:
    meta = _golden("meta_fields")
    fields = {
        "artifact_count": "int",
        "classification": "str",
        "duration_ms": "int",
        "limits_exceeded": "NoneType",
        "result_state": "str",
        "run_id": "str",
    }
    assert set(meta["succeeded"]) == set(fields)
    assert meta["succeeded"]["run_id"] == "str"
    assert meta["partial_limits"]["limits_exceeded"] == "list"
    assert "recovered" in meta["interrupted"]
