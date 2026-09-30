"""L.TR-0.5: the A-2 (tree) label vocabulary `tests/proof/labels.d/tree.toml` (MC-B3-06) under P0's
exact CSC-1 loader, and the two register properties the plan states for it: a label composing a
clause a present register entry serves is unclaimable (held, CM-7), and every label whose
`declared_by` merge is on HEAD has a registered test or is gated (Coverage (a)).

The register rule (CM-7) and the label loader are P0's (`tests/proof/register.py`,
`tests/proof/meta.py`); this file only reads them. It is permanent (no phase removes it)."""

from __future__ import annotations

import re
import tomllib
from argparse import Namespace
from pathlib import Path
from typing import Any

from tests.proof import meta, register
from tests.proof import trailers as trailers_mod
from tests.proof import transcribe as transcribe_mod

ROOT = Path(__file__).resolve().parents[2]
PROOF = ROOT / "tests" / "proof"
LEAF_ID = re.compile(r"^L\.TR-(?P<merge>[0-6]|L)\.\d+$")

# The twelve tree clauses of MC-03 a tree label may compose (a2:L32; Coverage (b)).
TREE_CLAUSES = {
    "A1.5", "A1.6", "A2.5", "A2.6", "A3.4", "A4.2",
    "A5.3", "A5.4", "A6.3", "A6.4", "A8.4", "A8.5",
}  # fmt: skip
# The clauses TM-B2-1's `full` phase serves (a2:L1150, MC-B3-04's lift set).
LIFT_SET = {"A1.5", "A2.6", "A4.2", "A5.4", "A6.3", "A6.4", "A8.4", "A8.5"}
VENUES = ("CI", "HOST", "BOTH")
POSTURES = ("claim", "gated_on", "both_variant")
OQ_OF_POSTURE = {"gated_on": "OQ-27", "both_variant": "OQ-31"}


def _tree_labels() -> list[dict[str, Any]]:
    return list(tomllib.loads((PROOF / "labels.d" / "tree.toml").read_text())["label"])


def _merge_of(leaf: str) -> str:
    """`L.TR-3.2` -> `TR-3`; `L.TR-L.4` -> `TR-L`."""
    match = LEAF_ID.match(leaf)
    assert match, leaf
    return f"TR-{match.group('merge')}"


def test_labels_fragment_loads_and_composes_resolve() -> None:
    labels = _tree_labels()
    assert labels
    rows = tomllib.loads(meta.ROW_OWNERS_PATH.read_text())["row"]
    row_ids = {r["id"] for r in rows}
    join_cids = {c for r in rows for c in r["join_cids"]}
    oqs = {o["id"] for o in meta.load_open_questions()}
    matrix = transcribe_mod.matrix_ids() | {c["id"] for c in transcribe_mod.load_matrix_map()}

    for label in labels:
        lid = label["id"]
        # exactly the CSC-1 shape (MC-04): required keys present, no other key
        assert set(label) <= meta.CSC1_ALL_KEYS, lid
        assert meta.CSC1_REQUIRED_KEYS <= set(label), lid
        assert set(label) - meta.CSC1_REQUIRED_KEYS <= {"composes", "oq"}, lid
        # `<ROW>:<label>` or `<C-ID>:<label>`; the row names a requirements row or a join C-id
        assert lid.split(":", 1)[0] == label["row"], lid
        assert label["row"] in row_ids | join_cids, lid
        assert label["step"] == "tree" and label["slice"] == "A", lid
        assert label["venue"] in VENUES, lid
        assert label["tier"], lid
        assert label["posture"] in POSTURES, lid
        assert LEAF_ID.match(label["declared_by"]), lid
        # `composes` is an MC-03 tree clause that resolves in the transcribed matrix (or absent)
        if "composes" in label:
            assert label["composes"] in TREE_CLAUSES, lid
            assert label["composes"] in matrix, lid
        # `oq` present iff the label is gated_on / both_variant, and named in open_questions.toml
        if label["posture"] in ("gated_on", "both_variant"):
            assert label["oq"] == OQ_OF_POSTURE[label["posture"]], lid
            assert label["oq"] in oqs, lid
        else:
            assert "oq" not in label, lid
        # a HOST-venue label is the `@host` twin of a CI label (CSC-9)
        if label["venue"] == "HOST":
            assert lid.endswith("@host"), lid

    # exactly the two OQ-bound labels the plan lists (Coverage (a) and (c))
    assert {(lb["id"], lb["oq"]) for lb in labels if "oq" in lb} == {
        ("WR-UNIT-1:eligibility-oq31", "OQ-31"),
        ("WR-UNIT-6:child-addressed-cancel", "OQ-27"),
    }
    # every clause of the lift set has a composing label (so TM-B2-1 holds something)
    assert LIFT_SET <= {lb["composes"] for lb in labels if "composes" in lb}

    # the one deferred label, declared here with its closing leaf (CM-8): the core deferral entry
    # already exists (`closes_at = TR-6`, `declared_by = L.CL-D1.3`), and this fragment adds no
    # deferral of its own
    (tree_size,) = [lb for lb in labels if lb["id"] == "WR-TERM-5:tree-size"]
    assert tree_size["declared_by"] == "L.TR-6.4" and tree_size["composes"] == "A1.6"
    deferral = [d for d in tomllib.loads((PROOF / "deferrals.toml").read_text())["deferral"]]
    (entry,) = [d for d in deferral if d["label"] == "WR-TERM-5:tree-size"]
    assert entry["closes_at"] == "TR-6" and entry["declared_by"] == "L.CL-D1.3"

    # every label id is unique across all labels.d fragments, and the real registry loads
    ids = [str(label["id"]) for label in meta._load_all_labels()]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    assert {lb["id"] for lb in labels} <= set(ids)
    assert meta.cmd_audit_rows(Namespace()) == 0
    assert meta.cmd_open_questions(Namespace()) == 0


