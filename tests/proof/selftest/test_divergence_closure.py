"""Selftest for `differ d1 --closure` (L.CZ.4; C-PROOF-HONEST:divergence-ledger-closed).

Planted ledgers and contexts only. The live command `python -m tests.proof.differ d1 --closure`
needs every due merge landed, so it is accepted at the CZ PR head; the closed ledger's permanent
rules (witness and reference, never the landing check) run in every later `d1`.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from tests.proof import differ as differ_mod

ROOT = Path(__file__).resolve().parents[3]


def _entry(eid: str, **kw) -> dict:
    base = {
        "id": eid,
        "facet": "internal",
        "row_or_k": "WR-A-1",
        "docs": [],
        "due_checkpoint": "CS-3",
        "direction": "change",
        "authorized_by": "a plan citation",
    }
    return {**base, **kw}


def _ctx(**kw) -> differ_mod.ClosureContext:
    base = {
        "code_values": {"execution.interrupted", "admission.budget_does_not_fit"},
        "rows": {"WR-A-1", "WR-COMPAT-3"},
        "k_landing": {"K-1": "CK-1", "K-16": ""},
        "landed": lambda merge_id: merge_id in {"CS-3", "CK-1", "CK-3/4", "SV-4"},
    }
    return differ_mod.ClosureContext(**{**base, **kw})


def _selector(entry_id: str) -> differ_mod.Selector:
    return differ_mod.Selector(entry_id, "internal", "key", "$.kinds.group_stop")


def _proves():
    return pytest.mark.proves(
        "C-PROOF-HONEST", "C-PROOF-HONEST:divergence-ledger-closed", "core", "CZ", "LOGIC", "CI"
    )


@_proves()
def test_planted_unappeared_entry_fails_closure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    good = [
        _entry("K-ROW"),
        _entry("K-1", row_or_k="K-1", due_checkpoint="CK-1"),
        _entry("K-16", row_or_k="K-16", due_checkpoint="post-P0 (never enumerated)"),
        _entry("PART1", row_or_k="WR-COMPAT-3", direction="additive"),
        _entry("D-ITEM", row_or_k="D-c", due_checkpoint="none (conditional, unbuilt)"),
        _entry("ADD-seed", row_or_k="none", direction="additive", due_checkpoint="post-P0 (x)"),
        _entry("ADD-code-ok", row_or_k="V-11", direction="additive", codes=["execution.*"]),
        _entry(
            "ADD-sel-ok",
            row_or_k="MC-CORE-04",
            direction="additive",
            keys=["$.kinds.group_stop"],
        ),
    ]
    used = {_selector("ADD-sel-ok")}
    assert differ_mod.closure_problems(good, _ctx(used=used)) == []

    # each planted defect fails, and names its entry
    planted = {
        "unappeared code": _entry(
            "ADD-code-gone", row_or_k="V-11", direction="additive", codes=["execution.gone"]
        ),
        "unused selector": _entry(
            "ADD-sel-gone", row_or_k="V-11", direction="additive", keys=["$.kinds.group_stop"]
        ),
        "due merge not landed": _entry("K-LATE", due_checkpoint="TR-6 (leaf L.TR-6.1)"),
        "K-item not landed": _entry("K-2", row_or_k="K-1", due_checkpoint="none (x)"),
        "unnamed": _entry("NOBODY", row_or_k="WR-NOT-A-ROW"),
        "none on a change": _entry("CHANGE-NONE", row_or_k="none"),
        "no authorization": _entry("NO-AUTH", authorized_by=" "),
    }
    ctx = _ctx(used=used, k_landing={"K-1": "CK-9"})
    for label, entry in planted.items():
        found = differ_mod.closure_problems(good + [entry], ctx)
        mine = [p for p in found if p.startswith(entry["id"] + ":")]
        assert mine, f"{label}: {found}"
    # the planted unappeared entry alone: closure exits 1 through the command, 0 without it
    monkeypatch.setattr(differ_mod, "ledger_closed", lambda path=None: True)

    def fake_run(_args) -> differ_mod.D1Run:
        return differ_mod.D1Run(entries=entries, currents={}, used=used)

    def fake_live(codes, used_, *, check_landing):
        return _ctx(used=used_, landed=(lambda m: m in {"CS-3"}) if check_landing else None)

    monkeypatch.setattr(differ_mod, "run_d1", fake_run)
    monkeypatch.setattr(differ_mod.ClosureContext, "live", staticmethod(fake_live))
    args = Namespace(strict=True, facet=[], closure=True)
    entries = [_entry("K-ROW")]
    assert differ_mod.cmd_d1(args) == 0
    entries = [_entry("K-ROW"), planted["unappeared code"]]
    assert differ_mod.cmd_d1(args) == 1
    assert "ADD-code-gone: code 'execution.gone' is defined nowhere" in capsys.readouterr().out
    # the landing check belongs to `--closure` alone: a later plain d1 does not repeat it
    entries = [_entry("K-LATE", due_checkpoint="TR-6")]
    assert differ_mod.cmd_d1(Namespace(strict=True, facet=[], closure=True)) == 1
    assert differ_mod.cmd_d1(Namespace(strict=True, facet=[], closure=False)) == 0


def test_the_committed_ledger_is_closed_and_golden_s0_stays(tmp_path: Path) -> None:
    assert differ_mod.ledger_closed()
    assert not differ_mod.ledger_closed(tmp_path / "absent.toml")
    open_ledger = tmp_path / "divergence.toml"
    open_ledger.write_text('[[entry]]\nid = "X"\n')
    assert not differ_mod.ledger_closed(open_ledger)
    # golden S0 and the D2 machinery are permanent: nothing here deletes or rewrites them
    assert (ROOT / "tests" / "fixtures" / "golden" / "s0" / "nodeids.txt").exists()
    assert differ_mod.D2_EXCEPTIONS_PATH.exists()
    assert differ_mod.D2_DRIVER_PATH.exists()
    # the closed marker is not an entry, and the loader still reads every entry
    assert len(differ_mod.load_divergence()) >= 150


def test_closure_reads_the_live_committed_ledger_without_landing(monkeypatch) -> None:
    """The permanent half of the rules holds on this head: every witnessable entry appears and every
    reference resolves (the due-merge landing check is `--closure`'s, at the CZ PR head)."""
    entries = differ_mod.load_divergence()
    ctx = differ_mod.ClosureContext.live(None, set(), check_landing=False)
    problems = differ_mod.closure_problems(entries, ctx)
    # with no facet output there is no code surface and no used selector to witness against, so
    # only the reference rules can be judged here
    reference_only = [
        p for p in problems if "appears nowhere" not in p and "defined nowhere" not in p
    ]
    assert reference_only == []
