"""L.TR-6.3: kind-freedom over the fake three-level and choice trees; termination-coverage closure.

Two claims, both of C-RUNTIME-NEUTRAL, both statements about files rather than runs:

* **Kind-freedom (static).** The loop's library modules (the scope of `tests/proof/spine/
  test_kind_freedom.py`: `decide.py`, `join.py`, `loop.py`, `facets.py`) branch on no resource,
  realization, port or lifetime value, and `decide` reads only the three V-6 flags, the condition
  and the goal (B1-C10). L.SV-5.17 proves it for one vertex; here it is proven for the two fake
  trees that exercise the widest walk: `three_level` (AllDeclaration at depth 3) and `choice_fake`
  (the SELECT path: `decide` answers `SELECT` for a CHOICE and the loop's `_select` is in the
  scanned source). For each tree the values *it declares* (resource kinds, realizations,
  vantages, lifetimes) are collected from the declaration, and the scanner must both find no
  branch on any of them in the scanned modules and be shown to catch a planted branch on each.
* **Closure.** The two termination suites (L.TR-3.7 for non-ChoiceNode plans, L.TR-5.5 for
  ChoiceNode plans) enumerate every plan the fixture set holds. The set is found here by an
  independent scan of `tests/fixtures/trees/` (an AST read of each module's `LABEL` and of its
  `ChoiceNode(...)` calls, never `tests/tree/plans.py`) plus `generators.GENERATED`; every valid
  plan but a `SCALE_ONLY` key is in exactly one suite's enumeration and each `SCALE_ONLY` key's
  reduction target is. Both suites read `tests/tree/plans.py` at run time, so a fixture authored
  after them (live_state, choice_long_running, survivor_writer) is covered with no edit to either
  (A2c2-3): the closure holds for the set as it stands and for a planted fixture (test below)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.fixtures.trees import generators
from tests.proof.spine import test_kind_freedom as kind_freedom
from tests.tree import plans
from tests.tree import test_tr3_termination as tr3
from tests.tree import test_tr5_termination as tr5
from trestle.workflow import decide as decide_module
from trestle.workflow import declarations
from trestle.workflow.declarations import AllDeclaration, ChoiceNode, LeafDeclaration

pytestmark = pytest.mark.spine

proves = pytest.mark.proves(
    "C-RUNTIME-NEUTRAL", "C-RUNTIME-NEUTRAL:closure", "A", "tree", "LOGIC", "CI"
)

TREES = Path(generators.__file__).resolve().parent
DECIDE = kind_freedom.PACKAGE / "decide.py"
FLAGS_READ = frozenset({"compose", "completion", "repeat"})


# ---- kind-freedom, static


def declared_kind_values(entry: declarations.WorkflowEntry) -> set[str]:
    """Every resource, realization, vantage and lifetime value the tree's declarations name."""
    values: set[str] = set()
    for unit in entry.units.values():
        declared = unit if isinstance(unit, AllDeclaration | ChoiceNode) else unit.declare()  # type: ignore[attr-defined]
        if isinstance(declared, LeafDeclaration):
            values.add(str(declared.resource_kind))
            values.update(str(kind) for kind in declared.may_touch)
            values.update(str(effect.lifetime.value) for effect in declared.effects)
        elif isinstance(declared, AllDeclaration):
            values.update(str(child.vantage.value) for child in declared.children)
        else:
            for alternative in declared.choice.alternatives:
                values.add(str(alternative.realization.value))
                values.update(str(v.value) for v in alternative.reachable_from)
    return values


def branches_on_values(source: str, name: str, values: set[str]) -> list[str]:
    """Every comparison, membership test or `match` in `source` that names one of `values` as a
    string literal (the declared values of a tree, whatever the carrier is called)."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        tested: list[ast.AST] = []
        if isinstance(node, ast.Compare):
            tested.append(node)
        elif isinstance(node, ast.Match):
            tested.append(node.subject)
            tested.extend(case.pattern for case in node.cases)
        for part in tested:
            for sub in ast.walk(part):
                if isinstance(sub, ast.Constant) and sub.value in values:
                    problems.append(
                        f"{name}:{getattr(part, 'lineno', '?')}: branch on declared value "
                        f"{sub.value!r}"
                    )
    return problems


def decide_reads(source: str) -> tuple[list[str], set[str]]:
    """`decide`'s parameter names and the attributes it reads off `flags`."""
    tree = ast.parse(source)
    (func,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "decide"]
    params = [arg.arg for arg in func.args.args]
    read = {
        node.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "flags"
    }
    return params, read