def test_planted_held_label_unclaimable(capsys: Any) -> None:
    """After L.TR-L.1 `meta register --probe multi-vertex-refusal` prints `choice-only` (CM-7: an
    entry that serves a clause also serves every label whose `composes` is that clause): a planted
    passing test for a label composing A2.6 is reported unclaimable, one composing a lifted clause
    (A1.5) is claimable, and with the entry served away the A2.6 label is claimable too."""
    labels = _tree_labels()
    (held_label,) = [lb for lb in labels if lb["id"] == "WR-UNIT-2:record-slices"]
    assert held_label["composes"] == "A2.6" and held_label["posture"] == "claim"
    (lifted,) = [lb for lb in labels if lb["id"] == "WR-UNIT-7:permutation-invariant"]
    assert lifted["composes"] == "A1.5" and lifted["posture"] == "claim"

    # TM-B2-1 is registered with DM-12 phases and its probe prints `choice-only` (the AllDeclaration
    # refusal is lifted; the ChoiceNode refusal stays until L.TR-5.3)
    assert register.cmd_probe("multi-vertex-refusal") == 0
    assert capsys.readouterr().out.strip() == "choice-only"

    passing = {
        held_label["id"]: [{"gap": None, "strict_xfail": False}],
        lifted["id"]: [{"gap": None, "strict_xfail": False}],
    }
    entries = register.load_entries()
    held = register.register_violations(entries, passing)
    assert held_label["id"] in held
    assert lifted["id"] not in held
    # ... and it is the entry, not the label, that holds it: with the entry's phase served away,
    # the same passing test is claimable
    without = [e for e in entries if e["id"] != "multi-vertex-refusal"]
    assert held_label["id"] not in register.register_violations(without, passing)

    # a label composing a clause no present entry serves (A2.5) is claimable as it stands
    (free,) = [lb for lb in labels if lb["id"] == "WR-UNIT-8:declared-tree-extracted"]
    assert free["composes"] == "A2.5"
    assert free["id"] not in register.register_violations(
        entries, {free["id"]: [{"gap": None, "strict_xfail": False}]}
    )


def label_status(label: dict[str, Any], registrants: dict[str, list[Any]], landed: set[str]) -> str:
    """One label's state at a HEAD where `landed` merges carry `WR-Merge:`: `registered` (a test
    carries it), `gated` (gated_on / both_variant), `pending` (its declaring merge has not
    landed), or `unregistered` (landed, no test, not gated: the failure)."""
    if registrants.get(label["id"]):
        return "registered"
    if label["posture"] in ("gated_on", "both_variant"):
        return "gated"
    if _merge_of(label["declared_by"]) not in landed:
        return "pending"
    return "unregistered"


def test_every_label_registered_or_gated() -> None:
    labels = _tree_labels()

    # -- planted: a label whose declared_by merge is on HEAD, with no registered test and not
    # gated_on / both_variant, fails; the same label of a later merge is reported pending
    base = {
        "id": "WR-UNIT-1:planted",
        "row": "WR-UNIT-1",
        "step": "tree",
        "slice": "A",
        "tier": "LOGIC",
        "venue": "CI",
        "posture": "claim",
        "declared_by": "L.TR-0.4",
    }
    assert label_status(base, {}, {"TR-0"}) == "unregistered"
    assert label_status(base, {}, set()) == "pending"
    assert label_status({**base, "declared_by": "L.TR-1.1"}, {}, {"TR-0"}) == "pending"
    assert label_status({**base, "declared_by": "L.TR-L.4"}, {}, {"TR-0"}) == "pending"
    assert label_status({**base, "declared_by": "L.TR-L.4"}, {}, {"TR-L"}) == "unregistered"
    assert label_status({**base, "posture": "gated_on", "oq": "OQ-27"}, {}, {"TR-0"}) == "gated"
    assert label_status({**base, "posture": "both_variant"}, {}, {"TR-0"}) == "gated"
    assert label_status(base, {base["id"]: [{"gap": None}]}, {"TR-0"}) == "registered"

    # -- real: over the real fragment at this HEAD. A merge is landed when its `WR-Merge:` carrier
    # is reachable; the collect-only registrant scan is run only when some tree merge is landed
    # (before that every label is pending and there is nothing to look for)
    landed = {m for m in _merges(labels) if trailers_mod.landing(m) is not None}
    registrants = register._label_registrants() if landed else {}  # noqa: SLF001
    statuses = {lb["id"]: label_status(lb, registrants, landed) for lb in labels}
    unregistered = sorted(lid for lid, st in statuses.items() if st == "unregistered")
    assert unregistered == [], unregistered
    # labels of merges not yet on HEAD are pending, never failures
    for lb in labels:
        if _merge_of(lb["declared_by"]) not in landed and lb["posture"] == "claim":
            assert statuses[lb["id"]] in ("pending", "registered"), lb["id"]


def _merges(labels: list[dict[str, Any]]) -> set[str]:
    return {_merge_of(lb["declared_by"]) for lb in labels}
