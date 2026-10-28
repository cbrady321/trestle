"""R-MCP-9 view catalog — frozen names, when-to-pick, no plugin schemas."""

from __future__ import annotations

import json

from trestle.query.catalog import VIEW_CATALOG_URI, view_catalog
from trestle.query.views import FETCH_WINDOW_KINDS, VIEW_NAME_VALUES, VIEW_NAMES


def test_view_catalog_covers_frozen_views_and_mouths() -> None:
    catalog = view_catalog()
    names = [row["name"] for row in catalog["views"]]
    assert names == list(VIEW_NAME_VALUES)
    assert set(names) == VIEW_NAMES

    blob = json.dumps(catalog)
    assert "last_error" in blob
    assert "run_events" in blob
    assert "run_tail" in blob
    assert "{run_id}/result" in blob
    assert "art_" in blob
    assert "pager" in blob.lower() or "50 rows" in blob
    assert catalog["mouths"]["print"] == "query view=run_tail"
    assert "run_events" in catalog["mouths"]["ctx.log"]
    assert catalog["mouths"]["return_value"] == "fetch {run_id}/result"
    assert catalog["mouths"]["artifact"] == "fetch art_…"
    assert list(catalog["fetch"]["kinds"]) == list(FETCH_WINDOW_KINDS)

    assert "input_schema" not in blob
    assert "return_schema" not in blob
    assert VIEW_CATALOG_URI == "trestle://views"
    dumped = json.dumps(catalog, separators=(",", ":")).encode()
    assert len(dumped) < 4096
