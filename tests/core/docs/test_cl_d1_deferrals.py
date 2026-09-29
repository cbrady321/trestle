"""L.CL-D1.3: the nine core deferrals, in CM-8's schema, cited and unproven until their band closes.

Reads only `tests/proof/deferrals.toml`, `tests/proof/labels.d`, `tests/proof/trailers.py` and the
rendered ledger, never the fence. No `closes_at` value is validated here: each checkpoint's CM-8
band rule does (I-C1-10).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.proof import deferrals as deferrals_mod
from tests.proof import ledger as ledger_mod
from tests.proof import meta as meta_mod
from tests.proof import results as results_mod
from tests.proof import trailers as trailers_mod

REPO_ROOT = Path(__file__).resolve().parents[3]

# plans/core.md ## Coverage (c): label -> closes_at
CORE_DEFERRALS = {
    "WR-OWN-8:environment-lease": "SL-8",
    "A1.3:single": "SL-9",
    "WR-OWN-1:created-container-released": "RB-12",
    "WR-OWN-3:created-container-stopped-on-success": "RB-12",
    "WR-TERM-5:tree-size": "TR-6",
    "WR-PROOF-10:slice-row-doc-syncs": "CZ",
    "WR-CANCEL-3:later-run-admitted-after-answer": "SL-8",
    "WR-CANCEL-5:adapter-contract-suite": "NW-2",
    "WR-OWN-6:docker-inventory": "RB-12",
}
DECLARED_BY = "L.CL-D1.3"


def problems(entries: list[dict]) -> list[str]:
    """Why `entries` are not the core deferrals; empty when they are."""
    found: list[str] = []
    core = [e for e in entries if e.get("from_step") == "core"]
    labels = [e["label"] for e in core]
    for label in sorted(set(labels)):
        if labels.count(label) > 1:
            found.append(f"{label}: {labels.count(label)} entries, expected exactly one")
    for label in sorted(set(CORE_DEFERRALS) - set(labels)):
        found.append(f"{label}: no entry")
    for label in sorted(set(labels) - set(CORE_DEFERRALS)):
        found.append(f"{label}: not one of the core deferrals")
    for entry in core:
        label = entry["label"]
        if not str(entry.get("citation", "")).strip():
            found.append(f"{label}: no citation")
        if entry.get("declared_by") != DECLARED_BY:
            found.append(
                f"{label}: declared_by {entry.get('declared_by')!r}, expected {DECLARED_BY}"
            )
        if "until" in entry:
            found.append(f"{label}: carries `until`, but no upstream precondition is open (CSC-15)")
        if label in CORE_DEFERRALS and entry.get("closes_at") != CORE_DEFERRALS[label]:
            found.append(f"{label}: closes_at {entry.get('closes_at')!r}")
    return found


def _write(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body))
    return path


def _entry(label: str, **overrides: str) -> str:
    fields = {
        "label": label,
        "from_step": "core",
        "closes_at": CORE_DEFERRALS.get(label, "SL-8"),
        "citation": "planted citation",
        "declared_by": DECLARED_BY,
    }
    fields.update(overrides)
    return "[[deferral]]\n" + "".join(f'{k} = "{v}"\n' for k, v in fields.items())


def _all_entries() -> str:
    return "\n".join(_entry(label) for label in CORE_DEFERRALS)


def _ledger_statuses() -> dict[str, str]:
    """Ledger statuses over whatever results exist; empty when none are countable."""
    try:
        report = ledger_mod.render()
    except ledger_mod.VacuousLedgerError:
        return {}
    return {label: str(entry["status"]) for label, entry in report.items()}


def test_core_deferrals_cited_and_unproven() -> None:
    entries = deferrals_mod.load_deferrals()  # P0's exact CM-8 schema; a withdrawn key raises
    assert problems(entries) == []
    assert len([e for e in entries if e["from_step"] == "core"]) == 9

    statuses = _ledger_statuses()
    declared = {label["id"]: label for label in meta_mod._load_all_labels()}
    for label, closes_at in CORE_DEFERRALS.items():
        if trailers_mod.landing(closes_at, cwd=REPO_ROOT) is not None:
            continue  # the band is on master: its checkpoint asserts the PROVEN side (CM-8)
        assert statuses.get(label, ledger_mod.UNPROVEN) == ledger_mod.UNPROVEN, label
        declaration = declared.get(label, {})
        posture = declaration.get("posture")
        if declaration.get("step") == "single":
            # the single phase declares the labels it will claim at their closing merge (L.SV-0.2,
            # `claim`); the status assertion above is what holds until a node registers one
            assert posture in ("claim", "deferred"), f"{label}: posture {posture!r}"
            continue
        assert posture in (None, "deferred"), f"{label}: claimed with posture {posture!r}"


def test_unclaimed_deferral_renders_unproven_beside_a_proven_label(tmp_path: Path) -> None:
    """The rendering the test above relies on: an unclaimed label is UNPROVEN, never PROVEN."""
    results_dir = tmp_path / "results"
    results_mod.write_record(
        results_mod.Record(
            nodeid="tests/planted.py::test_other",
            outcome="passed",
            gate="ci-test",
            venue="CI",
            interpreter="3.12.9",
            labels=["WR-OTHER-1:planted"],
        ),
        results_dir=results_dir,
    )
    report = ledger_mod.render(results_dir=results_dir)
    assert report["WR-OTHER-1:planted"]["status"] == ledger_mod.PROVEN
    for label in CORE_DEFERRALS:
        assert report.get(label, {"status": ledger_mod.UNPROVEN})["status"] == ledger_mod.UNPROVEN


def test_planted_bad_deferrals_each_fail(tmp_path: Path) -> None:
    good = _write(tmp_path / "good.toml", _all_entries())
    assert problems(deferrals_mod.load_deferrals(good)) == []

    uncited = _write(
        tmp_path / "uncited.toml", _all_entries() + "\n" + _entry("WR-X-1:uncited", citation=" ")
    )
    assert "WR-X-1:uncited: no citation" in problems(deferrals_mod.load_deferrals(uncited))
    missing_key = "\n".join(
        _entry(label).replace('citation = "planted citation"\n', "") for label in CORE_DEFERRALS
    )
    with pytest.raises(deferrals_mod.DeferralLoadError):
        deferrals_mod.load_deferrals(_write(tmp_path / "nokey.toml", missing_key))

    duplicate = _write(
        tmp_path / "dup.toml", _all_entries() + "\n" + _entry("WR-OWN-8:environment-lease")
    )
    assert "WR-OWN-8:environment-lease: 2 entries, expected exactly one" in problems(
        deferrals_mod.load_deferrals(duplicate)
    )

    withdrawn = _write(
        tmp_path / "withdrawn.toml",
        _all_entries() + "\n" + _entry("WR-X-2:withdrawn") + 'register_id = "TM-C5-1"\n',
    )
    with pytest.raises(deferrals_mod.DeferralLoadError):
        deferrals_mod.load_deferrals(withdrawn)

    short = _write(
        tmp_path / "short.toml",
        "\n".join(_entry(label) for label in list(CORE_DEFERRALS)[:-1]),
    )
    assert "WR-OWN-6:docker-inventory: no entry" in problems(deferrals_mod.load_deferrals(short))
