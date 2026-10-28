"""`ckpt single`: the J-SINGLE condition module (CM-5, MC-28; L.SV-5.14).

`TRIGGER_MERGE = "J-SINGLE"`, `TAG = "wr-ckpt/single"`. The MC-28 framework evaluates it only on the
newest commit carrying `WR-Merge: J-SINGLE` (CM-5, DM-11); `--preview` evaluates the PR head. The
module is named neither `test_*.py` nor `*_test.py`, and importing it runs nothing: each condition
is declared as data (`SPEC`: the explicit node id or command that evaluates it) and is evaluated
only when the framework calls its `check`. The live nodes (`test_condition_a`, `test_condition_c`,
`test_condition_f`, and the audit plugin's two pytester negatives) live in
`tests/proof/ckpt/single_conditions.py`, a file no default pytest pattern matches (DM-80), so they
run only under `meta ckpt single` and where a command names the path; the audit plugin itself
(`single_vertex_audit.py`) only through `-p`. `tests/single/spine/test_ckpt_single.py` holds the
default-collected planted self-tests, true on every later head.

The conditions are plans/a1-single-level.md `### J-SINGLE`, (a)-(f):

  (a) an `AllDeclaration` root and a `ChoiceNode` root are each refused
      `admission.plan_multi_vertex_unsupported` with no run dir, no ledger, no spawned process and
      no idempotency record
  (b) the audited session (`tests/single`, `tests/proof/spine`, `tests/proof/suites` under the
      recorder plugin): every admission has one vertex and the nodeid-keyed vacuity guards hold;
      `differ d4` has zero diffs over the s0, core and single fossils
  (c) the 18 `step: single` clause parts PROVEN through a named gate (venue-BOTH ones also
      PROVEN@HOST through the host-proc record `record.select` picks at the evaluated commit), the
      `step: tree` clauses unproven and unclaimed, and CM-8's band rule: every deferral closing at
      an A-1 merge has its label PROVEN
  (d) termination and kind-freedom (spine-marked) and the early model, with termination
      parameterized over the SV-5 inputs and the SL fixtures
  (e) the foundations suite, nothing deselected, no `proves` marker under it
  (f) every guarantee suite green for `second_domain_free`; the SL-11 merge diff has no `trestle/**`
      path
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.proof import ckpt as ckpt_mod

TRIGGER_MERGE = "J-SINGLE"
TAG = "wr-ckpt/single"

ROOT = Path(__file__).resolve().parents[3]
CONDITIONS_FILE = "tests/proof/ckpt/single_conditions.py"
COMMIT_ENV = "TRESTLE_CKPT_COMMIT"  # the evaluated commit, handed to the live nodes (CM-6 anchor)
PROVEN = "PROVEN"
NAMED_GATES = ("ci-test", "ci-spine", "ci-long")  # CSC-13

# (c): the venue-BOTH single clause parts (plans/a1-single-level.md J-SINGLE (c)); every venue-BOTH
# claim label of `step: single` joins them at evaluation time.
BOTH_PARTS = ["A6.1:single", "A6.2:single", "A8.1", "A8.3"]
# (c): the labels of the deferrals closing in this band (CM-8), for the plain-language record
BAND_LABELS = [
    "WR-OWN-8:environment-lease",
    "WR-CANCEL-3:later-run-admitted-after-answer",
    "A1.3:single",
]
# (d): the termination inputs that must be collected once the SL leaves have landed
SL_TERMINATION_INPUTS = ["transient_leaf", "slow_converge_leaf", "lagging_leaf", "remedy_leaf"]
TERMINATION_NODE = "test_run_tree_terminates_over_every_one_vertex_input"
KIND_FREEDOM_NODE = "test_no_kind_branch_outside_decide"
SECOND_WORKFLOW = "second_domain_free"
SL11_MERGE = "SL-11"

PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]


@dataclass(frozen=True)
class Command:
    """One command a condition runs: its argv, and what its combined output must (not) contain."""

    argv: tuple[str, ...]
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    hands_commit: bool = False  # the evaluated commit is put in the environment


@dataclass(frozen=True)
class ConditionSpec:
    """A condition declared as data: the explicit node id or command that evaluates it, and, for
    the part no command covers, a pure `extra` check over the evaluated commit."""

    id: str
    title: str
    commands: tuple[Command, ...] = ()
    extra: Callable[[str], tuple[bool, str]] | None = None


# ---------------------------------------------------------------------------
# pure functions (importable and planted by the self-test)
# ---------------------------------------------------------------------------


def clause_parts(clause: dict[str, Any]) -> list[tuple[str, str]]:
    """A matrix clause's ledger keys with the step each part carries (MC-03)."""
    if "parts" in clause:
        return [(f"{clause['id']}:{p['name']}", str(p["step"])) for p in clause["parts"]]
    return [(str(clause["id"]), str(clause.get("step", "")))]


