"""MC-35: the Slice A workflow registry (L.SV-5.14; grows at L.SL-11.1, joined by the tree
workflows at L.TR-6.6).

`SLICE_A_WORKFLOWS` maps a workflow's plugin name to what the guarantee suites and the checkpoint
audits need to run it without knowing it:

* `fixture_path`: the published plugin source, relative to the repository root;
* `env_arg`: the plugin's `env_arg` argument (WR-OWN-8), or None when it declares none;
* `declared_codes`: the stable codes its declaration names (retryable codes and remedy triggers),
  which the suites drive;
* `oq31_eligible_both`: whether the workflow is eligible for both OQ-31 variants.

`tests/proof/suites/test_conformance_matrix.py::test_registry_entries_publish_and_admit` publishes
and admits every entry; J-SINGLE (b)'s vertex audit requires every entry admitted from a node under
`tests/proof/suites/` (the audit plugin reads this table)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

SLICE_A_WORKFLOWS: dict[str, dict[str, Any]] = {
    "spine_leaf": {
        "fixture_path": "tests/fixtures/workflows/spine_leaf.py",
        "env_arg": "env",
        "declared_codes": (),
        "oq31_eligible_both": True,
    },
}

ENTRY_KEYS = frozenset({"fixture_path", "env_arg", "declared_codes", "oq31_eligible_both"})


def fixture_file(entry: dict[str, Any]) -> Path:
    """The entry's plugin source file."""
    return ROOT / str(entry["fixture_path"])
