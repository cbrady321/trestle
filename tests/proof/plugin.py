"""Root proof plugin (MC-01, MC-33): registers the marker vocabulary and
validates `proves()` markers against the CSC-1 labels registry at
collection time (L.P0-0a.2), and against the MC-03 matrix map for A*/B*
clause ids (L.P0-0c.1).

Later leaves extend collection-time validation here (the CSC-9 deselection
hook at L.P0-0d.9).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tests.proof import results as results_mod
from tests.proof import transcribe as transcribe_mod
from tests.proof.markers import MARKER_DOCS, VALID_SLICES

ROOT = Path(__file__).resolve().parents[2]
LABELS_DIR = ROOT / "tests" / "proof" / "labels.d"
COMPAT_MAP_PATH = ROOT / "tests" / "proof" / "compat_map.toml"

# nodeid -> clause/label ids this item's `proves()`/`stub_proven()` markers
# name, populated at collection time and read back in
# `pytest_runtest_logreport` (L.P0-0a.3).
_ITEM_LABELS: dict[str, list[str]] = {}


def _load_labels() -> dict[str, dict[str, object]]:
    labels: dict[str, dict[str, object]] = {}
    if not LABELS_DIR.exists():
        return labels
    for path in sorted(LABELS_DIR.glob("*.toml")):
        data = tomllib.loads(path.read_text())
        for entry in data.get("label", []):
            label_id = entry.get("id")
            if label_id:
                labels[label_id] = entry
    return labels


def _load_compat_map() -> dict[str, str]:
    """nodeid -> WR-COMPAT row id, from `compat_map.toml` (L.P0-0a.5)."""
    nodeid_to_row: dict[str, str] = {}
    if not COMPAT_MAP_PATH.exists():
        return nodeid_to_row
    data = tomllib.loads(COMPAT_MAP_PATH.read_text())
    for row in data.get("row", []):
        row_id = row.get("id")
        for nodeid in row.get("nodeids", []):
            nodeid_to_row[nodeid] = row_id
    return nodeid_to_row


def pytest_configure(config: pytest.Config) -> None:
    for name, doc in MARKER_DOCS.items():
        config.addinivalue_line("markers", f"{name}: {doc}")


def _deselect_host_gated(config: pytest.Config, items: list[pytest.Item]) -> list[pytest.Item]:
    """CSC-9 (L.P0-0d.9): deselect — never skip — `host_only` nodes unless
    `TRESTLE_HOST_GATE=proc`, and `docker_host` nodes unless
    `TRESTLE_HOST_GATE=docker`. One hook for every testpath and session
    (root, packs and env) — no per-directory conftest hook."""
    import os

    gate = os.environ.get("TRESTLE_HOST_GATE", "")
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if item.get_closest_marker("host_only") is not None and gate != "proc":
            deselected.append(item)
            continue
        if item.get_closest_marker("docker_host") is not None and gate != "docker":
            deselected.append(item)
            continue
        kept.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept
    return kept


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    items[:] = _deselect_host_gated(config, items)
    labels = _load_labels()
    compat_map = _load_compat_map()
    for item in items:
        item_labels: list[str] = []

        row_id = compat_map.get(item.nodeid)
        if row_id is not None:
            # compat_map.toml (L.P0-0a.5) marks this node `compat` and
            # `proves(row, "<row>:preserved")` without editing the test
            # file itself. WR-PROOF-2:pack-docker-live is not a WR-COMPAT
            # row (it labels the alpine live-compose skip UNPROVEN) so it
            # gets no `compat` marker, only the label.
            if row_id.startswith("WR-COMPAT"):
                item.add_marker(pytest.mark.compat)
                label_id = f"{row_id}:preserved"
            else:
                label_id = row_id
            if label_id not in labels:
                raise pytest.UsageError(
                    f"{item.nodeid}: compat_map.toml references undeclared label {label_id!r}"
                )
            item_labels.append(label_id)
        for mark in item.iter_markers(name="proves"):
            row = mark.args[0] if len(mark.args) > 0 else mark.kwargs.get("row")
            clause = mark.args[1] if len(mark.args) > 1 else mark.kwargs.get("clause")
            slice_ = mark.args[2] if len(mark.args) > 2 else mark.kwargs.get("slice")
            label_id = clause or row
            if label_id is None:
                continue
            if slice_ is not None and slice_ not in VALID_SLICES:
                raise pytest.UsageError(
                    f"{item.nodeid}: proves() slice={slice_!r} is not one of {VALID_SLICES}"
                )
            base_clause_id = label_id.split(":", 1)[0]
            if transcribe_mod.MATRIX_CLAUSE_RE.match(base_clause_id):
                # MC-03 matrix clause/part id (L.P0-0c.1): validated against
                # matrix_map.toml, never against the CSC-1 labels registry
                # (a clause is not a label; it names the matrix cell text
                # itself).
                if label_id not in transcribe_mod.matrix_ids():
                    raise pytest.UsageError(
                        f"{item.nodeid}: proves() references undeclared matrix clause "
                        f"{label_id!r} (not in matrix_map.toml)"
                    )
                item_labels.append(label_id)
                continue
            if label_id not in labels:
                raise pytest.UsageError(
                    f"{item.nodeid}: proves() references undeclared label {label_id!r}"
                    f" (not in {LABELS_DIR}/*.toml)"
                )
            declared_slice = labels[label_id].get("slice")
            if declared_slice == "compat":
                if slice_ != "core":
                    raise pytest.UsageError(
                        f"{item.nodeid}: label {label_id!r} is declared slice=compat and "
                        f"must be marked slice=core, got {slice_!r}"
                    )
            elif declared_slice is not None and slice_ is not None and slice_ != declared_slice:
                raise pytest.UsageError(
                    f"{item.nodeid}: label {label_id!r} is declared slice={declared_slice!r}, "
                    f"marker says slice={slice_!r}"
                )
            item_labels.append(label_id)

        for mark in item.iter_markers(name="target"):
            gap = mark.args[0] if mark.args else mark.kwargs.get("gap")
            has_strict_xfail = any(m.kwargs.get("strict") for m in item.iter_markers(name="xfail"))
            has_gap_reason = bool(
                list(item.iter_markers(name="gated_on")) or list(item.iter_markers(name="na"))
            )
            if not has_strict_xfail and not has_gap_reason:
                raise pytest.UsageError(
                    f"{item.nodeid}: target({gap!r}) requires a strict xfail or a "
                    "gated_on()/na() reason"
                )

        for mark in item.iter_markers(name="stub_proven"):
            label_id = mark.args[0] if mark.args else mark.kwargs.get("label")
            if label_id is None:
                raise pytest.UsageError(
                    f"{item.nodeid}: stub_proven() requires a label id argument"
                )
            if label_id not in labels:
                raise pytest.UsageError(
                    f"{item.nodeid}: stub_proven() references undeclared label {label_id!r}"
                )
            item_labels.append(label_id)

        if item_labels:
            _ITEM_LABELS[item.nodeid] = item_labels


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Write one MC-P0-05 JSONL record per test outcome, only when
    `TRESTLE_PROOF_GATE` is set (L.P0-0a.3). Only the `call` phase is
    recorded for a normal pass/fail/skip; a `setup`/`teardown` failure is
    recorded as `error` (it never reaches `call`)."""
    gate = results_mod.current_gate()
    if gate is None:
        return

    if report.when == "call":
        if report.skipped and getattr(report, "wasxfail", None) is not None:
            outcome = "xfailed"
        elif report.passed and getattr(report, "wasxfail", None) is not None:
            outcome = "xpassed"
        else:
            outcome = report.outcome  # passed | failed | skipped
    elif report.when in ("setup", "teardown") and not report.passed:
        outcome = "error"
    else:
        return

    labels = _ITEM_LABELS.get(report.nodeid, [])
    if not labels:
        return

    record = results_mod.Record(
        nodeid=report.nodeid,
        outcome=outcome,
        gate=gate,
        venue=results_mod.venue_for_gate(gate),
        interpreter=results_mod.current_interpreter(),
        labels=labels,
    )
    results_mod.write_record(record)
