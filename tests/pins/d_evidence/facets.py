"""Lane-D facet extractors for `python -m tests.proof.differ d1` (L.P0-1D.5).

`query_row_shapes` reads the nine query views over each S0 fossil home
(`tests/fixtures/fossils/s0/<state>/home`) plus the view catalog, and records
row *shapes* (field names and value types), never values, so it is stable
across runs. `event_row_shape` records the shape of the rows the child
context writes to `events.ndjson`, and of the committed S0 fossil's events.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.proof import harness
from trestle.child.context import RuntimeContext
from trestle.common import fsutil
from trestle.query.catalog import view_catalog
from trestle.query.views import VIEW_NAME_VALUES, VIEW_ROW_FIELDS

ROOT = Path(__file__).resolve().parents[3]
S0_FOSSILS = ROOT / "tests" / "fixtures" / "fossils" / "s0"
FOSSIL_STATES = ("succeeded", "failed")
NO_RUN_VIEWS = {"recent_runs", "recent_failures"}
_ABSENT_ARTIFACT = "art_absent"


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _shape(value: Any) -> Any:
    """Type-only structural shape of a JSON value."""
    if isinstance(value, dict):
        return {k: _shape(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return "list"
    return _type_name(value)


def _fossil_run_id(home: Path) -> str:
    return next(p.name for p in sorted((home / "runs").glob("*/*")) if p.is_dir())


def query_row_shapes() -> dict[str, Any]:
    views: dict[str, dict[str, Any]] = {
        name: {"declared_fields": sorted(VIEW_ROW_FIELDS[name]), "observed": {}}
        for name in VIEW_NAME_VALUES
    }
    with tempfile.TemporaryDirectory(prefix="trestle-d5-") as tmp:
        for state in FOSSIL_STATES:
            home = Path(tmp) / state
            shutil.copytree(S0_FOSSILS / state / "home", home)
            kernel = harness.fresh_kernel(home=home)
            run_id = _fossil_run_id(home)
            for name in VIEW_NAME_VALUES:
                if name in NO_RUN_VIEWS:
                    params: dict[str, object] = {}
                elif name == "artifact_refs":
                    params = {"artifact_id": _ABSENT_ARTIFACT}
                else:
                    params = {"run_id": run_id}
                out = kernel.control.query(name, params)
                if not isinstance(out, dict):
                    views[name]["observed"][state] = {"refused": getattr(out, "code", "?")}
                    continue
                rows = out["items"]
                views[name]["observed"][state] = {
                    "envelope_keys": sorted(out),
                    "row_count_kind": "some" if rows else "none",
                    "row_shape": _shape(rows[0]) if rows else None,
                }
    catalog = view_catalog()
    return {
        "view_names": list(VIEW_NAME_VALUES),
        "views": views,
        "view_catalog": {
            "keys": sorted(catalog),
            "view_entry_keys": sorted({k for v in catalog["views"] for k in v}),
            "fetch_keys": sorted(catalog["fetch"]),
            "fetch_kinds": catalog["fetch"]["kinds"],
            "mouth_keys": sorted(catalog["mouths"]),
        },
    }


def event_row_shape() -> dict[str, Any]:
    kinds: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="trestle-d5-") as tmp:
        root = Path(tmp)
        work = root / "work"
        evidence = root / "evidence"
        evidence.mkdir()
        events = evidence / "events.ndjson"
        ctx = RuntimeContext(
            work=work,
            evidence=evidence,
            deadline=datetime.now(UTC),
            events_path=events,
        )
        ctx.log("shape")
        ctx.progress("shape")
        ctx.progress("shape", fraction=0.5)
        staged = work / "tmp" / "shape.bin"
        staged.write_bytes(b"x")
        ctx.attach(staged, name="shape.bin")
        for row in fsutil.read_ndjson(events):
            kinds.setdefault(str(row["kind"]), []).append(_shape(row))
    fossil_events = next(
        (S0_FOSSILS / "succeeded" / "home" / "runs").glob("*/*/evidence/events.ndjson")
    )
    fossil_rows = [json.loads(line) for line in fossil_events.read_text().splitlines() if line]
    return {
        "child_context_rows": {
            k: sorted({json.dumps(s, sort_keys=True) for s in v}) for k, v in sorted(kinds.items())
        },
        "fossil_succeeded_rows": [_shape(r) for r in fossil_rows],
        "view_row_fields": sorted(VIEW_ROW_FIELDS["run_events"]),
    }
