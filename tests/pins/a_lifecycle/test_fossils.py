"""Lane-A S0 fossils (L.P0-1A.5): every MANIFEST state is committed and
projects, through the independent `tests.proof.records` seam, as declared.

`crashed` is a real run-ledger terminal kind with no S0 producer: recorded
absent (K-13), never present.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.pins.a_lifecycle import fossil_producers as fp
from tests.proof import fossils, records
from trestle.server.ledger import RunLedger, ledger_path

S0 = fp.FOSSILS_ROOT / fp.BAND
SPINE_TERMINALS = {"succeeded": "succeeded", "failed": "failed"}
# States a later leaf of this lane commits; required present once it lands.
LATER_LEAF_STATES: set[str] = {"straddle"}


def _run_dir(state: str) -> Path:
    dirs = sorted((S0 / state / "home" / "runs").glob("*/r_*"))
    assert len(dirs) == 1, (state, dirs)
    return dirs[0]


def _meta(state: str) -> dict[str, object]:
    return json.loads((_run_dir(state) / "evidence" / "meta.json").read_text(encoding="utf-8"))


def test_s0_projection_matches_manifest() -> None:
    states = fossils.load_states(fp.FOSSILS_ROOT)
    assert {sid for sid, (band, _e) in states.items() if band == fp.BAND} == set(
        fp.STATE_IDS
    ) | set(SPINE_TERMINALS) | LATER_LEAF_STATES | {"crashed"}
    for sid, (_band, entry) in states.items():
        if entry.get("absent"):
            assert not (S0 / sid).exists(), f"{sid}: declared absent (K-13) but a fossil exists"
            continue
        if sid in LATER_LEAF_STATES and not (S0 / sid).exists():
            continue
        if sid in SPINE_TERMINALS:
            assert records.node_record(_run_dir(sid)).terminal == SPINE_TERMINALS[sid]
            continue
        if sid in fp.DECLARED:
            assert fp.declared_projection(_run_dir(sid)) == fp.DECLARED[sid], sid


def test_seam_reads_every_s0_fossil() -> None:
    """Every committed S0 run directory reads through the records seam with
    no product reader, and never raises."""
    read = 0
    for run_dir in sorted(S0.glob("*/home/runs/*/r_*")):
        records.ledger_rows(run_dir)
        records.node_record(run_dir)
        read += 1
    assert read >= len(fp.STATE_IDS) + len(SPINE_TERMINALS)


def test_state_specific_evidence() -> None:
    assert _meta("interrupted")["recovered"] is True
    assert _meta("partial_limits")["limits_exceeded"]
    assert _meta("too_large")["result_state"] == "too_large"
    assert _meta("invalid_synthesized")["result_state"] == "invalid"
    assert (_run_dir("invalid_synthesized") / "evidence" / "result.json").exists()
    assert not (_run_dir("invalid_synthesized") / "evidence" / "result.index").exists()
    assert _meta("artifacts_present")["artifact_count"] >= 1
    assert (_run_dir("cancelled") / "work" / "cancel.flag").exists()
    assert _meta("worker_exit")["classification"] == "worker_exit"
    assert (S0 / "idempotency_keyed" / "home" / "idempotency.json").exists()
    created = RunLedger.open(ledger_path(_run_dir("idempotency_keyed"))).last_kind("created")
    assert created is not None and created["idempotency_key"] == "fossil-key"
    assert _run_dir("torn_tail").exists()
    raw = ledger_path(_run_dir("torn_tail")).read_bytes()
    assert not raw.endswith(b"\n")
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw.rsplit(b"\n", 1)[-1])
    raw = ledger_path(_run_dir("newline_less_tail")).read_bytes()
    assert not raw.endswith(b"\n")
    json.loads(raw.rsplit(b"\n", 1)[-1])


def test_producers_run_under_the_manifest_generate_cli(tmp_path: Path) -> None:
    """The producer specs a MANIFEST flip would name run under
    `python -m tests.proof.fossils generate` and project as declared."""
    from argparse import Namespace

    subset = ["created", "admitted", "started", "execution_ended", "newline_less_tail"]
    root = tmp_path / "fossils"
    (root / "s0").mkdir(parents=True)
    manifest = "".join(
        f'[[state]]\nid = "{sid}"\n'
        f'producer = "tests.pins.a_lifecycle.fossil_producers:produce_{sid}"\n'
        "absent = false\n\n"
        for sid in subset
    )
    (root / "s0" / "MANIFEST.toml").write_text(manifest)
    rc = fossils.cmd_generate(
        Namespace(checkpoint="s0", states=",".join(subset), fossils_root=str(root))
    )
    assert rc == 0
    for sid in subset:
        run_dir = next((root / "s0" / sid / "home" / "runs").glob("*/r_*"))
        assert fp.declared_projection(run_dir) == fp.DECLARED[sid], sid
    shutil.rmtree(root)
