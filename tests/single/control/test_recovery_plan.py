"""L.SV-3.8: recovery reads `spec.plan`: a known format is folded and swept in plan release rank
(never `nothing_created` for an unconfirmed target, B2-C9); a plan-less root is the implicit
depth-1 plan (B2-C1); an unknown format is finalized interrupted with its cleanup unknown and
nothing released. A6.2:single is the real server, SIGKILLed mid-run with a seeded lane."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import ancestry, mcp_host, tolerances
from tests.single.control import sweep_stub as sw
from tests.single.record import support as rec
from trestle.common import clock
from trestle.common import lane_format as lf
from trestle.common.fsutil import atomic_write_json
from trestle.common.plan import carving
from trestle.common.plan.compiler import implicit_depth1_plan
from trestle.common.types import RunView
from trestle.server.config import OperatorLimits
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path
from trestle.server.recovery import recover_run_dir, seed_interrupted_run

ENGINE = "/opt/engine/ctl"  # the stub engine's executable (never a real file)


@pytest.fixture(autouse=True)
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", tolerances.SETTLE_SHORT_S)
    monkeypatch.setattr(clock, "kill", tolerances.SETTLE_SHORT_S)


def _plan_json(release_slice: float = 0.0) -> dict[str, Any]:
    plan = carving.attach(implicit_depth1_plan("echo"), {}, release_slice)
    loaded = json.loads(plan.to_json())
    assert isinstance(loaded, dict)
    return loaded


def _write_lane(run_dir: Path, *entries: lf.Entry) -> None:
    lines = [lf.encode_entry(e, i + 1) + b"\n" for i, e in enumerate(entries)]
    lf.lane_path(run_dir).write_bytes(b"".join(lines))


def _argv_descriptor(name: str) -> lf.ArgvRelease:
    return sw.descriptor(name, exe=ENGINE)


def _seed(
    home: Path,
    run_id: str,
    *,
    plan: dict[str, Any] | None,
    entries: tuple[lf.Entry, ...] = (),
) -> Path:
    """A started run (no identity rows: B2-C11 branch (i)), its spec carrying `plan`, and a lane."""
    run_dir = seed_interrupted_run(home, run_id, last_kind="started")
    spec: dict[str, Any] = {"plugin": "echo", "snapshot_id": "snap_test"}
    if plan is not None:
        spec["plan"] = plan
    atomic_write_json(evidence_dir(run_dir) / "spec.json", spec)
    if entries:
        _write_lane(run_dir, *entries)
    return run_dir


def _issue(name: str, release: lf.ReleaseDescriptor) -> lf.IssueEntry:
    return rec.issue_entry(effect=name, release=release)


def _confirm(name: str, status: lf.ConfirmationStatus) -> lf.ConfirmationEntry:
    return rec.confirmation_entry(effect=name, status=status)


def _kinds(run_dir: Path) -> list[str]:
    return [str(row["kind"]) for row in support.rows(run_dir)]


def _limits() -> OperatorLimits:
    return OperatorLimits(release_executables=frozenset({ENGINE}), sweep_parallelism=1)


def test_pin_recovery_ignores_spec_plan() -> None:
    """The own pin (inverted by the tests below): at `wr-ckpt/core`'s recovery a `plan` in the spec
    changes nothing. Kept here as the one behaviour that must survive: a run with no lane and a
    known plan recovers exactly as one with none (same rows, no sweep rows)."""
    kernel = support.spine_kernel()
    with_plan = _seed(kernel.home, "r_pin_with_plan", plan=_plan_json())
    without = _seed(kernel.home, "r_pin_without_plan", plan=None)
    recover_run_dir(with_plan)
    recover_run_dir(without)
    assert _kinds(with_plan) == _kinds(without)
    assert _kinds(with_plan)[-1] == "interrupted"
    assert "sweep_disposition" not in _kinds(with_plan)


@pytest.mark.proves("WR-CANCEL-3", "WR-CANCEL-3:unknown-plan-format", "A", "single", "PROC", "BOTH")
def test_unknown_plan_format_interrupted_cleanup_unknown() -> None:
    kernel = support.spine_kernel()
    plan = _plan_json()
    plan["format_version"] = 99  # a format this reader does not know
    entries = (
        rec.plan_entry(),
        _issue("a", _argv_descriptor("a")),
        _confirm("a", lf.ConfirmationStatus.APPLIED),
    )
    run_dir = _seed(kernel.home, "r_unknown_format", plan=plan, entries=entries)
    engine = sw.Engine(present={"a"})
    recover_run_dir(run_dir, limits=_limits(), sweep_io=engine.io())
    kinds = _kinds(run_dir)
    assert kinds[-1] == "interrupted"  # never left non-terminal, never succeeded
    assert engine.calls == []  # nothing was swept, so nothing released
    assert "sweep_disposition" not in kinds and kinds.count("sweep_skipped") == 1
    (row,) = support.rows_of(run_dir, "sweep_skipped")
    assert row["reason"] == "unknown_plan_format"
    view = kernel.control.project.status(run_dir.name)
    assert isinstance(view, RunView)
    assert view.cleanup is not None and view.cleanup.processes == "unknown"
    # a plan whose digest does not verify is treated the same way
    tampered = _plan_json()
    tampered["release_slice"] = 5.0
    other = _seed(kernel.home, "r_tampered_plan", plan=tampered, entries=entries)
    recover_run_dir(other, limits=_limits(), sweep_io=engine.io())
    assert support.rows_of(other, "sweep_skipped") and engine.calls == []


def test_planless_root_swept_as_implicit_depth1() -> None:
    kernel = support.spine_kernel()
    entries = (
        rec.plan_entry(),
        _issue("a", _argv_descriptor("a")),
        _confirm("a", lf.ConfirmationStatus.APPLIED),
        _issue("b", _argv_descriptor("b")),
        _confirm("b", lf.ConfirmationStatus.APPLIED),
    )
    run_dir = _seed(kernel.home, "r_planless_sweep", plan=None, entries=entries)
    engine = sw.Engine(present={"a", "b"})
    recover_run_dir(run_dir, limits=_limits(), sweep_io=engine.io())
    kinds = _kinds(run_dir)
    assert kinds[-1] == "interrupted"
    rows = support.rows_of(run_dir, "sweep_disposition")
    # every target at rank 0, reverse issue order: b then a, both released
    assert [r["target"]["effect"] for r in rows] == ["b", "a"]
    assert {r["disposition"] for r in rows} == {"released"}
    assert engine.roles("b") == ["obs", "stop", "rm", "obs"]
    assert (
        kinds.index("lane_folded") < kinds.index("sweep_disposition") < kinds.index("interrupted")
    )


def test_recovery_unconfirmed_target_never_nothing_created() -> None:
    kernel = support.spine_kernel()
    entries = (
        rec.plan_entry(),
        _issue("u", _argv_descriptor("u")),  # issued, never confirmed
        _issue("c", _argv_descriptor("c")),
        _confirm("c", lf.ConfirmationStatus.APPLIED),
    )
    run_dir = _seed(kernel.home, "r_unconfirmed", plan=_plan_json(), entries=entries)
    engine = sw.Engine()  # nothing is present: both look absent on the first observation
    recover_run_dir(run_dir, limits=_limits(), sweep_io=engine.io())
    dispositions = {
        r["target"]["effect"]: r["disposition"]
        for r in support.rows_of(run_dir, "sweep_disposition")
    }
    # the confirmed one is released (observed absent); the unconfirmed one is unknown, never
    # nothing_created, so a re-sweep cannot downgrade what an earlier interrupted sweep stopped
    assert dispositions == {"c": "released", "u": "unknown"}
    assert "nothing_created" not in dispositions.values()


def test_recovery_not_swept_twice() -> None:
    kernel = support.spine_kernel()
    entries = (
        rec.plan_entry(),
        _issue("a", _argv_descriptor("a")),
        _confirm("a", lf.ConfirmationStatus.APPLIED),
    )
    run_dir = _seed(kernel.home, "r_no_double_sweep", plan=_plan_json(), entries=entries)
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append("group_stop", run_id=run_dir.name, confirmed_gone=True, method="signal")
    ledger.append(
        "sweep_disposition",
        run_id=run_dir.name,
        target={"path": "", "effect": "a"},
        form="argv_release",
        disposition="released",
    )  # the conductor's, before the crash
    engine = sw.Engine(present={"a"})
    recover_run_dir(run_dir, limits=_limits(), sweep_io=engine.io())
    assert engine.calls == [] and len(support.rows_of(run_dir, "sweep_disposition")) == 1
    assert len(support.rows_of(run_dir, "group_stop")) == 1  # not stopped twice either


# -- the real thing: a server SIGKILLed mid-run, with a test-seeded lane -------------------------


def _seed_live_lane(run_dir: Path, marker: Path) -> None:
    """An InRunGroup claim and its created marker, a durable target, and an unconfirmed argv
    target whose executable would leave `marker` behind if the sweep ever ran it."""
    script = run_dir / "work" / "tmp" / "release-probe"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    script.chmod(0o755)
    unlisted = lf.ArgvRelease(
        executable=str(script),
        observe_argv=("probe",),
        observe_ok_exit=frozenset({0}),
        stop_argv=("probe",),
        timeout=timedelta(seconds=tolerances.SETTLE_LONG_S),
    )
    _write_lane(
        run_dir,
        rec.plan_entry(),
        _issue("grp", lf.InRunGroup()),
        _confirm("grp", lf.ConfirmationStatus.APPLIED),
        _issue("vol", lf.Durable(lf.DurableOwner.HOST)),
        _confirm("vol", lf.ConfirmationStatus.APPLIED),
        _issue("ext", unlisted),
    )


@pytest.mark.proves("A6.2", "A6.2:single", "A", "single", "PROC", "BOTH")
def test_server_killed_mid_run_plan_rank_sweep_after_restart(tmp_path: Path) -> None:
    marker = tmp_path / "release-ran"
    with mcp_host.McpHost() as host:
        run_id = ""
        try:
            shutil.copy(support.SPINE_PLUGIN_DIR / "tree.py", host.home / "plugins")
            started = host.call("run", {"plugin": "tree", "wait_ms": 0})
            run_id = started["run_id"]
            (run_dir,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
            support.wait_ready(run_dir)
            tree = support.Tree(marker=run_id, live=support.marked(run_id))
            assert tree.wrapper and tree.child and len(tree.descendants) == 2, tree.live
            assert support.wait_until(
                lambda: len(support.rows_of(run_dir, "process_identity")) >= 4,
                tolerances.JOIN_WAIT_S,
            )
            _seed_live_lane(run_dir, marker)
            host.kill_server()
            host.restart()

            kinds = _kinds(run_dir)
            # (MC-13) every attributable process is gone once recovery finished
            assert support.wait_until(
                lambda: not support.alive_marked(tree.live, run_id), tolerances.PROC_WAIT_S
            )
            # the lane was folded before `interrupted`, and swept in the record's order after it
            assert kinds[-1] == "interrupted"
            first_swept = kinds.index("sweep_disposition")
            assert kinds.index("lane_folded") < first_swept < kinds.index("error_record")
            assert kinds.index("group_stop") < kinds.index("lane_folded")
            dispositions = {
                r["target"]["effect"]: r["disposition"]
                for r in support.rows_of(run_dir, "sweep_disposition")
            }
            # the durable target is left durable; the argv target is unconfirmed and unlisted:
            # unknown, never run (its marker was never written), never nothing_created
            assert dispositions == {"vol": "left_durable", "ext": "unknown"}
            assert not marker.exists()
            (stop,) = support.rows_of(run_dir, "group_stop")
            assert stop["confirmed_gone"] is True and stop["method"] == "recovery"
        finally:
            if run_id:
                ancestry.reap(support.marked(run_id))
