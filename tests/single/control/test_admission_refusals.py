"""L.SV-3.5: refusals before a run id (B2-I1: a refusal is not a run) -- a ChoiceNode declared root
(temporary, TM-B2-1; an AllDeclaration root is admitted since L.TR-L.1), a root-slice misfit and a
finalization-margin misfit (B2-C2 (4), (5)), and the schema refusal that names the identifier and
where the valid ones are listed (B2-C2 (1))."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.proof import ancestry
from tests.single.control import support
from trestle.common import clock, codes
from trestle.common.types import AdmitRequest, AdmitResultAdmitted, RequestOutcome, RunView
from trestle.server import idempotency
from trestle.server.main import Kernel

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures" / "workflows"
KEY = "sv35-key"

# a root declared `all` with no child: one vertex, so it is not a composite root (A-1 admits it),
# yet its identifier set is checked against the request (B2-C2 (1))
_IDENTIFIER_ROOT = """
from __future__ import annotations

from datetime import timedelta

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ArgBinding,
    CompletionSource,
    Compose,
    LoopFlags,
    Repeat,
    WorkflowEntry,
)

ROOT = AllDeclaration(
    unit="stack",
    flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
    children=(),
    concurrency=1,
    budget=timedelta(seconds=120),
    identifier_sets={"svcs": frozenset({"db", "api"})},
    arg_bindings=(ArgBinding(arg="services", identifier_set="svcs", filters_children=False),),
    env_key_field=None,
)

ENTRY = WorkflowEntry(root="stack", units={"stack": ROOT}, deadline=timedelta(seconds=300))


@trestle(deadline=300)
def id_root(ctx: Context, services: list[str] | None = None) -> dict[str, str]:
    return {"ok": "yes"}
