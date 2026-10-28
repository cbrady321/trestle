"""Selftest for divergence.toml (L.P0-0c.5): the P0 seed matches root MC-06
exactly, and no entry breaks the compat/no-keep rules."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DIVERGENCE_PATH = ROOT / "tests" / "proof" / "divergence.toml"


def _entries() -> list[dict[str, object]]:
    return list(tomllib.loads(DIVERGENCE_PATH.read_text()).get("entry", []))


REQUIRED_FIELDS = {
    "id",
    "facet",
    "row_or_k",
    "docs",
    "due_checkpoint",
    "direction",
    "authorized_by",
}


def test_schema_every_entry_names_row_or_k_docs_checkpoint_facet_authorized_by() -> None:
    entries = _entries()
    assert entries
    for e in entries:
        missing = REQUIRED_FIELDS - set(e)
        assert not missing, f"{e.get('id')} missing {missing}"
        assert e["authorized_by"]
        assert e["facet"]
        assert e["row_or_k"]
        assert e["due_checkpoint"]


def test_all_k_part2_and_named_additive_diffs_present() -> None:
    entries = _entries()
    row_or_k = {e["row_or_k"] for e in entries}
    for i in range(1, 20):
        assert f"K-{i}" in row_or_k, f"K-{i} missing from divergence.toml"

    facets = {e["facet"] for e in entries}
    # The root-named additive diffs this leaf must seed (TerminalAnswer field
    # and the process_identity ledger kind, named explicitly by the leaf's
    # own accept text).
    ids = {e["id"] for e in entries}
    assert "ADD-terminal-answer-field" in ids
    assert "ADD-ledger-kinds" in ids
    assert "ledger_kinds" in facets

    part2_ids = {e["id"] for e in entries if e["id"].startswith("BF2-")}
    assert part2_ids == {f"BF2-{n}" for n in range(1, 17)}


def test_no_entry_authorizes_a_part1_break() -> None:
    # Part 1 (WR-COMPAT rows' preserved clauses) is never named by an entry
    # as something this ledger *changes*; only Part-2 defect corrections and
    # knowing changes (K-items) are seeded (DM-06).
    for e in _entries():
        assert not str(e["id"]).startswith("PART1-")


def test_no_keep_carrier_entry() -> None:
    for e in _entries():
        row_or_k = str(e["row_or_k"]).lower()
        assert "keep" not in row_or_k
        assert "cleanup_param" not in row_or_k
