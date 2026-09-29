"""L.P0-1C.5: the S0 goldens agree with their extractors, structurally.

`differ d1` reads `tests/proof/facets.toml`; the lane cannot edit that spine
file, so the d1 test below registers the four lane-C facets in a temporary
facets file and runs the real engine over it (`--strict`).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from tests.pins.c_surface import facets
from tests.proof import differ, normalize

FACET_TOML = """
[[facet]]
id = "{id}"
additive = "{additive}"
golden = "tests/fixtures/golden/s0/{id}.json"
extractor = "tests.pins.c_surface.facets:{id}"
"""
ADDITIVE = {
    "tools_list": "named",
    "runview_shape": "named",
    "refusal_codes": "named",
    "spec_keys": "free",
}


def _golden(facet: str) -> object:
    return json.loads((facets.GOLDEN_DIR / f"{facet}.json").read_text(encoding="utf-8"))


def _assert_matches_golden(facet: str) -> None:
    """d1's own comparison (`differ.facet_diff`) under the facet's facets.toml policy: a "named"
    facet grows only where a divergence entry names the addition (keys/items/grows selectors, or
    `codes` for refusal_codes); a "free" one may add keys, never lose or change one."""
    current = normalize.normalize(getattr(facets, facet)())
    result = differ.facet_diff(
        facet, normalize.normalize(_golden(facet)), current, policy=ADDITIVE[facet]
    )
    assert (result.missing, result.unexpected) == ([], [])


@pytest.mark.compat
@pytest.mark.proves("WR-COMPAT-1", "WR-COMPAT-1:preserved", "core", "core", "PROC", "CI")
def test_tools_list_matches_golden() -> None:
    _assert_matches_golden("tools_list")
    golden = _golden("tools_list")
    assert isinstance(golden, dict)
    assert len(golden["tool_names"]) == 10


@pytest.mark.compat
@pytest.mark.proves("WR-COMPAT-7", "WR-COMPAT-7:preserved", "core", "core", "PROC", "CI")
def test_refusal_codes_match_golden() -> None:
    _assert_matches_golden("refusal_codes")


def test_runview_shape_matches_golden() -> None:
    _assert_matches_golden("runview_shape")


def test_spec_keys_match_golden() -> None:
    _assert_matches_golden("spec_keys")


def test_golden_files_are_in_canonical_form() -> None:
    for facet in facets.FACET_FUNCTIONS:
        text = (facets.GOLDEN_DIR / f"{facet}.json").read_text(encoding="utf-8")
        assert (
            text
            == json.dumps(
                normalize.normalize(json.loads(text)), indent=2, sort_keys=True, ensure_ascii=False
            )
            + "\n"
        )


def test_d1_strict_passes_with_lane_c_facets_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    facets_toml = tmp_path / "facets.toml"
    facets_toml.write_text(
        "".join(FACET_TOML.format(id=fid, additive=add) for fid, add in ADDITIVE.items())
    )
    monkeypatch.setattr(differ, "FACETS_PATH", facets_toml)
    args = argparse.Namespace(facet=sorted(ADDITIVE), strict=True)
    assert differ.cmd_d1(args) == 0, capsys.readouterr().out