def _entry(name: str) -> declarations.WorkflowEntry:
    (plan,) = [p for kind in plans.KINDS for p in plans.valid_plans(kind) if p.name == name]
    return plan.entry


@proves
@pytest.mark.parametrize("fixture", ["three_level", "choice_fake"])
def test_kind_freedom_static(fixture: str) -> None:
    """The scanned loop modules branch on no kind value the tree declares; `decide` reads only the
    three V-6 flags, the condition and the goal; the SELECT path (choice_fake) is inside the scan;
    and the scan catches a planted branch on each value the tree declares."""
    entry = _entry(fixture)
    values = declared_kind_values(entry)
    assert values, fixture  # the tree declares kinds at all (else the check below is vacuous)
    for module in kind_freedom.SCOPE:
        path = kind_freedom.PACKAGE / module
        assert path.is_file(), f"{module} is missing from the scanned scope"
        source = path.read_text(encoding="utf-8")
        assert kind_freedom.scan_source(source, module) == [], module
        assert branches_on_values(source, module, values) == [], (fixture, module)
    # decide: three flags, the condition, the goal, nothing else
    params, read = decide_reads(DECIDE.read_text(encoding="utf-8"))
    assert params == ["flags", "condition", "goal"], params
    assert read <= FLAGS_READ, read - FLAGS_READ
    assert decide_module.decide.__code__.co_varnames[:3] == ("flags", "condition", "goal")
    composes = {
        d.flags.compose.value
        for d in (
            u if isinstance(u, AllDeclaration | ChoiceNode) else u.declare()
            for u in entry.units.values()
        )  # type: ignore[attr-defined]
    }
    if fixture == "choice_fake":  # the SELECT path is in the scan and decide answers SELECT
        assert "choice" in composes
        loop_source = (kind_freedom.PACKAGE / "loop.py").read_text(encoding="utf-8")
        assert "def _select" in loop_source and "class TreeWalk" in loop_source
        assert decide_module.Command.SELECT in decide_module.decide(
            declarations.LoopFlags(
                declarations.Compose.CHOICE,
                declarations.CompletionSource.OBSERVED,
                declarations.Repeat.SAFE,
            ),
            decide_module.Condition.UNSATISFIED,
            decide_module.Goal.CONVERGE,
        )
    else:
        assert composes == {"all", "leaf"}, composes
    # the scan is not blind to this tree's values: a planted branch on each is caught
    for value in sorted(values):
        planted = (
            f"def _planted(unit):\n    if unit.resource_kind == {value!r}:\n        return 1\n"
        )
        assert branches_on_values(planted, "planted.py", {value}), value
        assert branches_on_values(
            f"def _planted(unit):\n    return unit.x in ({value!r}, 'other')\n",
            "planted.py",
            {value},
        ), value


# ---- closure of the termination enumerations


def _label_of(tree: ast.Module) -> dict[str, object] | None:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if any(isinstance(t, ast.Name) and t.id == "LABEL" for t in targets):
            return ast.literal_eval(node.value)  # type: ignore[attr-defined,no-any-return]
    return None


def _calls_choice(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "ChoiceNode")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "ChoiceNode")
        )
        for node in ast.walk(tree)
    )


def scanned_valid(directory: Path) -> dict[str, str]:
    """`{name: kind}` of every MC-B3-01 export with `expect = "valid"` in `directory`, read from
    the source with `ast` (never by importing `tests/tree/plans.py`): a module-level `LABEL`
    literal, and `choice` iff the module builds a `ChoiceNode`."""
    found: dict[str, str] = {}
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        label = _label_of(tree)
        if label is not None and label.get("expect") == "valid":
            found[path.stem] = "choice" if _calls_choice(tree) else "nonchoice"
    return found


