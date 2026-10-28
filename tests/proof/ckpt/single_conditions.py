"""J-SINGLE's live condition nodes (L.SV-5.14; CM-5 "Condition modules", DM-80, A1c3-2).

This file matches no default pytest pattern (`test_*.py`, `*_test.py`), so nothing here is collected
by a bare `pytest`: the nodes run only under `python -m tests.proof.meta ckpt single` and where a
command names the path (`pytest tests/proof/ckpt/single_conditions.py::test_condition_a`; this leaf,
L.SL-11.1, MJ.SL-11 and J-SINGLE (a), (c), (f)). They never run on a later head: TR-L lifts the
refusal (a) asserts, and L.TR-1.1 removes the admission audit hook by design (DM-11). The default-
collected planted self-tests are `tests/single/spine/test_ckpt_single.py`.

* `test_condition_a`: (a) through the MC-12 host.
* `test_condition_c`: (c) over the rendered ledger and the host-proc record at the evaluated commit.
* `test_condition_f`: (f) the second domain-free workflow and the SL-11 merge diff.
* `test_vertex_audit_*`: the audit plugin's two pytester negatives, which need TM-B2-8's hook, and
  the pure verdict's own cases.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.proof import ancestry, mcp_host, tolerances
from tests.proof.ckpt import single, single_vertex_audit
from tests.proof.suites.workflows import SLICE_A_WORKFLOWS
from trestle.common import clock, codes
from trestle.server import idempotency

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "workflows"
COMPOSITE_FIXTURES = ("probe_all_root", "probe_choice_root")
KEY = "j-single-a-key"
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S


def _commit() -> str:
    """The evaluated commit (the CM-6 anchor): handed by `meta ckpt`, else HEAD."""
    handed = os.environ.get(single.COMMIT_ENV)
    if handed:
        return handed
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _descendants() -> set[int]:
    """The pids of every live descendant of this process (by parent pid), but for the `ps` the
    snapshot itself runs (MC-13)."""
    procs = [p for p in ancestry.snapshot() if os.path.basename(p.argv.split(" ", 1)[0]) != "ps"]
    found: set[int] = set()
    frontier = {os.getpid()}
    while frontier:
        frontier = {p.pid for p in procs if p.ppid in frontier} - found
        found |= frontier
    return found


def _home_run_files(home: Path) -> list[str]:
    """Every run directory or ledger file under `home` (a refusal leaves none)."""
    found = sorted(str(p.relative_to(home)) for p in (home / "runs").glob("*/*"))
    found += sorted(str(p.relative_to(home)) for p in home.rglob("*ledger*"))
    return found


# ---------------------------------------------------------------------------
# (a)
# ---------------------------------------------------------------------------


def test_condition_a(tmp_path: Path) -> None:
    """(a) An `AllDeclaration` root and a `ChoiceNode` root are each refused
    `admission.plan_multi_vertex_unsupported` through the MC-12 host, with no run dir, no ledger,
    no spawned process (the MC-13 diff is empty) and no idempotency record (B2-I1)."""
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        for name in COMPOSITE_FIXTURES:
            shutil.copy(FIXTURES / f"{name}.py", host.home / "plugins")
        before = _descendants()
        assert _home_run_files(host.home) == []
        for name in COMPOSITE_FIXTURES:
            refused = host.call(
                "run",
                {
                    "plugin": name,
                    "wait_ms": tolerances.HARNESS_WAIT_MS,
                    "completion": "terminal",
                    "idempotency_key": f"{KEY}-{name}",
                },
            )
            assert isinstance(refused, dict), refused
            assert refused["code"] == codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED, refused
            assert "run_id" not in refused, refused
            assert _home_run_files(host.home) == [], f"{name}: a refusal left a run directory"
            assert idempotency.lookup(host.home, f"{KEY}-{name}") is None
        assert _descendants() == before, "a refused root spawned a process (MC-13)"


# ---------------------------------------------------------------------------
# (c)
# ---------------------------------------------------------------------------


def test_condition_c() -> None:
    """(c) All 18 `step: single` clause parts PROVEN through a named gate, every venue-BOTH one also
    PROVEN@HOST through the host-proc record `record.select` returns at the evaluated commit, with
    `record.pass_set_violations` empty; every `step: tree` clause unproven and unclaimed; CM-8's
    band rule: every deferral whose `closes_at` is an A-1 merge has its label PROVEN."""
    from tests.proof import deferrals as deferrals_mod
    from tests.proof import ledger as ledger_mod
    from tests.proof import meta as meta_mod
    from tests.proof import results as results_mod
    from tests.proof import transcribe as transcribe_mod
    from tests.proof.host import record as record_mod

    commit = _commit()
    clauses = list(transcribe_mod.load_matrix_map())
    labels = list(meta_mod._load_all_labels())  # noqa: SLF001
    parts = single.single_parts(clauses)
    assert len(parts) == 18, f"the matrix map has {len(parts)} single clause parts, expected 18"
    both = single.BOTH_PARTS + sorted(
        str(lb["id"])
        for lb in labels
        if lb.get("step") == "single" and lb.get("venue") == "BOTH" and lb.get("posture") == "claim"
    )
    try:
        report = ledger_mod.render()
    except ledger_mod.VacuousLedgerError as exc:
        pytest.fail(f"no ledger to read: {exc}")
    # the host-proc record is resolved here, only through `record.select` at the evaluated commit
    host_record = record_mod.select("host-proc", commit, cwd=ROOT)
    problems = single.part_problems(
        report,
        results_mod.read_records(),
        parts,
        both=both,
        host_record=host_record,
    )
    if host_record is not None:
        by_id = {str(lb["id"]): lb for lb in labels}
        problems += record_mod.pass_set_violations(host_record, by_id)
    problems += single.unclaimed_problems(report, single.tree_parts(clauses))
    proven = {key for key in report if report[key].get("status") == single.PROVEN}
    problems += single.closing_violations(
        deferrals_mod.load_deferrals(), single.a1_merges(), proven
    )
    assert problems == [], "; ".join(problems[:8])


# ---------------------------------------------------------------------------
# (f)
# ---------------------------------------------------------------------------


def test_condition_f() -> None:
    """(f) Every guarantee suite is green for `second_domain_free` (registered in MC-35 by
    L.SL-11.1) and the SL-11 merge diff has no `trestle/**` path."""
    from tests.proof import trailers as trailers_mod

    assert single.SECOND_WORKFLOW in SLICE_A_WORKFLOWS, (
        f"{single.SECOND_WORKFLOW} is not in SLICE_A_WORKFLOWS (registered by L.SL-11.1)"
    )
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/proof/suites",
        "tests/proof/suites/test_conformance_matrix.py::test_second_workflow_passes_every_guarantee_suite",
    ]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-600:]
    marker = trailers_mod.landing(single.SL11_MERGE, ref=_commit(), cwd=ROOT)
    assert marker is not None, f"WR-Merge: {single.SL11_MERGE} is not in the history of the commit"
    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{marker}^", marker],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert [p for p in diff if p.startswith("trestle/")] == [], "the SL-11 merge changed trestle/**"


# ---------------------------------------------------------------------------
# the audit plugin: its pure verdict and its two pytester negatives (TM-B2-8's hook)
# ---------------------------------------------------------------------------


def _line(
    plugin: str, nodeid: str | None, *, vertex_count: int = 1, pid: int = 1
) -> dict[str, Any]:
    return {
        "run_dir": f"/r/{plugin}",
        "vertex_count": vertex_count,
        "plugin": plugin,
        "pid": pid,
        "nodeid": nodeid,
    }


def _complete() -> list[dict[str, Any]]:
    """A recording that satisfies every guard for the shipped registry (own pid = 1)."""
    lines = [
        _line("spine_leaf", "tests/proof/spine/test_plan_walk.py::test_a (call)"),
        _line("spine_leaf", "tests/single/spine/test_w_a1.py::test_b (call)", pid=2),
    ]
    lines += [
        _line(name, "tests/proof/suites/test_conformance_matrix.py::test_c (call)")
        for name in SLICE_A_WORKFLOWS
    ]
    return lines


def test_audit_verdict_passes_a_complete_recording_and_names_each_defect() -> None:
    entries = sorted(SLICE_A_WORKFLOWS)
    complete = _complete()
    assert single_vertex_audit.verdict(complete, own_pid=1, entries=entries) == []
    # a second vertex is a defect on its own
    bad = [*complete, _line("spine_leaf", "tests/single/x.py::t (call)", vertex_count=2)]
    (problem,) = single_vertex_audit.verdict(bad, own_pid=1, entries=entries)
    assert "vertex_count 2 != 1" in problem
    # each vacuity guard is keyed on what it names: dropping the one line that satisfies it fails
    # that guard and no other
    others = {
        "other process": [
            line for line in complete if line["pid"] != 2 or line["plugin"] != "spine_leaf"
        ],
        "spine prefix": [line for line in complete if "proof/spine/" not in str(line["nodeid"])],
        "suites prefix": [line for line in complete if "proof/suites/" not in str(line["nodeid"])],
    }
    for guard, lines in others.items():
        problems = single_vertex_audit.verdict(lines, own_pid=1, entries=entries)
        assert len(problems) == 1 and problems[0].startswith("vacuous"), (guard, problems)
    # an admission made elsewhere never satisfies a suites guard, whatever its plugin
    elsewhere = [
        line
        if "proof/suites/" not in str(line["nodeid"])
        else {**line, "nodeid": "tests/single/z.py::t"}
        for line in complete
    ]
    (problem,) = single_vertex_audit.verdict(elsewhere, own_pid=1, entries=entries)
    assert "tests/proof/suites/" in problem
    assert single_vertex_audit.verdict([], own_pid=1, entries=entries)[0].startswith("vacuous")
    torn = single_vertex_audit.verdict(
        complete, own_pid=1, entries=entries, bad_lines=["{not json"]
    )
    assert len(torn) == 1 and "unreadable" in torn[0]


_PLANTED_ONE_VERTEX = """
from pathlib import Path

