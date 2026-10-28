"""L.CL-D1.2: the containment boundary is disclosed (WR-CANCEL-6) and no doc claims more (K-19).

Two boundaries sit outside the containment and evidence-integrity guarantee: a descendant that
double-forks out of attribution between two ancestry snapshots, and a run started by a version that
recorded no process identity (with its pre-upgrade `ps` check). RV-1 reviews the adequacy of the
wording at L.J-CORE.1; this file is its automated presence check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.proof import kdoc

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS = REPO_ROOT / "docs"
PS_CHECK = "ps -ax -o pid,command | grep trestle.child.main"

# Each boundary is present when every phrase of its group appears (case-insensitive, whitespace
# collapsed) somewhere in the doc set.
BOUNDARIES = {
    "double-fork escape": ("double-fork", "between two", "snapshot", "outside the containment"),
    "pre-identity run": (
        "recorded no process identity",
        "interrupted",
        "unconfirmed",
        "never clean",
        "no signal",
        PS_CHECK,
    ),
}

CLAIM = re.compile(
    r"\b(?:every|all|any)\s+(?:(?:child|descendant|spawned|plugin|worker)\s+)?"
    r"(?:process(?:es)?|descendants?|children)\b",
    re.I,
)
CONTAINMENT_VERB = re.compile(
    r"\b(?:kill|kills|killed|stop|stops|stopped|terminate|terminates|contain|contains|reap|reaps"
    r"|end|ends|clean|cleans|signal|signals)\b",
    re.I,
)
# A universal quantifier over processes is only acceptable when the same sentence scopes it to what
# Trestle can see. Negations and "only" do not scope it: the F-1 sentence ("every process the run
# started is gone, or the answer says it could not be confirmed ... only when ... never ...")
# carried them and still promised more than the code does.
QUALIFIER = re.compile(r"\b(?:attribut\w*|recorded)\b", re.I)
# Sentences about reachability (a port) or about signalling nothing are not claims of containment.
NON_CLAIM = re.compile(r"\b(?:connect|reach|port|no signal|never signals?)\b", re.I)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def missing_boundaries(texts: list[str]) -> list[str]:
    """The boundaries the doc set does not disclose (empty when both are present)."""
    flat = _flat(" ".join(texts)).lower()
    return [
        name
        for name, phrases in BOUNDARIES.items()
        if not all(_flat(phrase).lower() in flat for phrase in phrases)
    ]


def universal_claims(text: str) -> list[str]:
    """Sentences with a universal quantifier over processes and a containment verb, unless the
    sentence scopes the quantifier by 'attributable' or 'recorded'."""
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n\s*\n", _flat_keep_breaks(text)):
        if (
            CLAIM.search(sentence)
            and CONTAINMENT_VERB.search(sentence)
            and not NON_CLAIM.search(sentence)
            and not QUALIFIER.search(sentence)
        ):
            found.append(sentence.strip())
    return found


def _flat_keep_breaks(text: str) -> str:
    """Join hard-wrapped lines of one paragraph; keep paragraph breaks."""
    return "\n\n".join(_flat(paragraph) for paragraph in re.split(r"\n\s*\n", text))


def _doc_texts() -> dict[str, str]:
    return {
        str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8")
        for path in sorted(DOCS.rglob("*.md"))
    }


@pytest.mark.proves(
    "WR-CANCEL-6",
    "WR-CANCEL-6:boundary-disclosed-no-universal-claim",
    "core",
    "core",
    "INSPECT",
    "CI",
)
def test_containment_boundary_disclosed_and_no_universal_claim() -> None:
    docs = _doc_texts()
    assert missing_boundaries(list(docs.values())) == []

    security = docs["docs/security.md"]
    assert missing_boundaries([security]) == []  # the full statement lives in security.md
    assert "not a sandbox" in security  # the existing statement is preserved

    offenders = {name: universal_claims(text) for name, text in docs.items()}
    assert {name: found for name, found in offenders.items() if found} == {}

    # planted: a doc set missing either boundary fails, one carrying an unqualified claim fails
    assert missing_boundaries(["A descendant that double-forks between two snapshots."]) == [
        "double-fork escape",
        "pre-identity run",
    ]
    only_double_fork = "\n".join(
        [
            "A double-fork between two snapshots is outside the containment guarantee.",
        ]
    )
    assert missing_boundaries([only_double_fork]) == ["pre-identity run"]
    only_pre_identity = (
        "A run that recorded no process identity is finalized interrupted, unconfirmed, "
        f"never clean, and no signal is sent. Check: {PS_CHECK}"
    )
    assert missing_boundaries([only_pre_identity]) == ["double-fork escape"]
    assert universal_claims("Trestle kills every process a plugin starts.") != []
    assert universal_claims("Trestle stops all processes it launched.") != []
    assert universal_claims("Trestle stops every process attributable to the run.") == []
    assert universal_claims("Any process on this machine can reach the port.") == []
    # the RV-1 F-1 sentence: negations and `only` later in the sentence must not excuse it
    old_f1 = (
        "By the terminal row every process the run started is gone, or the answer says it could "
        "not be confirmed: `cleanup.processes` is `released` only when the supervisor confirmed "
        "the group gone, otherwise `unknown` (never a clean claim without confirmation)."
    )
    assert universal_claims(old_f1) != []
    assert (
        universal_claims(
            "By the terminal row every process attributable to the run is gone, or the answer "
            "says it could not be confirmed."
        )
        == []
    )


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-19", "core", "core", "INSPECT", "CI")
def test_k19_documented_in_each_mapped_doc() -> None:
    k19 = next(k for k in kdoc.load_k_doc_map() if k["id"] == "K-19")
    assert k19["landing_merge"] == "CL-D1"
    docs = list(k19["docs"]) + ["docs/security.md"]  # MC-05's doc plus the full statement
    assert "docs/agents.md" in docs
    for doc in docs:
        text = _flat((REPO_ROOT / doc).read_text(encoding="utf-8"))
        assert "K-19" in text, doc
        assert "recorded no process identity" in text, doc
        assert "unconfirmed" in text and "never clean" in text, doc
        assert PS_CHECK in text, doc
