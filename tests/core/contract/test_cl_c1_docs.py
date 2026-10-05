"""L.CL-C1.6: the K-2, K-5 (call form, async) and K-9 (declared) docs and the R-J disclosure are
published (MC-05). INSPECT: this checks presence and the facts the statements quote; whether the
statements are adequate is the reviewer's call (RV-4, L.J-CORE.1)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from trestle.common import clock

REPO = Path(__file__).resolve().parents[3]


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def _block(text: str, tag: str) -> str:
    match = re.search(rf"<!-- {tag} -->(.*?)<!-- /{tag} -->", text, flags=re.DOTALL)
    assert match is not None, f"no <!-- {tag} --> block"
    return match.group(1)


def _k_docs(k: str) -> list[str]:
    entries = tomllib.loads(_read("tests/proof/k_doc_map.toml"))["k"]
    (entry,) = (e for e in entries if e["id"] == k)
    return list(entry["docs"])


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-2", "core", "core", "INSPECT", "CI")
@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-9-declared", "core", "core", "INSPECT", "CI")
@pytest.mark.proves(
    "WR-PLAN-5",
    "WR-PLAN-5:packages-recorded-not-snapshotted-disclosed",
    "core",
    "core",
    "INSPECT",
    "CI",
)
def test_kdocs_and_rj_disclosure_present() -> None:
    # K-2: every doc MC-05 names says what identity now covers and what it no longer is
    assert _k_docs("K-2") == ["docs/agents.md"]
    k2 = _block(_read("docs/agents.md"), "K-2")
    for phrase in (
        "snapshot id",
        "declared",
        "digests",
        "schemas",
        "runtime version",
        "source_sha256",
    ):
        assert phrase in k2, phrase
    assert "`plugin.py` alone" in k2

    # K-9 (declared): every doc MC-05 names states the declared deadline, the default kept, the
    # ceiling and the refusal, and the numbers are the module's own
    assert set(_k_docs("K-9")) == {"docs/plugins.md", "docs/agents.md"}
    for doc in _k_docs("K-9"):
        k9 = _block(_read(doc), "K-9")
        assert "deadline" in k9, doc
        assert "admission.budget_does_not_fit" in k9, doc
        assert f"{clock.deadline_ceiling:g} s" in k9, doc
        assert "300 s" in k9, doc
    plugins = _read("docs/plugins.md")
    assert "deadline_s" in _block(plugins, "K-9") and "deadline_source" in _block(plugins, "K-9")

    # K-5, call form and async half (docs/plugins.md)
    assert "docs/plugins.md" in _k_docs("K-5")
    k5 = _block(plugins, "K-5")
    assert "call form" in k5 and "@trestle(deadline=" in k5
    assert "async def" in k5 and "refused at publication" in k5

    # R-J: declared packages are recorded, not snapshotted, beside the `packages` keyword
    rj = plugins[plugins.index("`packages` names") :]
    assert "recorded, not snapshotted" in rj[:400]
    assert "execution.provenance_mismatch" in rj[:1500]

    # the Cursor skill carries the same facts in short form
    skill = _read(".cursor/skills/trestle/SKILL.md")
    assert "admission.budget_does_not_fit" in skill and "recorded, not snapshotted" in skill