def single_parts(clauses: Iterable[dict[str, Any]]) -> list[str]:
    """The `step: single` clause parts (18 in the shipped matrix map)."""
    return [key for c in clauses for key, step in clause_parts(c) if step.startswith("single")]


def tree_parts(clauses: Iterable[dict[str, Any]]) -> list[str]:
    """The `step: tree` clause parts (A-2's, unclaimed at J-SINGLE)."""
    return [key for c in clauses for key, step in clause_parts(c) if step.startswith("tree")]


def a1_merges(config: Path | None = None) -> set[str]:
    """The A-1 merge ids: the gates of `fence.d/single.toml` but the checkpoint itself."""
    path = config or ROOT / "tests" / "proof" / "fence.d" / "single.toml"
    gates = tomllib.loads(path.read_text(encoding="utf-8")).get("gate", [])
    return {str(g["merge"]) for g in gates} - {"J-SINGLE"}


def closing_set(deferrals: Iterable[dict[str, Any]], merges: set[str]) -> list[str]:
    """CM-8's band rule, first half: the labels of every deferral whose `closes_at` is one of
    `merges`."""
    return sorted(str(d["label"]) for d in deferrals if d.get("closes_at") in merges)


def closing_violations(
    deferrals: Iterable[dict[str, Any]], merges: set[str], proven: set[str]
) -> list[str]:
    """CM-8's band rule, second half: each label of the closing set must be PROVEN at the
    candidate (read at the candidate, never copied from an earlier checkpoint)."""
    from tests.proof import deferrals as deferrals_mod

    return deferrals_mod.band_rule_violations(list(deferrals), merges, proven)


def part_problems(
    report: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    parts: Iterable[str],
    *,
    both: Iterable[str] = (),
    host_record: dict[str, Any] | None = None,
) -> list[str]:
    """Each part PROVEN in the ledger and through a named gate at CI on 3.12; each venue-BOTH part
    also PROVEN@HOST in `host_record` (the record `record.select` returned, resolved by the caller
    at the evaluated commit; this function implements neither the selection nor the pass set)."""
    problems: list[str] = []
    for part in parts:
        if report.get(part, {}).get("status") != PROVEN:
            problems.append(f"{part} is not PROVEN in the ledger")
        elif not any(
            part in (r.get("labels") or [])
            and r.get("gate") in NAMED_GATES
            and r.get("venue") == "CI"
            and r.get("outcome") == "passed"
            and str(r.get("interpreter", "")).startswith("3.12")
            for r in records
        ):
            problems.append(f"{part} is not PROVEN@CI through a named gate {NAMED_GATES}")
    both_list = list(both)
    if both_list:
        if host_record is None:
            problems.append(
                f"no admissible host-proc record at the evaluated commit (venue-BOTH: "
                f"{both_list[:3]}...): a record is stale once any non-record path changed after "
                "its sha (CM-6)"
            )
        else:
            for part in both_list:
                hits = [
                    r for r in host_record.get("results", []) if part in (r.get("labels") or [])
                ]
                if not hits or any(r.get("outcome") != "PASSED" for r in hits):
                    problems.append(f"{part} is not PROVEN@HOST in the host-proc record")
    return problems


def unclaimed_problems(report: dict[str, dict[str, Any]], parts: Iterable[str]) -> list[str]:
    """Every `step: tree` clause part is UNPROVEN and unclaimed (no result names it)."""
    return [
        f"tree clause {part} is claimed ({report[part].get('status')})"
        for part in parts
        if part in report
    ]


