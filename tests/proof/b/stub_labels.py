"""MC-B-05 helpers: load `stub_labels.toml` and audit it against the declared labels (L.RB-0.5).

Not a test module (never matches `test_*.py`, DM-80): `tests/proof/b/test_stub_labels.py` and
`tests/proof/ckpt/slice_b.py` both call it and implement nothing of their own.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
STUB_LABELS_PATH = ROOT / "tests" / "proof" / "b" / "stub_labels.toml"

TWIN_SUFFIX = "@stub-twin"  # CSC-8
ROW_KEYS = {"label", "group", "text", "deferral"}
TOP_KEYS = {"twin_suffix", "row"}
# the stub groups MC-B-05 names: clauses B4.6, B6.2, B8.4; D-1 (toolchain), D-8 (engine starter),
# D-9 (AWS demo, never real), D-19 (JVM/Gradle)
GROUPS = ("B4.6", "B6.2", "B8.4", "D-1", "D-8", "D-9", "D-19")
# DM-29: the adversary-stub clauses carry no STUB label
NO_STUB_CLAUSES = ("B4.5", "B9.1")
# AMB-8: the leaf that appends the rows of the stub labels a merge's leaves declare. A B-PRE leaf
# (RB-4.2, RB-4.3) declares a stub label before its row leaf lands, so the row is required only
# once the row leaf's commit is in the history.
ROW_LEAF_BY_MERGE = {
    "RB-4": "L.RB-4.5",
    "RB-7": "L.RB-7.2",
    "RB-9": "L.RB-9.6",
    "RB-10": "L.RB-10.2",
}
_DECLARED_BY_MERGE = re.compile(r"^L\.(RB-\d+)\.\d+$")


class StubLabelsLoadError(ValueError):
    pass


@dataclass
class StubLabels:
    twin_suffix: str = TWIN_SUFFIX
    rows: list[dict[str, Any]] = field(default_factory=list)


def load(path: Path | None = None) -> StubLabels:
    data = tomllib.loads((path or STUB_LABELS_PATH).read_text())
    extra = set(data) - TOP_KEYS
    if extra:
        raise StubLabelsLoadError(f"unknown top-level key(s) {sorted(extra)}")
    if "twin_suffix" not in data:
        raise StubLabelsLoadError("twin_suffix is absent")
    return StubLabels(twin_suffix=str(data["twin_suffix"]), rows=list(data.get("row", [])))


def landed_leaves(root: Path | None = None) -> set[str] | None:
    """Leaf ids (`L.RB-4.5`) with a commit `L.<id>: ...` in HEAD's history; `None` when the history
    cannot be read (then every row is required, the strict reading)."""
    proc = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=root or ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return {
        m.group(1) for line in proc.stdout.splitlines() if (m := re.match(r"^(L\.[^:\s]+):", line))
    }


def row_leaf(label: dict[str, Any]) -> str | None:
    """The leaf that appends this stub label's row, or `None` when the label's `declared_by` names
    no merge with a row leaf (its row is then always required)."""
    match = _DECLARED_BY_MERGE.match(str(label.get("declared_by", "")))
    return ROW_LEAF_BY_MERGE.get(match.group(1)) if match else None


def is_twin(label_id: str, suffix: str = TWIN_SUFFIX) -> bool:
    return label_id.endswith(suffix)


def stub_proven_non_twins(labels: list[dict[str, Any]], suffix: str) -> list[dict[str, Any]]:
    return [
        lb
        for lb in labels
        if lb.get("posture") == "stub_proven" and not is_twin(str(lb["id"]), suffix)
    ]


def problems(
    stub: StubLabels,
    labels: list[dict[str, Any]],
    landed: set[str] | None = None,
) -> list[str]:
    """Every way `stub` fails MC-B-05 against the declared `labels` (all of labels.d)."""
    out: list[str] = []
    if stub.twin_suffix != TWIN_SUFFIX:
        out.append(f"twin_suffix is {stub.twin_suffix!r}, CSC-8 spells it {TWIN_SUFFIX!r}")
    declared = {str(lb["id"]): lb for lb in labels}
    seen: dict[str, int] = {}
    for row in stub.rows:
        extra = set(row) - ROW_KEYS
        missing = ROW_KEYS - set(row)
        if extra or missing:
            out.append(f"row {row.get('label')!r}: bad schema (extra={extra}, missing={missing})")
            continue
        label_id = str(row["label"])
        seen[label_id] = seen.get(label_id, 0) + 1
        if row["group"] not in GROUPS:
            out.append(f"row {label_id}: group {row['group']!r} is not one of {GROUPS}")
        for key in ("text", "deferral"):
            if not str(row[key]).strip():
                out.append(f"row {label_id}: {key} is empty")
        label = declared.get(label_id)
        if label is None:
            out.append(f"row {label_id}: no such label is declared in labels.d")
        elif label.get("posture") != "stub_proven":
            out.append(f"row {label_id}: the label's posture is {label.get('posture')!r}")
        elif is_twin(label_id, stub.twin_suffix):
            out.append(f"row {label_id}: a twin label has no row (its row is its origin's)")
    out += [
        f"row {label_id}: appears {n} times, exactly one row per label"
        for label_id, n in seen.items()
        if n > 1
    ]
    for label in stub_proven_non_twins(labels, stub.twin_suffix):
        if str(label["id"]) in seen:
            continue
        leaf = row_leaf(label)
        if landed is not None and leaf is not None and leaf not in landed:
            continue  # AMB-8: its row leaf has not landed yet
        out.append(f"stub_proven label {label['id']} has no row in stub_labels.toml (MC-B-05)")
    return out
