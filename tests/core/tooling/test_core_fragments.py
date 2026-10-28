"""L.CS-1.3: the core phase's labels and temporary fragments load under P0's
exact loaders (CSC-1, CM-7). Reads only `labels.d/` and `temporary.d/`; the
fence fragment is `test_core_fence.py`'s alone. Agreement with the root plan
tables is the writer's plan-time check, never a CI test."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from tests.proof import meta, register

ROOT = Path(__file__).resolve().parents[3]
LABELS_D = ROOT / "tests" / "proof" / "labels.d"
CORE_LABELS = LABELS_D / "core.toml"
CORE_TEMPORARY = ROOT / "tests" / "proof" / "temporary.d" / "core.toml"
LEAF_ID = re.compile(r"^L\.[A-Za-z0-9/-]+\.\d+$")


def _core_labels() -> list[dict]:
    return list(tomllib.loads(CORE_LABELS.read_text())["label"])


def test_core_fragments_parse_and_match_plan() -> None:
    # labels: exactly CSC-1's key set (P0's own constants), postures in C.9's vocabulary
    labels = _core_labels()
    assert labels
    for label in labels:
        assert set(label) <= meta.CSC1_ALL_KEYS, label["id"]
        assert meta.CSC1_REQUIRED_KEYS <= set(label), label["id"]
        assert label["posture"] in meta.CSC1_POSTURES, label["id"]
        assert label["id"].split(":", 1)[0] == label["row"], label["id"]
        assert label["step"] == "core", label["id"]
        assert label["venue"] in ("CI", "HOST", "BOTH"), label["id"]
        assert LEAF_ID.match(label["declared_by"]), label["id"]
        # no core-declared label is gated or both-variant (no `oq`); `na` only as a CK decline
        # patch writes it (CM-7 step 5): reason "K-n declined"
        assert "oq" not in label and "composes" not in label, label["id"]
        if label["posture"] == "na":
            assert re.fullmatch(r"K-\d+ declined", label.get("reason", "")), label["id"]
        else:
            assert label["posture"] in ("claim", "shape"), label["id"]
        # the two WR-COMPAT rows carry slice compat; every other row slice core
        expected_slice = "compat" if label["row"].startswith("WR-COMPAT-") else "core"
        assert label["slice"] == expected_slice, label["id"]

    # every label id is unique across all labels.d fragments; its row exists
    all_labels = meta._load_all_labels()
    ids = [str(label["id"]) for label in all_labels]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    rows = {r["id"] for r in tomllib.loads(meta.ROW_OWNERS_PATH.read_text())["row"]}
    for label in labels:
        assert label["row"] in rows, label["id"]

    # the label loader accepts the real registry (audit-rows load errors return 1)
    from argparse import Namespace

    assert meta.cmd_audit_rows(Namespace()) == 0

    # temporary: loads under P0's register loader (schema, unique ids, no recursive probe)
    entries = register.load_entries()
    core_ids = {e["id"] for e in tomllib.loads(CORE_TEMPORARY.read_text())["entry"]}
    assert core_ids and core_ids <= {e["id"] for e in entries}
    for raw in tomllib.loads(CORE_TEMPORARY.read_text())["entry"]:
        assert raw["permanent"] is False
        assert LEAF_ID.match(raw["introduced_by"]), raw["id"]
        assert "meta register" not in raw["probe"], raw["id"]
        if raw["removed_by"] == "named-not-removed":
            assert raw["serves"] == [] and raw.get("citation"), raw["id"]
        else:
            assert LEAF_ID.match(raw["removed_by"]), raw["id"]
        # no deferral is listed in temporary.d (CM-8): a deferral has a `closes_at`
        assert "closes_at" not in raw and "from_step" not in raw, raw["id"]
