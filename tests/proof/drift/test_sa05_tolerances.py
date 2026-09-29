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
