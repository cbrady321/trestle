"""TM-B4-3: the request schema is bound to CATALOG_V0 until L.RB-1.1 wires the catalog (L.RB-0.2).

The register rule (`meta register`) refuses a claim of a label while a present temporary entry
serves it, and a `claim` label with no registering node counts as claimed. This target holds the
label `WR-AUTH-3:unknown-id-zero-effects` as a declaration, red as a strict xfail, for exactly as
long as `catalog_v0` exists; L.RB-1.1 deletes the module, this file and TM-B4-3 together and
registers the real node (`tests/mcp/test_refusals.py`)."""

from __future__ import annotations

import importlib.util

import pytest
from tests.proof.markers import target_check


@pytest.mark.target("TM-B4-3")
@pytest.mark.proves("WR-AUTH-3", "WR-AUTH-3:unknown-id-zero-effects", "B", "B", "MCP", "CI")
@pytest.mark.xfail(strict=True, reason="defect:TM-B4-3")
def test_target_request_schema_is_bound_to_the_catalog() -> None:
    target_check(
        importlib.util.find_spec("trestle_env.catalog_v0") is None,
        "TM-B4-3",
        "the request schema still enumerates the hard-coded one-service catalog",
    )
