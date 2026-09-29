"""WR-COMPAT-5 (the plugin `Context` members and the bare `@trestle`
decorator) and WR-COMPAT-10's declare-nothing defaults (300 s / 4096 B),
pinned against the S0 goldens."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pins.b_contract import facets
from tests.pins.b_contract.kit import run_plugin
from tests.proof import normalize


def _golden(name: str) -> object:
    path = facets.GOLDEN_DIR / f"{name}.json"
    return normalize.normalize(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.compat
@pytest.mark.proves("WR-COMPAT-5", "WR-COMPAT-5:preserved", "core", "core", "must", "CI")
def test_context_surface_preserved() -> None:
    current = normalize.normalize(facets.context_surface())
    missing, unexpected = normalize.structural_diff(
        _golden("context_surface"), current, policy="named"
    )
    assert (missing, unexpected) == ([], [])
    # the members plugin authors rely on, stated outright
    context = current["context"]
    assert set(context["attributes"]) == {"tmp", "outputs", "cancelled", "deadline"}
    assert set(context["methods"]) == {"log", "progress", "artifact", "attach"}
    assert current["decorator"]["bare"] is True


@pytest.mark.compat
@pytest.mark.proves("WR-COMPAT-10", "WR-COMPAT-10:preserved", "core", "core", "must", "CI")
def test_declare_nothing_defaults(tmp_path: Path) -> None:
    current = normalize.normalize(facets.snapshot_manifest())
    missing, unexpected = normalize.structural_diff(
        _golden("snapshot_manifest"), current, policy="free"
    )
    assert missing == []
    assert current["declare_nothing_defaults"] == {
        "timeout_s": 300,
        "summary_budget": 4096,
        "version": "0.1.0",
    }
    # admission stamps the same defaults onto a run that declared nothing
    view, run_dir = run_plugin(tmp_path, "echo", {"message": "defaults"})
    assert view.state == "succeeded"
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert spec["timeout_s"] == 300
    assert spec["summary_budget"] == 4096