from tests.proof import harness


def test_planted_admission():
    harness.admit_tree(Path({fixture!r}), {args!r})
"""

# A-1 admits one-vertex roots only and a snapshot's declared tree cannot yet name a resolved
# composite (A-2's extraction), so the second vertex is planted the way the foundations enumerator
# builds trees: a compiled, carved two-vertex plan handed to `write_admitted_run` itself.
_PLANTED_TWO_VERTEX = """
import shutil
import tempfile
from pathlib import Path

from tests.proof import harness
from tests.proof.foundations import trees
from trestle.common import clock
from trestle.common.plan import carving, compiler
from trestle.common.types import AdmitRequest


def test_planted_admission():
    plugins = Path(tempfile.mkdtemp())
    shutil.copy({fixture!r}, plugins)
    kernel = harness.fresh_kernel([plugins])
    kernel.registry.maybe_refresh()
    snap = kernel.registry.get("spine_leaf")
    compiled = compiler.compile(trees.build(("all", (trees.LEAF,))), {{}})
    assert len(compiled.vertices) == 2
    release_slice = carving.release_slice_for(compiled, clock.release_slice)
    slices = carving.carve(compiled, 300.0, clock.FINALIZATION_RESERVE_S, release_slice)
    plan = carving.attach(compiled, slices, release_slice)
    request = AdmitRequest(plugin="spine_leaf", args={{"env": "dev"}})
    kernel.control.admission.write_run(snap, request, plan)
