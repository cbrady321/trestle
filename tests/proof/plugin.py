"""Root proof plugin (MC-01, MC-33): registers the marker vocabulary and
validates `proves()` markers against the CSC-1 labels registry at
collection time (L.P0-0a.2).

Later leaves extend collection-time validation here (the matrix-id switch
at L.P0-0c.1; the CSC-9 deselection hook at L.P0-0d.9).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tests.proof import results as results_mod
from tests.proof.markers import MARKER_DOCS, VALID_SLICES

ROOT = Path(__file__).resolve().parents[2]
LABELS_DIR = ROOT / "tests" / "proof" / "labels.d"

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


def pytest_configure(config: pytest.Config) -> None:
    for name, doc in MARKER_DOCS.items():
        config.addinivalue_line("markers", f"{name}: {doc}")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    labels = _load_labels()
    for item in items:
        item_labels: list[str] = []
        for mark in item.iter_markers(name="proves"):
            row = mark.args[0] if len(mark.args) > 0 else mark.kwargs.get("row")
            clause = mark.args[1] if len(mark.args) > 1 else mark.kwargs.get("clause")
            slice_ = mark.args[2] if len(mark.args) > 2 else mark.kwargs.get("slice")
            label_id = clause or row
            if label_id is None:
                continue
            if label_id not in labels:
                raise pytest.UsageError(
                    f"{item.nodeid}: proves() references undeclared label {label_id!r}"
                    f" (not in {LABELS_DIR}/*.toml)"
                )
            if slice_ is not None and slice_ not in VALID_SLICES:
                raise pytest.UsageError(
                    f"{item.nodeid}: proves() slice={slice_!r} is not one of {VALID_SLICES}"
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
