"""SA-08 drift proof: matrix-map / row-owner transcription (L.P0-0c.1,
L.P0-0c.2) stays exact against the read-only design packet."""

from __future__ import annotations

import os

import pytest

from tests.proof import transcribe


@pytest.mark.parametrize("sa", ["SA-08"])
def test_matrix_map_shape(sa: str) -> None:
    clauses = transcribe.load_matrix_map()
    assert len(clauses) == 65
    cells = {c["cell"] for c in clauses}
    assert len(cells) == 18
    n_parts = sum(len(c["parts"]) if "parts" in c else 1 for c in clauses)
    assert n_parts == 69
    assert sum(1 for c in clauses if c["stub_label_required"]) == 3
    assert sum(1 for c in clauses if c["adversary_stub"]) == 2


@pytest.mark.parametrize("sa", ["SA-08"])
def test_matrix_verbatim_against_requirements(sa: str) -> None:
    """HOST-only (main checkout has the requirements doc); UNPROVEN off-HOST."""
    requirements = os.environ.get("TRESTLE_REQUIREMENTS")
    if not requirements or not os.path.exists(requirements):
        pytest.skip("TRESTLE_REQUIREMENTS not set to the main checkout (HOST-only, UNPROVEN)")
    from argparse import Namespace

    rc = transcribe.cmd_check_matrix(Namespace(requirements=requirements))
    assert rc == 0


@pytest.mark.parametrize("sa", ["SA-08"])
def test_row_owners_129_unique(sa: str) -> None:
    rows = transcribe.load_row_owners()
    ids = [r["id"] for r in rows]
    assert len(ids) == 129
    assert len(set(ids)) == 129
    assert sum(1 for i in ids if i.startswith("WR-COMPAT-")) == 12


@pytest.mark.parametrize("sa", ["SA-08"])
def test_row_owners_against_decomposition(sa: str) -> None:
    """HOST-only (main checkout has decomposition-workflow-runtime.md)."""
    decomposition = os.environ.get("TRESTLE_DECOMPOSITION")
    if not decomposition or not os.path.exists(decomposition):
        pytest.skip("TRESTLE_DECOMPOSITION not set to the main checkout (HOST-only, UNPROVEN)")
    from argparse import Namespace

    rc = transcribe.cmd_check_rows(Namespace(decomposition=decomposition))
    assert rc == 0
