"""L.SV-3.4: admission compiles a depth-1 plan for every root and writes it into `spec.json`
(MC-20); the plan digest joins run identity; clock publishes the reserve and the release slice;
`write_admitted_run` is the post-refusal half (MC-B2-08) with the TM-B2-8 audit hook."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from tests.proof import tolerances
from tests.single.control import support
from trestle.common import clock
from trestle.common.plan import bounds, carving
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.types import AdmitRequest, AdmitResultAdmitted
from trestle.server.admission import plan_for_admission, write_admitted_run
from trestle.server.idempotency import IdempotencyStore
from trestle.server.main import Kernel
from trestle.server.snapshots import load_declared_tree

# every `spec.json` key a run wrote at `wr-ckpt/core`, before plans
CORE_SPEC_KEYS = {
    "plugin",
    "version",
    "snapshot_id",
    "args",
    "args_hash",
    "source_sha256",
    "schema_sha256",
    "manifest_sha256",
    "python_version",
    "platform",
    "summary_budget",
    "timeout_s",
    "resolved_artifacts",
    "provenance",
    "deadline",
}


def _kernel(tmp_path: Path, **extra: str) -> Kernel:
    plugins = {"echo": support.ECHO.read_text(encoding="utf-8"), **extra}
    return support.make_kernel(tmp_path, plugins)


def _admit(kernel: Kernel, plugin: str, args: dict[str, object] | None = None) -> Path:
    result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args=args or {}))
    assert isinstance(result, AdmitResultAdmitted), result
    return support.run_dir_of(kernel, result.run_id)


def _plan(run_dir: Path) -> AdmittedPlan:
    raw = support.read_spec(run_dir)["plan"]
    return AdmittedPlan.from_json(json.dumps(raw))


def test_spec_keeps_every_existing_key(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    spec = support.read_spec(_admit(kernel, "echo", {"message": "hi"}))
    assert CORE_SPEC_KEYS <= set(spec)
    assert spec["plugin"] == "echo" and spec["args"] == {"message": "hi"}


def test_every_root_gets_depth1_plan_in_spec(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, wf=support.workflow_source("wf"))
    for name in ("echo", "wf"):
        run_dir = _admit(kernel, name, {"message": "hi"} if name == "echo" else {})
        raw = support.read_spec(run_dir)["plan"]
        assert isinstance(raw, dict) and raw["format_version"] == 1
        plan = _plan(run_dir)
        assert [v.path for v in plan.vertices] == [""] and plan.edges == ()
        assert plan.release_rank == {"": 0}
        assert plan.slices == {}  # one vertex: the root has no carve (V-8 L-3)


def test_plan_carries_declaration_digest_and_plan_digest(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, wf=support.workflow_source("wf"))
    workflow = _plan(_admit(kernel, "wf"))
    tree = load_declared_tree(kernel.registry.get("wf"))  # type: ignore[arg-type]
    assert tree is not None and workflow.declaration_digest == tree.digest
    plain = _plan(_admit(kernel, "echo", {"message": "hi"}))
    assert plain.declaration_digest is None
    for plan in (workflow, plain):
        assert plan.plan_digest is not None and len(plan.plan_digest) == 64
        # from_json verified the digest against the body; it also covers the release slice
        assert plan.plan_digest != carving.attach(plan, {}, plan.release_slice + 1).plan_digest
    assert workflow.plan_digest != plain.plan_digest


def test_plan_carries_lane_entries_and_precedence_ordinal(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, wf=support.workflow_source("wf"))
    for name in ("echo", "wf"):
        plan = _plan(_admit(kernel, name, {"message": "hi"} if name == "echo" else {}))
        assert plan.lane_entries == bounds.LANE_BASE_ENTRIES + 3
        assert plan.precedence_ordinal == {"": 0}
        assert plan.selected_scope == frozenset({()})


def test_release_slice_is_published_only_for_a_release_walk(tmp_path: Path) -> None:
    kernel = _kernel(
        tmp_path,
        plain_wf=support.workflow_source("plain_wf"),
        walk_wf=support.workflow_source("walk_wf", release_timeouts_s=(2,)),
    )
    assert _plan(_admit(kernel, "echo", {"message": "hi"})).release_slice == 0.0
    assert _plan(_admit(kernel, "plain_wf")).release_slice == 0.0
    assert _plan(_admit(kernel, "walk_wf")).release_slice == clock.release_slice


@pytest.mark.proves("WR-PLAN-5", "WR-PLAN-5:plan-digest-in-identity", "A", "single", "PROC", "CI")
def test_plan_digest_joins_run_identity(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, wf=support.workflow_source("wf"))
    snap = kernel.registry.get("wf")
    assert snap is not None
    req = AdmitRequest(plugin="wf", args={})
    home = kernel.home
    base = plan_for_admission(snap, req, 300.0)
    assert isinstance(base, AdmittedPlan)
    other = carving.attach(base, {}, base.release_slice + 1.0)
    assert other.plan_digest != base.plan_digest
    seen: dict[str, tuple[str, str]] = {}
    for label, plan in (("base", base), ("other", other)):
        admitted = write_admitted_run(home, snap, req, plan)
        run_dir = support.run_dir_of(kernel, admitted.run_id)
        spec = support.read_spec(run_dir)
        rehash = hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert admitted.spec_hash == rehash  # the spec, plan digest inside, is the identity
        assert _created_row(run_dir)["spec_hash"] == admitted.spec_hash
        plan_json = spec["plan"]
        assert isinstance(plan_json, dict)
        seen[label] = (str(plan_json["plan_digest"]), admitted.spec_hash)
    # same request, two plans: the plan digest is the difference, and the identity follows it
    assert seen["base"][0] != seen["other"][0]
    assert seen["base"][1] != seen["other"][1]


def _created_row(run_dir: Path) -> dict[str, object]:
    ledger = (run_dir / "evidence" / "ledger.ndjson").read_text(encoding="utf-8").splitlines()
    row = json.loads(ledger[0])
    assert isinstance(row, dict) and row["kind"] == "created"
    return row


@pytest.mark.proves("A2.1", "A2.1", "A", "single", "LOGIC+PROC", "CI")
def test_permuted_args_and_catalog_same_plan_digest(tmp_path: Path) -> None:
    first = support.make_kernel(
        tmp_path / "a",
        {"wf": support.workflow_source("wf"), "echo": support.ECHO.read_text(encoding="utf-8")},
    )
    second = support.make_kernel(
        tmp_path / "b",
        {"echo": support.ECHO.read_text(encoding="utf-8"), "wf": support.workflow_source("wf")},
    )
    digests = {
        _plan(_admit(first, "wf", {"name": "a", "other": "b"})).plan_digest,
        _plan(_admit(second, "wf", {"other": "b", "name": "a"})).plan_digest,
    }
    assert len(digests) == 1


def test_clock_publishes_reserve_and_release_slice() -> None:
    assert clock.release_slice == tolerances.release_slice() > 0
    assert clock.FINALIZATION_RESERVE_S == tolerances.finalization_reserve_s() > 0
    assert clock.sweep_parallelism == tolerances.sweep_parallelism() >= 1
    assert clock.stop_bound == clock.release_slice + clock.grace + clock.kill
    assert tolerances.stop_bound() == clock.stop_bound
    assert clock.finalization_margin == clock.stop_bound + clock.FINALIZATION_RESERVE_S


def _normalized(run_dir: Path) -> dict[str, object]:
    spec = support.read_spec(run_dir)
    spec.pop("deadline")
    return {"spec": spec, "tree": sorted(str(p.relative_to(run_dir)) for p in run_dir.rglob("*"))}


def test_write_admitted_run_equals_admit_for_one_vertex(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, wf=support.workflow_source("wf"))
    snap = kernel.registry.get("wf")
    assert snap is not None
    req = AdmitRequest(plugin="wf", args={"name": "n"}, idempotency_key="key-1")
    admitted = kernel.control.admission.admit(req)
    assert isinstance(admitted, AdmitResultAdmitted)
    via_admit = support.run_dir_of(kernel, admitted.run_id)
    plan = plan_for_admission(snap, req, 300.0)
    assert isinstance(plan, AdmittedPlan)
    direct = write_admitted_run(kernel.home, snap, replace(req, idempotency_key="key-2"), plan)
    via_write = support.run_dir_of(kernel, direct.run_id)
    left, right = _normalized(via_admit), _normalized(via_write)
    assert left["spec"] == right["spec"]
    assert left["tree"] == right["tree"]
    # the created row is the same but for the run id, the hash (deadline) and the key
    rows = {"admit": _created_row(via_admit), "write": _created_row(via_write)}
    for key in ("kind", "plugin", "version", "snapshot_id", "args_hash", "caller_session"):
        assert rows["admit"][key] == rows["write"][key], key
    # the idempotency record is written by both
    store = IdempotencyStore.open(kernel.home)
    assert store.lookup("key-1") is not None and store.lookup("key-2") is not None


def test_write_admitted_run_records_admission_only_when_audit_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = _kernel(tmp_path, wf=support.workflow_source("wf"))
    audit = tmp_path / "audit.ndjson"
    monkeypatch.delenv("TRESTLE_ADMISSION_AUDIT", raising=False)
    _admit(kernel, "wf")
    assert not audit.exists()  # unset: no write, no other effect

    monkeypatch.setenv("TRESTLE_ADMISSION_AUDIT", str(audit))
    run_dir = _admit(kernel, "wf")
    lines = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1  # exactly one line per admission
    line = lines[0]
    assert set(line) == {"run_dir", "vertex_count", "plugin", "pid", "nodeid"}
    assert line["run_dir"] == str(run_dir)
    assert line["vertex_count"] == len(_plan(run_dir).vertices) == 1
    assert line["plugin"] == "wf" and line["pid"] == os.getpid()
    assert line["nodeid"] == os.environ["PYTEST_CURRENT_TEST"]
    _admit(kernel, "wf")
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 2
