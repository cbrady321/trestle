"""CI twin of `host/test_k6_docs.py` (L.RB-3.4; MC-B-03): the same documented rows, the same
cases and the same removal-set rule, on an in-memory engine that applies Compose's semantics."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.proof import tolerances

from twin import k6_docs


@pytest.mark.stub_proven("WR-OWN-4:docs-equal-observed-host@stub-twin")
def test_documented_removal_sets_equal_observed(tmp_path: Path) -> None:
    engine = k6_docs.FakeEngine()

    def observe(row: str):
        project = f"trestle-k6docs-{row.replace('_', '-')}"
        return k6_docs.observe_case(
            engine, tmp_path / row, project, row, "alpine@sha256:0", tolerances.JOIN_WAIT_S
        )

    k6_docs.assert_docs_equal_observed(observe)
    # the legacy runner really asked the engine for each teardown the rows name
    assert engine.backend().calls == [
        "up",
        "down(volumes=False)",
        "up",
        "stop",
        "up",
        "up",
        "down(volumes=True)",
    ]