"""


def _planted_session(pytester: pytest.Pytester, source: str) -> Any:
    """A session under the audit plugin whose one test admits a run through `write_admitted_run`
    (the recorded path) from a node under `tests/proof/spine/`."""
    pytester.makefile(".py", **{"tests/proof/spine/test_planted": source})
    return pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "tests.proof.ckpt.single_vertex_audit"
    )


def test_vertex_audit_catches_planted_two_vertex_admission(pytester: pytest.Pytester) -> None:
    """A two-vertex plan admitted through `write_admitted_run` in a pytester session fails the
    audit, by its vertex count (planted through `write_admitted_run` itself, the recorded path)."""
    result = _planted_session(
        pytester, _PLANTED_TWO_VERTEX.format(fixture=str(FIXTURES / "spine_leaf.py"))
    )
    assert result.ret != 0
    output = result.stdout.str()
    assert "spine_leaf: vertex_count 2 != 1" in output, output
    assert "single vertex audit: FAILED" in output


def test_vertex_audit_vacuity_guard_fails_without_expected_fixture(
    pytester: pytest.Pytester,
) -> None:
    """A session that admits `spine_leaf` from its spine suite only never reaches the MCP-host
    path and never admits an MC-35 entry from `tests/proof/suites/`: it fails its vacuity guards,
    keyed on the recorded nodeid, though every recorded admission has one vertex."""
    result = _planted_session(
        pytester,
        _PLANTED_ONE_VERTEX.format(
            fixture=str(FIXTURES / "spine_leaf.py"), args={"env": "dev", "mode": "advance"}
        ),
    )
    assert result.ret != 0
    output = result.stdout.str()
    assert "vertex_count" not in output, output
    assert "no spine_leaf admission from a process other than pytest's" in output, output
    assert "no spine_leaf admission recorded under tests/proof/suites/" in output, output
    # the spine-prefix guard was met by the admission the planted test made from that path
    assert "recorded under tests/proof/spine/" not in output, output