def no_proves_marker(root: Path) -> list[str]:
    """Every `proves(...)` marker use under `root` (a foundations file claims no clause)."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "proves":
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}: a proves marker")
            elif isinstance(node, ast.Name) and node.id == "proves":
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}: a proves marker")
    return found


# ---------------------------------------------------------------------------
# running a spec (only from a condition's `check`, never at import)
# ---------------------------------------------------------------------------


def _label(command: Command) -> str:
    """The command as a reader would type it: no interpreter, no `-q`, no `-p no:cacheprovider`."""
    words = list(command.argv[1:])
    for noise in (["-q"], ["-p", "no:cacheprovider"]):
        for i in range(len(words) - len(noise) + 1):
            if words[i : i + len(noise)] == noise:
                del words[i : i + len(noise)]
                break
    return " ".join(words)[:200]


def _run(command: Command, commit: str) -> tuple[bool, str]:
    from tests.proof import fence as fence_mod

    env = dict(os.environ)
    root = str(ROOT)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH")]))
    if command.hands_commit:
        env[COMMIT_ENV] = commit
    try:
        proc = subprocess.run(
            list(command.argv),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=fence_mod.HOST_RUN_MAX,
        )
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(command.argv[-3:])}: timed out"
    output = proc.stdout + proc.stderr
    label = _label(command)
    if proc.returncode != 0:
        tail = " | ".join(line for line in output.splitlines() if line.strip())[-300:]
        return False, f"`{label}` exited {proc.returncode}: {tail}"
    for needle in command.must_contain:
        if needle not in output:
            return False, f"`{label}`: output does not show {needle!r}"
    for needle in command.must_not_contain:
        if needle in output:
            return False, f"`{label}`: output shows {needle!r}"
    return True, ""


def _check(spec: ConditionSpec) -> Callable[[str], tuple[bool, str]]:
    def check(commit: str) -> tuple[bool, str]:
        for command in spec.commands:
            ok, reason = _run(command, commit)
            if not ok:
                return False, reason
        if spec.extra is not None:
            return spec.extra(commit)
        return True, ""

    return check


def _pytest(*args: str, hands_commit: bool = False, **checks: tuple[str, ...]) -> Command:
    return Command(tuple(PYTEST + list(args)), hands_commit=hands_commit, **checks)


def _foundations_extra(commit: str) -> tuple[bool, str]:
    found = no_proves_marker(ROOT / "tests" / "proof" / "foundations")
    return (not found, "; ".join(found[:4]))


SPEC: dict[str, ConditionSpec] = {
    "a": ConditionSpec(
        "J-SINGLE-a",
        "the two composite roots are refused, with no effect of any kind",
        (_pytest(f"{CONDITIONS_FILE}::test_condition_a"),),
    ),
    "b": ConditionSpec(
        "J-SINGLE-b",
        "the audited session: every admission one vertex, the nodeid-keyed vacuity guards, d4",
        (
            _pytest(
                "tests/single",
                "tests/proof/spine",
                "tests/proof/suites",
                "-p",
                "tests.proof.ckpt.single_vertex_audit",
                must_contain=("single vertex audit: ok",),
            ),
            Command((sys.executable, "-m", "tests.proof.differ", "d4")),
        ),
    ),
    "c": ConditionSpec(
        "J-SINGLE-c",
        "the 18 single clause parts PROVEN, the tree ones unclaimed, the band's deferrals closed",
        (_pytest(f"{CONDITIONS_FILE}::test_condition_c", hands_commit=True),),
    ),
    "d": ConditionSpec(
        "J-SINGLE-d",
        "termination over the real loop and kind-freedom (spine-marked), and the early model",
        (
            _pytest(
                "-m",
                "spine",
                "tests/proof/spine/test_termination.py",
                "tests/proof/spine/test_kind_freedom.py",
            ),
            _pytest("tests/single/workflow/test_termination_model.py"),
            _pytest(
                "-m",
                "spine",
                "tests/proof/spine/test_termination.py",
                "tests/proof/spine/test_kind_freedom.py",
                "--collect-only",
                must_contain=(
                    KIND_FREEDOM_NODE,
                    *(f"{TERMINATION_NODE}[{name}]" for name in SL_TERMINATION_INPUTS),
                ),
            ),
        ),
    ),
    "e": ConditionSpec(
        "J-SINGLE-e",
        "the foundations suite, nothing deselected, no proves marker under it",
        (
            _pytest(
                "tests/proof/foundations",
                "-m",
                "foundations",
                must_not_contain=("deselected",),
            ),
        ),
        _foundations_extra,
    ),
    "f": ConditionSpec(
        "J-SINGLE-f",
        "every guarantee suite green for second_domain_free; the SL-11 diff has no trestle/** path",
        (_pytest(f"{CONDITIONS_FILE}::test_condition_f", hands_commit=True),),
    ),
}


def _build() -> list[ckpt_mod.Condition]:
    return [ckpt_mod.Condition(spec.id, _check(spec)) for spec in SPEC.values()]


CONDITIONS = _build()

assert [c.id for c in CONDITIONS] == [f"J-SINGLE-{x}" for x in "abcdef"]

__all__ = [
    "TRIGGER_MERGE",
    "TAG",
    "CONDITIONS",
    "SPEC",
    "single_parts",
    "tree_parts",
    "closing_set",
    "closing_violations",
]