"""


def _fixture(name: str) -> str:
    return (FIXTURES / f"{name}.py").read_text(encoding="utf-8")


def _tree_fixture(name: str) -> str:
    return (REPO / "tests" / "fixtures" / "trees" / f"{name}.py").read_text(encoding="utf-8")


def _run_dirs(kernel: Kernel) -> list[Path]:
    runs = kernel.home / "runs"
    return sorted(p for p in runs.glob("*/*") if p.is_dir()) if runs.exists() else []


def _descendants() -> set[int]:
    """The pids of every live descendant of this process (by parent pid), but for the `ps` the
    snapshot itself runs."""
    procs = [p for p in ancestry.snapshot() if os.path.basename(p.argv.split(" ", 1)[0]) != "ps"]
    found: set[int] = set()
    frontier = {os.getpid()}
    while frontier:
        frontier = {p.pid for p in procs if p.ppid in frontier} - found
        found |= frontier
    return found


def _refused(kernel: Kernel, plugin: str, args: dict[str, object] | None = None) -> RequestOutcome:
    """Run through the control surface (the path the MCP `run` tool wraps): the outcome, with the
    refusal invariants of B2-I1 checked (no run dir, no idempotency record, no process)."""
    before = _descendants()
    outcome = kernel.control.run(plugin=plugin, args=args or {}, idempotency_key=KEY)
    assert isinstance(outcome, RequestOutcome), outcome
    assert outcome.origin == "admission" and outcome.retryable is False
    assert "run_id" not in outcome.to_dict()
    assert _run_dirs(kernel) == []
    assert idempotency.lookup(kernel.home, KEY) is None
    assert _descendants() == before  # MC-13: no process was spawned
    return outcome


def _admit_result(kernel: Kernel, plugin: str) -> object:
    return kernel.control.admission.admit(AdmitRequest(plugin=plugin, args={}))


def test_all_root_admitted_run_id_minted(tmp_path: Path) -> None:
    """L.TR-L.1 lifted the refusal for an `AllDeclaration` root (TM-B2-1, phase `choice-only`):
    the MC-B3-01 fixture `two_branch_barrier` is admitted, a run id is minted and its run directory
    written, and admission spawns no process (MC-13)."""
    kernel = support.make_kernel(
        tmp_path, {"two_branch_barrier": _tree_fixture("two_branch_barrier")}
    )
    before = _descendants()
    result = _admit_result(kernel, "two_branch_barrier")
    assert isinstance(result, AdmitResultAdmitted), result
    assert [p.name for p in _run_dirs(kernel)] == [result.run_id]
    assert _descendants() == before


def test_choice_root_admitted_run_id_minted(tmp_path: Path) -> None:
    """L.TR-5.3 removed the last temporary refusal (TM-B2-1): a `ChoiceNode` root (the MC-B3-01
    fixture `choice_fake`) is admitted, a run id is minted and its run directory written, and
    admission spawns no process (MC-13); the retired code is not produced."""
    kernel = support.make_kernel(tmp_path, {"choice_fake": _tree_fixture("choice_fake")})
    before = _descendants()
    result = _admit_result(kernel, "choice_fake")
    assert isinstance(result, AdmitResultAdmitted), result
    assert [p.name for p in _run_dirs(kernel)] == [result.run_id]
    assert _descendants() == before


def test_one_vertex_roots_are_not_refused_for_shape(tmp_path: Path) -> None:
    kernel = support.make_kernel(
        tmp_path,
        {"echo": support.ECHO.read_text(encoding="utf-8"), "wf": support.workflow_source("wf")},
    )
    for plugin, args in (("echo", {"message": "hi"}), ("wf", {})):
        result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args=args))
        assert isinstance(result, AdmitResultAdmitted), result


@pytest.mark.proves(
    "WR-DEADLINE-3", "WR-DEADLINE-3:root-slice-misfit-no-run-id", "A", "single", "LOGIC", "CI"
)
def test_misfit_root_slice_refused_no_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = support.make_kernel(
        tmp_path,
        {
            "walk": support.workflow_source("walk", budget_s=30, release_timeouts_s=(2,)),
            "plain": support.workflow_source("plain", budget_s=30),
        },
    )
    # at the published defaults both admit as they do today
    for plugin in ("walk", "plain"):
        assert isinstance(_admit_result(kernel, plugin), AdmitResultAdmitted)
    admitted = _run_dirs(kernel)
    assert len(admitted) == 2

    # the release slice raised past (admitted deadline - root budget): B2-C2 (4) names the root
    monkeypatch.setattr(clock, "release_slice", 300.0 - 30.0 + 1.0)
    outcome = _refused_beside(kernel, "walk", admitted)
    assert outcome.code == codes.BUDGET_DOES_NOT_FIT == "admission.budget_does_not_fit"
    assert "<root>" in outcome.message and "release slice" in outcome.message
    # a root with no release walk has no release slice: a plain root is admitted as today
    assert isinstance(_admit_result(kernel, "plain"), AdmitResultAdmitted)


def _refused_beside(kernel: Kernel, plugin: str, existing: list[Path]) -> RequestOutcome:
    outcome = kernel.control.run(plugin=plugin, args={}, idempotency_key=KEY)
    assert isinstance(outcome, RequestOutcome), outcome
    assert outcome.origin == "admission" and "run_id" not in outcome.to_dict()
    assert _run_dirs(kernel) == existing  # nothing new: no run dir, no ledger
    assert idempotency.lookup(kernel.home, KEY) is None
    return outcome


def test_b2c2_5_margin_misfit_refused_no_run_id(tmp_path: Path) -> None:
    kernel = support.make_kernel(
        tmp_path,
        {
            # sweep = ceil(1 / 4) * 5 * 5 = 25; grace + kill + 25 = 40 > 35 (the published margin)
            "slow_release": support.workflow_source("slow_release", release_timeouts_s=(5,)),
            # ceil(5 / 4) * 5 * 2 = 20; 15 + 20 = 35 = the margin: fits exactly
            "five_fit": support.workflow_source("five_fit", release_timeouts_s=(2,) * 5),
            # ceil(9 / 4) * 5 * 2 = 30; 15 + 30 = 45 > 35
            "nine_over": support.workflow_source("nine_over", release_timeouts_s=(2,) * 9),
        },
    )
    assert clock.finalization_margin == clock.stop_bound + clock.FINALIZATION_RESERVE_S
    assert (clock.grace, clock.kill, clock.sweep_parallelism) == (10.0, 5.0, 4)
    assert isinstance(_admit_result(kernel, "five_fit"), AdmitResultAdmitted)
    admitted = _run_dirs(kernel)
    for plugin in ("slow_release", "nine_over"):
        outcome = _refused_beside(kernel, plugin, admitted)
        assert outcome.code == codes.BUDGET_DOES_NOT_FIT
        assert "<root>" in outcome.message and "finalization" in outcome.message


def test_b2c2_5_root_without_release_targets_is_never_a_margin_misfit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = support.make_kernel(
        tmp_path,
        {"plain": support.workflow_source("plain"), "echo": support.ECHO.read_text("utf-8")},
    )
    monkeypatch.setattr(clock, "finalization_margin", 0.0)
    assert isinstance(_admit_result(kernel, "plain"), AdmitResultAdmitted)
    result = kernel.control.admission.admit(AdmitRequest(plugin="echo", args={"message": "hi"}))
    assert isinstance(result, AdmitResultAdmitted)


def test_b2c2_5_margin_limit_is_read_from_the_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = support.make_kernel(
        tmp_path, {"walk": support.workflow_source("walk", release_timeouts_s=(2,))}
    )
    assert isinstance(_admit_result(kernel, "walk"), AdmitResultAdmitted)  # 15 + 10 = 25 <= 35
    admitted = _run_dirs(kernel)
    monkeypatch.setattr(clock, "finalization_margin", 24.0)
    assert _refused_beside(kernel, "walk", admitted).code == codes.BUDGET_DOES_NOT_FIT
    monkeypatch.setattr(clock, "grace", 1.0)  # 1 + 5 + 10 = 16 <= 24
    monkeypatch.setattr(clock, "kill", 5.0)
    assert isinstance(_admit_result(kernel, "walk"), AdmitResultAdmitted)


@pytest.mark.proves(
    "WR-PLAN-2", "WR-PLAN-2:schema-refusal-names-identifier", "A", "single", "MCP+LOGIC", "CI"
)
def test_schema_refusal_names_identifier_and_locator(tmp_path: Path) -> None:
    kernel = support.make_kernel(tmp_path, {"id_root": _IDENTIFIER_ROOT})
    args: dict[str, object] = {"services": ["db", "zz"]}
    outcome = _refused(kernel, "id_root", args)
    assert outcome.code == codes.UNKNOWN_IDENTIFIER == "admission.unknown_identifier"
    assert "zz" in outcome.message  # the identifier
    assert "identifier_sets.svcs" in outcome.message  # where the valid ones are listed
    wire = outcome.to_dict()
    assert wire["code"] == "admission.unknown_identifier" and "run_id" not in wire

    # a valid identifier list is admitted (one vertex): the refusal is the identifier's alone
    ok = kernel.control.admission.admit(AdmitRequest(plugin="id_root", args={"services": ["db"]}))
    assert isinstance(ok, AdmitResultAdmitted), ok


def test_plan_refusal_codes_are_reexported_once() -> None:
    from trestle.common.plan import vocabulary as vocab

    for name in (
        "BOUND_EXCEEDED",
        "UNKNOWN_IDENTIFIER",
        "UNIT_UNRESOLVED",
        "DEPENDENCY_CYCLE",
        "DECLARATION_CONFLICT",
        "LEASE_SET_UNDECIDABLE",
        "ROUTE_UNSUPPORTED",
    ):
        assert getattr(codes, name) == getattr(vocab, name)
    # V-11 has one spelling for the budget misfit: core's constant, not a second one
    assert codes.BUDGET_DOES_NOT_FIT == vocab.BUDGET_DOES_NOT_FIT
    assert not hasattr(codes, "ADMISSION_BUDGET_DOES_NOT_FIT")


def test_run_view_is_returned_for_an_admitted_root(tmp_path: Path) -> None:
    kernel = support.make_kernel(tmp_path, {"wf": support.workflow_source("wf")})
    view = kernel.control.run(plugin="wf", args={}, wait_ms=5000)
    assert isinstance(view, RunView)


def _probe(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.proof.probes.single", "multi-vertex-refusal", *args],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_probe_reports_absent_by_exit_status(tmp_path: Path) -> None:
    """TM-B2-1's probe at L.TR-5.3: exit 1 (absent) for the entry and for both phases, since
    neither the `AllDeclaration` root nor the `ChoiceNode` root is refused; a usage error is 2;
    nothing is written."""
    home_before = sorted(p.name for p in REPO.glob("*"))
    assert _probe("--phase", "choice-only").returncode == 1
    assert _probe().returncode == 1
    assert _probe("--phase", "full").returncode == 1
    assert _probe("--phase", "nonsense").returncode == 2
    assert sorted(p.name for p in REPO.glob("*")) == home_before