def scanned_generated() -> dict[str, str]:
    found: dict[str, str] = {}
    for tree in generators.GENERATED:
        entry = tree.entry
        has_choice = any(
            isinstance(u if isinstance(u, AllDeclaration | ChoiceNode) else u.declare(), ChoiceNode)  # type: ignore[attr-defined]
            for u in entry.units.values()
        )
        found[tree.name] = "choice" if has_choice else "nonchoice"
    return found


def suite_enumerations() -> tuple[set[str], set[str]]:
    """The names the two termination suites enumerate, read from the suites' own accessors:
    L.TR-3.7's `product_plans()` (non-ChoiceNode, less the SCALE_ONLY key) and L.TR-5.5's
    `choice_plans()` (ChoiceNode)."""
    return {p.name for p in tr3.product_plans()}, {p.name for p in tr5.choice_plans()}


@proves
def test_termination_covers_every_valid_fixture() -> None:
    """The independent scan of the fixture directory (plus `GENERATED`) equals what
    `valid_plans` yields; every plan but a SCALE_ONLY key is in exactly one suite's enumeration and
    each SCALE_ONLY key's reduction target is; both suites report enumerated == generated."""
    scanned = {**scanned_valid(TREES), **scanned_generated()}
    assert len(scanned) == len(scanned_valid(TREES)) + len(scanned_generated())  # no name collides
    assert len(scanned) >= 20  # not vacuous: the fixture directory is scanned, not a stub
    nonchoice = {n for n, k in scanned.items() if k == "nonchoice"}
    choice = {n for n, k in scanned.items() if k == "choice"}
    yielded_nonchoice = {p.name for p in plans.valid_plans("nonchoice")}
    yielded_choice = {p.name for p in plans.valid_plans("choice")}
    assert nonchoice == yielded_nonchoice  # the two ways of finding the set agree, kind by kind
    assert choice == yielded_choice
    assert choice >= {"choice_fake", "slice_a_tree", "live_state"}
    assert scanned.keys() == yielded_nonchoice | yielded_choice  # the union, nothing else

    scale_only = {tree.name: reduced.name for tree, reduced in generators.SCALE_ONLY.items()}
    assert scale_only  # the reduction exists
    in_tr3, in_tr5 = suite_enumerations()
    assert not (in_tr3 & in_tr5)  # no plan is in both
    for name in scanned:
        if name in scale_only:
            assert name not in in_tr3 | in_tr5, name  # reduced, not enumerated
            assert scale_only[name] in in_tr3 | in_tr5, (name, scale_only[name])
        else:
            assert (name in in_tr3) != (name in in_tr5), name  # exactly one suite
            assert name in (in_tr3 if scanned[name] == "nonchoice" else in_tr5), name
    assert in_tr3 | in_tr5 == set(scanned) - set(scale_only)  # nothing enumerated that is not held
    # enumerated == generated in both suites: the product sizes each suite asserts of itself
    assert len(in_tr3) == len(yielded_nonchoice) - len(generators.SCALE_ONLY)
    assert len(in_tr5) == len(yielded_choice)
    for shape in (tr5.shape_of(p) for p in tr5.choice_plans()):
        assert tr5.expected_runs(shape) >= 1, shape.plan.name


def test_closure_reads_the_fixture_set_at_run_time(tmp_path: Path) -> None:
    """A fixture added to the directory is scanned, and the enumerator picks it up with no edit to
    either suite: the scan and `valid_plans` agree on a copy that gains a planted plan, and a
    defective (`expect != valid`) fixture is in neither."""
    copy = tmp_path / "trees"
    copy.mkdir()
    for path in TREES.glob("*.py"):
        (copy / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    base = scanned_valid(copy)
    assert set(base) == {
        p.name for kind in plans.KINDS for p in plans.valid_plans(kind, copy)
    } - set(scanned_generated())
    planted = (TREES / "three_level.py").read_text(encoding="utf-8")
    (copy / "planted_valid.py").write_text(planted, encoding="utf-8")
    (copy / "planted_bad.py").write_text(
        planted.replace('"expect": "valid"', '"expect": "cycle"'), encoding="utf-8"
    )
    after = scanned_valid(copy)
    assert set(after) - set(base) == {"planted_valid"}
    yielded = {p.name for kind in plans.KINDS for p in plans.valid_plans(kind, copy)}
    assert "planted_valid" in yielded and "planted_bad" not in yielded
    assert set(after) | set(scanned_generated()) == yielded
