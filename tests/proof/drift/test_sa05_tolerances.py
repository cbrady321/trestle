"""SA-05 drift proof: every timing bound has one product definition, is read
by the proof court only through `tests.proof.tolerances` under its MC-09 name,
and no proof test hard-codes a timing literal (L.P0-0a.4)."""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

from tests.proof import tolerances

ROOT = Path(__file__).resolve().parents[3]

# MC-09 published name -> tolerances accessor.
ACCESSORS = {
    "grace": "grace",
    "kill": "kill",
    "release_slice": "release_slice",
    "finalization_margin": "finalization_margin",
    "deadline_ceiling": "deadline_ceiling",
    "sweep_parallelism": "sweep_parallelism",
    "stop_bound": "stop_bound",
    "APPEND_COST_RATIO": "append_cost_ratio",
    "FINALIZATION_RESERVE_S": "finalization_reserve_s",
}
TIMING_KEYWORDS = {"timeout", "timeout_s", "wait_ms", "grace_s", "kill_s", "deadline_s"}
CLOCK_FILE = "trestle/common/clock.py"


def _module_level_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return names


def _definitions(name: str) -> list[str]:
    literal_files = {file for file, _ in tolerances.find_s0_literal_sites()}
    found: list[str] = []
    for path in sorted((ROOT / "trestle").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if rel in literal_files:
            continue
        found += [rel for n in _module_level_names(path) if n == name]
    return found


@pytest.mark.proves("WR-PROOF-9", "WR-PROOF-9:tolerances-declared", "core", "core", "LOGIC", "CI")
@pytest.mark.parametrize("sa", ["SA-05"])
def test_single_definition(sa: str) -> None:
    for name, (s0_module, s0_attr) in tolerances._S0_FALLBACKS.items():
        clock_defs = _definitions(name)
        if s0_attr is None:
            # Not yet published: at most one definition, and only on the clock module.
            assert clock_defs in ([], [CLOCK_FILE]), (name, clock_defs)
            continue
        assert s0_module is not None
        s0_defs = _definitions(s0_attr)
        assert s0_defs == [s0_module.replace(".", "/") + ".py"], (s0_attr, s0_defs)
        assert clock_defs in ([], [CLOCK_FILE]), (name, clock_defs)
        # The imported value is the product value, not a copy.
        mod = __import__(s0_module, fromlist=[s0_attr])
        assert float(getattr(tolerances, ACCESSORS[name])()) == float(getattr(mod, s0_attr))


@pytest.mark.proves("WR-PROOF-9", "WR-PROOF-9:tolerances-declared", "core", "core", "LOGIC", "CI")
@pytest.mark.parametrize("sa", ["SA-05"])
def test_no_timing_literal_in_proof_tests(sa: str) -> None:
    offenders: list[str] = []
    for base in ("tests/pins", "tests/proof"):
        for path in sorted((ROOT / base).rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                candidates = [kw.value for kw in node.keywords if kw.arg in TIMING_KEYWORDS]
                func = node.func
                fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if fname == "sleep":
                    candidates += node.args[:1]
                for value in candidates:
                    if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
                        if not isinstance(value.value, bool):
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders


@pytest.mark.proves("WR-PROOF-9", "WR-PROOF-9:tolerances-declared", "core", "core", "LOGIC", "CI")
@pytest.mark.parametrize("sa", ["SA-05"])
def test_pending_raises_on_read(sa: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tolerances, "_clock_module", lambda: None)
    for name, (s0_module, _) in tolerances._S0_FALLBACKS.items():
        accessor = getattr(tolerances, ACCESSORS[name])
        if s0_module is None:
            with pytest.raises(tolerances.ToleranceUnpublished):
                accessor()
        else:
            assert isinstance(accessor(), float)


@pytest.mark.parametrize("sa", ["SA-05"])
def test_published_in_clock_module_is_picked_up_without_editing_tolerances(
    sa: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_before = Path(tolerances.__file__).read_text()
    monkeypatch.setattr(tolerances, "_clock_module", lambda: None)
    for accessor in ("deadline_ceiling", "stop_bound"):
        with pytest.raises(tolerances.ToleranceUnpublished):
            getattr(tolerances, accessor)()
    monkeypatch.undo()

    clock = types.ModuleType("trestle.common.clock")
    clock.deadline_ceiling = 42.5  # type: ignore[attr-defined]
    clock.stop_bound = 7  # type: ignore[attr-defined]
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "trestle.common.clock", clock)
        assert tolerances.deadline_ceiling() == 42.5
        assert tolerances.stop_bound() == 7.0
    assert Path(tolerances.__file__).read_text() == source_before


@pytest.mark.proves("WR-PROOF-9", "WR-PROOF-9:tolerances-declared", "core", "core", "LOGIC", "CI")
@pytest.mark.parametrize("sa", ["SA-05"])
def test_bound_resolves_under_its_mc09_name(sa: str, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = types.ModuleType("trestle.common.clock")
    expected: dict[str, float] = {}
    for i, name in enumerate(ACCESSORS, start=1):
        setattr(clock, name, 100.0 + i)
        expected[name] = 100.0 + i
    monkeypatch.setitem(sys.modules, "trestle.common.clock", clock)
    for name, accessor in ACCESSORS.items():
        assert getattr(tolerances, accessor)() == expected[name], name


# ---- sleep as synchronisation ------------------------------------------------------------------
# A `sleep` directly followed by an `assert` stands in for a condition the assert then reads. A
# positive assertion after a pause is a race on a loaded runner (CI run 36955650845 CK-8:
# test_cs2_containment `assert 143 == 0`); wait on the condition instead (`wait_until`, a NodeEnd,
# a pid reaped). An absence-over-window check ("for a while nothing happened") may pause: it can
# only pass more easily under load, never fail falsely, and is marked `# absence-window` on the
# sleep's line. The sites below predate the rule; the set may shrink, never grow.
SLEEP_SYNC_SCOPE = ("tests", "packages/trestle-packs/tests", "packages/trestle-env/tests")
SLEEP_SYNC_BASELINE = {
    # positive after a pause: race-shaped, to be fixed (then removed from this set)
    ("tests/core/spine/test_cs2_clock.py", "test_deadline_vs_cancel_first_cause"),
    ("tests/proof/selftest/test_ancestry.py", "test_survivors_cli_exit_status"),
    (
        "tests/proof/selftest/test_ancestry.py",
        "test_survivors_cli_accepts_repeated_markers_and_ignores_itself",
    ),
    # absence over a window: to be marked `# absence-window` by their lanes (then removed here)
    (
        "tests/single/control/mcp/test_sever_rejoin.py",
        "test_sever_run_continues_bounded_and_cleaned",
    ),
    ("tests/single/spine/test_w_a1.py", "test_wait_ends_on_stop_read_from_the_record"),
    ("tests/tree/test_tr4_lease.py", "test_direct_call_acquires_before_first_effect"),
}


def _sleep_sync_sites(root: Path) -> dict[tuple[str, str], int]:
    """Every `(file, function)` holding a `sleep(...)` statement directly followed by an `assert`
    in the same block (not marked `# absence-window`), with the sleep's line."""
    sites: dict[tuple[str, str], int] = {}
    for base in SLEEP_SYNC_SCOPE:
        for path in sorted((root / base).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            for func in ast.walk(ast.parse(source)):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    for field in ("body", "orelse", "finalbody"):
                        block = getattr(node, field, None)
                        if not isinstance(block, list):
                            continue
                        for first, second in zip(block, block[1:], strict=False):
                            if not (
                                isinstance(first, ast.Expr)
                                and isinstance(first.value, ast.Call)
                                and isinstance(second, ast.Assert)
                            ):
                                continue
                            call = first.value.func
                            name = call.attr if isinstance(call, ast.Attribute) else ""
                            name = name or getattr(call, "id", "")
                            if name != "sleep":
                                continue
                            if "# absence-window" in lines[first.lineno - 1]:
                                continue
                            sites.setdefault((str(path.relative_to(root)), func.name), first.lineno)
    return sites


@pytest.mark.parametrize("sa", ["SA-05"])
def test_no_sleep_as_synchronisation(sa: str) -> None:
    """The ratchet, both ways: no new site, and no baseline entry that is no longer a site (a
    fixed site leaves the baseline in the same change, so the set only shrinks)."""
    found = _sleep_sync_sites(ROOT)
    new = sorted(
        f"{path}:{line} ({func})"
        for (path, func), line in found.items()
        if (path, func) not in SLEEP_SYNC_BASELINE
    )
    assert not new, f"{sa}: sleep then assert; wait on the condition instead: {new}"
    stale = sorted(SLEEP_SYNC_BASELINE - found.keys())
    assert not stale, f"{sa}: fixed sites still in SLEEP_SYNC_BASELINE (remove them): {stale}"


def test_the_sleep_sync_scan_finds_a_planted_site(tmp_path: Path) -> None:
    """Not vacuous: a planted sleep-then-assert is found, a marked absence window is not."""
    planted = tmp_path / "tests" / "test_planted.py"
    planted.parent.mkdir()
    planted.write_text(
        "import time\n\n\ndef test_raced():\n    time.sleep(0.1)\n    assert True\n\n\n"
        "def test_window():\n    time.sleep(0.1)  # absence-window\n    assert True\n",
        encoding="utf-8",
    )
    for base in SLEEP_SYNC_SCOPE[1:]:
        (tmp_path / base).mkdir(parents=True)
    assert _sleep_sync_sites(tmp_path) == {("tests/test_planted.py", "test_raced"): 5}


# ---- lane polling outside the test kit ---------------------------------------------------------
# A test that waits on a run's record waits through `tests.proof.records.await_record` (or its
# shapes `await_node_end` / `await_confirmations`): it returns on the condition, on the root's
# terminal row, or at its bound, and says which. A local loop over the lane or ledger is a second,
# bespoke copy of that wait (RACES-REPORT L-1..L-5). The set may shrink, never grow.
LANE_POLL_READERS = {"lane_rows", "ledger_rows", "node_record", "lane"}
LANE_POLL_HOME = "tests/proof/records.py"
LANE_POLL_BASELINE: set[tuple[str, str]] = {
    # the race leads' local waits (RACES-REPORT L-1..L-5, L-15a): moved onto the kit next
    ("tests/tree/test_tr3_slices.py", "_wait_end"),
    ("tests/tree/host/test_trl_rollup.py", "wait_end"),
    ("tests/tree/test_tr4_depth.py", "stopped_answer"),
    ("tests/tree/test_tr3_failfast.py", "_readiness_run"),
    (
        "tests/proof/selftest/test_mcp_host.py",
        "test_sever_cancel_notification_run_reaches_terminal",
    ),
    (
        "tests/proof/selftest/test_mcp_host.py",
        "test_sever_close_server_exits_run_recovered_interrupted",
    ),
    # predate the rule: each moves onto `await_record` when its file is next changed
    ("packages/trestle-env/tests/twin/cancel_case.py", "mid_wait"),
    ("packages/trestle-env/tests/twin/test_reuse_twin.py", "stop_once_found_reused"),
    (
        "tests/core/admission/test_cl_a1_capacity.py",
        "test_over_capacity_queued_then_dispatched_or_refused",
    ),
    ("tests/core/admission/test_cl_a1_capacity.py", "test_queued_run_gets_no_extra_time"),
    ("tests/core/spine/test_cs4_call.py", "test_cancel_and_query_answer_while_terminal_call_held"),
    ("tests/core/spine/test_cs4_call.py", "test_terminal_response_follows_terminal_row"),
    (
        "tests/core/spine/test_cs4_call.py",
        "test_wait_past_the_bound_is_the_named_code_never_running",
    ),
    (
        "tests/single/workflow/proc/test_runtime_containment.py",
        "test_runtime_launched_tree_ignoring_sigterm_gone_after_cancel",
    ),
    ("tests/tree/host/test_tr5_containment.py", "start_and_settle"),
    ("tests/tree/host/test_trl_cancel.py", "start_and_settle"),
    ("tests/tree/host/test_trl_lease.py", "test_direct_call_acquires_before_effect"),
    ("tests/tree/test_tr4_lease.py", "test_child_record_has_no_lease_entry"),
}


def _lane_poll_sites(root: Path) -> dict[tuple[str, str], int]:
    """Every `(file, function)` outside `records.py` whose body holds a `while` loop or a
    `wait_until(` call and also calls a record reader (`lane_rows`, `ledger_rows`, `node_record`,
    or a rig's `lane`), with the function's line. Nested helpers count toward their encloser."""
    sites: dict[tuple[str, str], int] = {}
    for base in SLEEP_SYNC_SCOPE:
        for path in sorted((root / base).rglob("*.py")):
            rel = str(path.relative_to(root))
            if rel == LANE_POLL_HOME:
                continue
            for func in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                loops = reads = False
                for node in ast.walk(func):
                    if isinstance(node, ast.While):
                        loops = True
                    elif isinstance(node, ast.Call):
                        call = node.func
                        name = call.attr if isinstance(call, ast.Attribute) else ""
                        name = name or getattr(call, "id", "")
                        loops = loops or name == "wait_until"
                        reads = reads or name in LANE_POLL_READERS
                if loops and reads:
                    sites.setdefault((rel, func.name), func.lineno)
    return sites


@pytest.mark.parametrize("sa", ["SA-05"])
def test_no_lane_polling_outside_records(sa: str) -> None:
    """The ratchet, both ways, as `test_no_sleep_as_synchronisation`."""
    found = _lane_poll_sites(ROOT)
    new = sorted(
        f"{path}:{line} ({func})"
        for (path, func), line in found.items()
        if (path, func) not in LANE_POLL_BASELINE
    )
    assert not new, f"{sa}: a local poll of the record; use records.await_record: {new}"
    stale = sorted(LANE_POLL_BASELINE - found.keys())
    assert not stale, f"{sa}: fixed sites still in LANE_POLL_BASELINE (remove them): {stale}"


def test_the_lane_poll_scan_finds_a_planted_site(tmp_path: Path) -> None:
    """Not vacuous: a planted local poll of the lane is found, a read with no loop is not, and
    the test kit's own home is exempt."""
    planted = tmp_path / "tests" / "test_planted.py"
    planted.parent.mkdir()
    planted.write_text(
        "from tests.proof import records\n\n\ndef test_polls(run_dir):\n"
        "    while not records.lane_rows(run_dir).rows:\n        pass\n\n\n"
        "def test_reads(run_dir):\n    assert records.node_record(run_dir).terminal\n",
        encoding="utf-8",
    )
    home = tmp_path / LANE_POLL_HOME
    home.parent.mkdir(parents=True)
    home.write_text("def await_record(d):\n    while True:\n        lane_rows(d)\n")
    for base in SLEEP_SYNC_SCOPE[1:]:
        (tmp_path / base).mkdir(parents=True)
    assert _lane_poll_sites(tmp_path) == {("tests/test_planted.py", "test_polls"): 4}
