"""`ckpt slice-a`: the J-SLICE-A condition module (CM-5, MC-28; L.TR-6.7).

`TRIGGER_MERGE = "J-SLICE-A"`, `TAG = "wr-ckpt/slice-a"`. The MC-28 framework evaluates it only on
the newest commit carrying `WR-Merge: J-SLICE-A` (CM-5, DM-11); `--preview` evaluates the PR head
and `--dry` lists what is still pending on any commit. The module is named neither `test_*.py` nor
`*_test.py` and importing it runs nothing (DM-80): a condition is a pure function of a `World`
(plain data), and `LiveWorld` fills the same attributes, lazily, from the checkout the first time a
condition reads them. `tests/tree/test_ckpt_slice_a.py` holds the default-collected planted
self-tests over synthetic worlds and never runs a live condition, so it stays true on every later
head. The module lands in a product merge before the checkpoint merge (CM-5): the checkpoint lane's
globs hold only fossils and reviews.

The conditions are plans/a2-tree.md `### J-SLICE-A` S0, S2..S14 (S1 is the evaluation itself, not a
condition the module can require):

  S0  host evidence: `record.select("host-proc", HEAD)` (CM-6's explicit-anchor selection) exists,
      is admissible, and meets CM-6's role-2 pass set (`record.pass_set_violations`), whatever phase
      owns a node; the named P0 node `live_records.py::test_committed_records_valid_for_head` passes
  S2  the twelve tree clauses PROVEN through a named gate, none held: `meta register` is clean and
      the five mechanisms of the band are `absent`
  S3  WR-PROOF-1 for column A: every clause of cells A1..A9 PROVEN, evaluated here over the MC-02
      ledger and the S0 record (never a report-mode exit code or a withdrawn flag, CSC-5)
  S4  every `labels.d/tree.toml` label owned: >= 1 passing registered test at its tier and venue, or
      gated on OQ-27 / both-variant OQ-31; a skipped, xfailed or unrun test counts UNPROVEN
  S5  J-TRL still holds: `test_j_trl.py`, and `lift_set_check` over the ledger this module rendered
      (it sets `TRESTLE_LIFT_LEDGER` itself)
  S6  the equivalence proofs d4, d5, d6, d7
  S7  the spine gate
  S8  the backward straddle: `differ d2 --reader wr-ckpt/single`, and the drain exceptions exactly
      where MC-31 records TR-L / TR-5 as `drain`
  S9  the open questions untouched (OQ-25, OQ-27, OQ-30, OQ-31 listed; OQ-32 answered, not listed)
  S10 the slice-a fossils read back under head, with an in-flight and a terminal multi-vertex root
  S11 the baseline (SC-2): ruff check, ruff format --check, mypy
  S12 RV-5 transcription record present, valid, passing, an ancestor of the candidate
  S13 CM-8: every deferral closing in TR-0..TR-6 has its label PROVEN at the candidate
  S14 the join C-id closures (DM-58)

The module does not change TM-P0-6's state: column A is enforced here, which is neither a remover
nor a phase of TM-P0-6 (remover `L.CZ.1`); `meta enforce` and `meta audit-rows` stay in report mode.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.proof import ckpt as ckpt_mod
from tests.proof import fence as fence_mod

TRIGGER_MERGE = "J-SLICE-A"
TAG = "wr-ckpt/slice-a"

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable
PROVEN = "PROVEN"
NAMED_GATES = ("ci-test", "ci-spine", "ci-long")  # CSC-13
LEDGER_ENV = "TRESTLE_LIFT_LEDGER"  # `tests/tree/joins/lift_set_check.py`'s ledger variable (IC-13)
FOSSILS_ROOT = ROOT / "tests" / "fixtures" / "fossils"
SLICE_A_FOSSILS = "slice-a"
LINT_PATHS = [
    "trestle",
    "tests",
    "packages/trestle-packs",
    "conftest.py",
    "scripts/smoke_packs.py",
    "scripts/demo_pack_workflows.py",
]

# S2: the twelve tree clauses, and the mechanisms of the band that must read `absent`
TREE_CLAUSES = [
    "A1.5", "A1.6", "A2.5", "A2.6", "A3.4", "A4.2", "A5.3", "A5.4", "A6.3", "A6.4", "A8.4", "A8.5",
]  # fmt: skip
ABSENT_MECHANISMS = [
    "multi-vertex-refusal",
    "one-vertex-spine-fixture",
    "loop-walk-children-absent",
    "admission-audit-hook",
    "TM-C3",
]
# S4: the open-question postures (C.9)
OQ_POSTURES = {"OQ-27": "gated_on", "OQ-31": "both_variant"}
# S9: the open questions this band leaves in their C.9 postures, and the one it answered
OPEN_QUESTIONS = {"OQ-25", "OQ-27", "OQ-30", "OQ-31"}
ANSWERED_QUESTION = "OQ-32"
# S8: the drain exceptions of MC-31's boundaries (rollback.toml merge -> D2 exception id)
DRAIN_EXCEPTIONS = {"TR-L": "E-TRL-DRAIN", "TR-5": "E-TR5-DRAIN"}
# S12
REVIEW = "RV-5"
REVIEW_FILE = f"{REVIEW}-slice-a.toml"
# S13: the merges of the band whose deferrals close here (CM-8)
TR_MERGES = {f"TR-{n}" for n in range(0, 7)}
# S14
CONTRACT_PARITY_NODES = [
    "tests/tree/test_tr0_identity.py::test_declared_tree_digest_covers_descendants",
    "tests/tree/test_tr0_publication.py::test_cycle_refused_at_publication_no_snapshot",
    "tests/tree/test_tr3_digest.py::test_descendant_decl_changed_after_admission_stops_zero_effects",
]
CONTAINMENT_MODULE = "tests/tree/host/test_tr5_containment.py"
TREE_HOST_PREFIX = "tests/tree/"
JOIN_LABEL_ROWS = ("C-RECORD-INTEGRITY", "C-RUNTIME-NEUTRAL", "C-CONTAIN-AND-CLOCK")
ANSWER_BOUNDED_LABEL = (
    "WR-UNIT-7:hundred-node-budget"  # C-ANSWER-BOUNDED's leaf (L.TR-6.4; D7 is S6)
)

CommandResult = tuple[int, str]


# ---------------------------------------------------------------------------
# the world: every input a condition reads
# ---------------------------------------------------------------------------


@dataclass
class World:
    """Plain data: the self-test builds these; `LiveWorld` fills the same names lazily. A command
    result is `(exit code, output)`; the default is a clean exit."""

    report: dict[str, dict[str, Any]] = field(default_factory=dict)  # the rendered MC-02 ledger
    records: list[dict[str, Any]] = field(default_factory=list)  # results/*.jsonl records
    labels: list[dict[str, Any]] = field(default_factory=list)  # labels.d/*.toml (CSC-1)
    clauses: list[dict[str, Any]] = field(default_factory=list)  # matrix_map.toml (MC-03)
    both_clauses: set[str] = field(default_factory=set)  # clause keys carried at venue BOTH
    deferrals: list[dict[str, Any]] = field(default_factory=list)  # deferrals.toml (CM-8)
    host_record: dict[str, Any] | None = None  # record.select("host-proc", HEAD)
    s0_node: CommandResult = (0, "")  # the named P0 node
    register: dict[str, bool] = field(default_factory=dict)  # entry id -> present
    register_violations: list[str] = field(default_factory=list)  # `meta register`'s rule
    s5_trl: CommandResult = (0, "")  # test_j_trl.py
    s5_lift: CommandResult = (0, "")  # lift_set_check over the ledger this module rendered
    d4: CommandResult = (0, "")
    d5: CommandResult = (0, "")
    d6: CommandResult = (0, "")
    d7: CommandResult = (0, "")
    spine: CommandResult = (0, "")  # pytest -m spine
    d2_single: CommandResult = (0, "")  # d2 --reader wr-ckpt/single
    rollback: list[dict[str, Any]] = field(default_factory=list)  # rollback.toml boundaries
    d2_exceptions: list[dict[str, Any]] = field(default_factory=list)  # d2_exceptions.toml
    open_questions: CommandResult = (0, "")  # meta open-questions
    open_question_ids: set[str] = field(default_factory=set)
    d2_head: CommandResult = (0, "")  # d2 --reader HEAD over the slice-a fossils
    slice_a_states: list[str] = field(default_factory=list)  # slice-a fossil state ids
    lint: dict[str, CommandResult] = field(default_factory=dict)  # ruff check / format / mypy
    review: dict[str, Any] | None = None  # RV-5-slice-a.toml as {record, error, ancestor, shown}


def clause_parts(clause: dict[str, Any]) -> list[str]:
    """A matrix clause's ledger keys: its id, or `<id>:<part>` for each part (MC-03)."""
    if "parts" in clause:
        return [f"{clause['id']}:{part['name']}" for part in clause["parts"]]
    return [str(clause["id"])]


def column_a_keys(clauses: list[dict[str, Any]]) -> list[str]:
    """Every ledger key of the clauses of cells A1..A9 (the `:A` cells of the matrix map)."""
    return [
        key
        for clause in clauses
        if str(clause.get("cell", "")).endswith(":A")
        for key in clause_parts(clause)
    ]


def _fail(problems: list[str], limit: int = 6) -> tuple[bool, str]:
    if not problems:
        return True, ""
    shown = problems[:limit]
    more = f" (+{len(problems) - limit} more)" if len(problems) > limit else ""
    return False, "; ".join(shown) + more


def _proven(w: World, key: str) -> bool:
    return w.report.get(key, {}).get("status") == PROVEN


def _named_gate_pass(records: list[dict[str, Any]], key: str) -> bool:
    return any(
        key in (r.get("labels") or [])
        and r.get("gate") in NAMED_GATES
        and r.get("venue") == "CI"
        and r.get("outcome") == "passed"
        and str(r.get("interpreter", "")).startswith("3.12")
        for r in records
    )


def _host_hits(record: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if record is None:
        return []
    return [r for r in record.get("results", []) if key in (r.get("labels") or [])]


def key_problems(w: World, key: str, venue: str) -> list[str]:
    """One clause or label key at its venue. CI: PROVEN in the ledger through a named gate on 3.12.
    HOST: PASSED in the S0-selected record. BOTH: both (the CI half and the host half). A skipped,
    xfailed or unrun node renders UNPROVEN (MC-02), and in the record any outcome but PASSED is no
    proof."""
    problems: list[str] = []
    if venue in ("CI", "BOTH"):
        if not _proven(w, key):
            problems.append(f"{key} is not PROVEN in the ledger")
        elif not _named_gate_pass(w.records, key):
            problems.append(f"{key} is not PROVEN@CI through a named gate {NAMED_GATES}")
    if venue in ("HOST", "BOTH"):
        if w.host_record is None:
            problems.append(f"{key} ({venue}): no admissible host-proc record for the candidate")
        else:
            hits = _host_hits(w.host_record, key)
            if not hits or any(r.get("outcome") != "PASSED" for r in hits):
                problems.append(f"{key} is not PROVEN@HOST in the host-proc record")
    return problems


def clause_venue(w: World, key: str) -> str:
    return "BOTH" if key in w.both_clauses else "CI"


# ---------------------------------------------------------------------------
# S0 host evidence
# ---------------------------------------------------------------------------


def verdict_s0(w: World) -> tuple[bool, str]:
    from tests.proof.host import record as record_mod

    problems: list[str] = []
    if w.host_record is None:
        problems.append(
            "no admissible host-proc record for the candidate: a record is stale once any "
            "non-record path changed after its sha (CM-6)"
        )
    else:
        by_id = {str(lb["id"]): lb for lb in w.labels}
        problems += record_mod.pass_set_violations(w.host_record, by_id)
    if w.s0_node[0] != 0:
        problems.append(
            f"live_records.py::test_committed_records_valid_for_head: {_tail(w.s0_node)}"
        )
    return _fail(problems)


def _tail(result: CommandResult, size: int = 200) -> str:
    rc, output = result
    lines = " | ".join(line for line in output.splitlines() if line.strip())
    return f"exit {rc}: {lines[-size:]}"


# ---------------------------------------------------------------------------
# S2 the twelve tree clauses, none held
# ---------------------------------------------------------------------------


def verdict_s2(w: World) -> tuple[bool, str]:
    problems: list[str] = [f"register: {v}" for v in w.register_violations]
    for mechanism in ABSENT_MECHANISMS:
        if mechanism not in w.register:
            problems.append(f"{mechanism} is not a register entry (its absence cannot be read)")
        elif w.register[mechanism]:
            problems.append(f"{mechanism} is still present")
    known = {key for clause in w.clauses for key in clause_parts(clause)}
    for clause in TREE_CLAUSES:
        if clause not in known:
            problems.append(f"{clause} is not a clause of the matrix map")
            continue
        problems += key_problems(w, clause, clause_venue(w, clause))
    return _fail(problems)


# ---------------------------------------------------------------------------
# S3 WR-PROOF-1 for column A
# ---------------------------------------------------------------------------


def verdict_s3(w: World) -> tuple[bool, str]:
    keys = column_a_keys(w.clauses)
    if not keys:
        return False, "the matrix map has no clause of cells A1..A9"
    problems: list[str] = []
    for key in keys:
        problems += key_problems(w, key, clause_venue(w, key))
    return _fail(problems)


# ---------------------------------------------------------------------------
# S4 every tree label owned
# ---------------------------------------------------------------------------


def tree_labels(w: World) -> list[dict[str, Any]]:
    return [lb for lb in w.labels if lb.get("step") == "tree"]


def label_problems(w: World, label: dict[str, Any]) -> list[str]:
    """A label needs >= 1 passing registered test at its venue, or is gated on an open question or
    both-variant (a posture no result decides)."""
    if label.get("posture") in ("gated_on", "both_variant"):
        return []
    return key_problems(w, str(label["id"]), str(label.get("venue", "CI")))


def verdict_s4(w: World) -> tuple[bool, str]:
    labels = tree_labels(w)
    if not labels:
        return False, "labels.d/tree.toml declares no tree label"
    problems: list[str] = []
    for label in labels:
        problems += label_problems(w, label)
    return _fail(problems)


# ---------------------------------------------------------------------------
# S5 J-TRL still holds
# ---------------------------------------------------------------------------


def verdict_s5(w: World) -> tuple[bool, str]:
    problems = []
    if w.s5_trl[0] != 0:
        problems.append(f"tests/tree/joins/test_j_trl.py: {_tail(w.s5_trl)}")
    if w.s5_lift[0] != 0:
        problems.append(f"lift_set_check over the rendered ledger: {_tail(w.s5_lift)}")
    return _fail(problems)


def lift_ledger_records(
    records: list[dict[str, Any]], host_record: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The ledger S5 hands `lift_set_check`: the CI result records and the S0 record's results as
    HOST records (`outcome` spelt as the records spell it; `PASSED` is `passed`)."""
    out = list(records)
    if host_record is not None:
        for result in host_record.get("results", []):
            out.append(
                {
                    "nodeid": result.get("nodeid"),
                    "outcome": str(result.get("outcome", "")).lower(),
                    "gate": host_record.get("gate"),
                    "venue": "HOST",
                    "interpreter": host_record.get("python", ""),
                    "labels": list(result.get("labels") or []),
                }
            )
    return out


# ---------------------------------------------------------------------------
# S6 equivalence, S7 spine
# ---------------------------------------------------------------------------


def verdict_s6(w: World) -> tuple[bool, str]:
    problems = [
        f"differ {mode}: {_tail(getattr(w, mode))}"
        for mode in ("d4", "d5", "d6", "d7")
        if getattr(w, mode)[0] != 0
    ]
    return _fail(problems)


def verdict_s7(w: World) -> tuple[bool, str]:
    if w.spine[0] != 0:
        return False, f"pytest -m spine: {_tail(w.spine)}"
    return True, ""


# ---------------------------------------------------------------------------
# S8 the backward straddle
# ---------------------------------------------------------------------------


def drain_problems(rollback: list[dict[str, Any]], exceptions: list[dict[str, Any]]) -> list[str]:
    """The drain exceptions exactly where MC-31 records TR-L / TR-5 as `drain` (A2c2-1)."""
    problems = []
    ids = {str(e.get("id")) for e in exceptions}
    classes = {str(b.get("merge")): str(b.get("class")) for b in rollback}
    for merge, exception in DRAIN_EXCEPTIONS.items():
        if merge not in classes:
            problems.append(f"rollback.toml records no boundary for {merge}")
        elif (classes[merge] == "drain") != (exception in ids):
            problems.append(
                f"{merge} is {classes[merge]!r} in rollback.toml but {exception} "
                f"{'is' if exception in ids else 'is not'} declared"
            )
    return problems


def verdict_s8(w: World) -> tuple[bool, str]:
    problems = []
    if w.d2_single[0] != 0:
        problems.append(f"differ d2 --reader wr-ckpt/single: {_tail(w.d2_single)}")
    problems += drain_problems(w.rollback, w.d2_exceptions)
    return _fail(problems)


# ---------------------------------------------------------------------------
# S9 open questions
# ---------------------------------------------------------------------------


def verdict_s9(w: World) -> tuple[bool, str]:
    problems = []
    if w.open_questions[0] != 0:
        problems.append(f"meta open-questions: {_tail(w.open_questions)}")
    if missing := sorted(OPEN_QUESTIONS - w.open_question_ids):
        problems.append(f"open questions {missing} are not listed")
    if ANSWERED_QUESTION in w.open_question_ids:
        problems.append(f"{ANSWERED_QUESTION} is answered, so it is not listed (C.9)")
    for label in w.labels:
        oq = label.get("oq")
        if oq in OQ_POSTURES and label.get("posture") != OQ_POSTURES[oq]:
            problems.append(f"{label.get('id')}: {oq} posture is {label.get('posture')!r}")
    return _fail(problems)


# ---------------------------------------------------------------------------
# S10 fossils snapshot
# ---------------------------------------------------------------------------


def _multi_vertex_kind(state_id: str, kind: str) -> bool:
    """A tree-band state (`trl-`/`tr5-`) of the kind `inflight` or `terminal` (a multi-vertex
    root)."""
    return re.match(rf"^(trl|tr5)-{kind}-", state_id) is not None


def verdict_s10(w: World) -> tuple[bool, str]:
    problems = []
    if not w.slice_a_states:
        problems.append(f"tests/fixtures/fossils/{SLICE_A_FOSSILS}/ holds no state")
    else:
        for kind in ("inflight", "terminal"):
            if not any(_multi_vertex_kind(s, kind) for s in w.slice_a_states):
                problems.append(f"no {kind} multi-vertex root among the slice-a states")
    if w.d2_head[0] != 0:
        problems.append(f"differ d2 --reader HEAD over the slice-a fossils: {_tail(w.d2_head)}")
    return _fail(problems)


# ---------------------------------------------------------------------------
# S11 baseline
# ---------------------------------------------------------------------------


def verdict_s11(w: World) -> tuple[bool, str]:
    wanted = ("ruff check", "ruff format --check", "mypy")
    problems = []
    for name in wanted:
        result = w.lint.get(name)
        if result is None:
            problems.append(f"{name} was not run")
        elif result[0] != 0:
            problems.append(f"{name}: {_tail(result)}")
    return _fail(problems)


# ---------------------------------------------------------------------------
# S12 RV-5
# ---------------------------------------------------------------------------


def verdict_s12(w: World) -> tuple[bool, str]:
    info = w.review
    if info is None:
        return False, f"tests/proof/reviews/{REVIEW_FILE} is absent"
    if info.get("error"):
        return False, f"{REVIEW_FILE}: {info['error']}"
    record = info["record"]
    if record.get("outcome") != "pass":
        return False, f"{REVIEW} outcome is {record.get('outcome')!r}, expected pass"
    if not info.get("ancestor"):
        return (
            False,
            f"{REVIEW} reviewed sha {record.get('sha')} is not an ancestor of the candidate",
        )
    if not info.get("shown"):
        return False, f"the ledger does not show review:{REVIEW}"
    return True, ""


# ---------------------------------------------------------------------------
# S13 deferrals closing in the band (CM-8)
# ---------------------------------------------------------------------------


def verdict_s13(w: World) -> tuple[bool, str]:
    from tests.proof import deferrals as deferrals_mod

    closing = [d for d in w.deferrals if d.get("closes_at") in TR_MERGES]
    if not closing:
        return False, "no deferral closes in TR-0..TR-6 (WR-TERM-5:tree-size is declared)"
    proven = {key for key in w.report if _proven(w, key)}
    problems = deferrals_mod.band_rule_violations(w.deferrals, TR_MERGES, proven)
    return _fail(problems)


# ---------------------------------------------------------------------------
# S14 the join C-id closures (DM-58)
# ---------------------------------------------------------------------------


def node_passed(records: list[dict[str, Any]], nodeid: str) -> bool:
    """A node PASSED in the ledger's records: at least one passing named-gate record, no other
    outcome (a parametrized node is every `nodeid[...]`)."""
    mine = [
        r
        for r in records
        if r.get("nodeid") == nodeid or str(r.get("nodeid")).startswith(nodeid + "[")
    ]
    return (
        bool(mine)
        and all(r.get("outcome") == "passed" for r in mine)
        and any(r.get("gate") in NAMED_GATES for r in mine)
    )


def verdict_s14(w: World) -> tuple[bool, str]:
    problems: list[str] = []
    for node in CONTRACT_PARITY_NODES:  # C-CONTRACT-PARITY
        if not node_passed(w.records, node):
            problems.append(f"C-CONTRACT-PARITY: {node} is not PASSED in the ledger")
    # C-CONTAIN-AND-CLOCK: every venue-BOTH tree node, incl. the containment module, PASSED in S0
    if w.host_record is None:
        problems.append("C-CONTAIN-AND-CLOCK: no admissible host-proc record")
    else:
        results = [
            r
            for r in w.host_record.get("results", [])
            if str(r.get("nodeid", "")).startswith(TREE_HOST_PREFIX)
        ]
        if not any(str(r["nodeid"]).startswith(CONTAINMENT_MODULE + "::") for r in results):
            problems.append(f"C-CONTAIN-AND-CLOCK: no {CONTAINMENT_MODULE} node in the S0 record")
        problems += [
            f"C-CONTAIN-AND-CLOCK: {r['nodeid']} is {r.get('outcome')}"
            for r in results
            if r.get("outcome") != "PASSED"
        ]
    for label in tree_labels(w):  # C-RECORD-INTEGRITY, C-RUNTIME-NEUTRAL, C-CONTAIN-AND-CLOCK
        if str(label.get("row")) in JOIN_LABEL_ROWS:
            problems += label_problems(w, label)
    # C-ANSWER-BOUNDED (D7 is S6)
    if not _proven(w, ANSWER_BOUNDED_LABEL):
        problems.append(f"C-ANSWER-BOUNDED: {ANSWER_BOUNDED_LABEL} is not PROVEN")
    return _fail(problems)


# ---------------------------------------------------------------------------
# reading the checkout (only from a condition's `check`, never at import)
# ---------------------------------------------------------------------------


def _run(argv: list[str], env: dict[str, str] | None = None) -> CommandResult:
    """One bounded subprocess in the repository root; a timeout is a failed run (exit 124)."""
    merged = dict(os.environ, **(env or {}))
    merged["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), merged.get("PYTHONPATH")]))
    try:
        proc = subprocess.run(
            argv,
            cwd=ROOT,
            env=merged,
            capture_output=True,
            text=True,
            timeout=fence_mod.HOST_RUN_MAX,
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return proc.returncode, proc.stdout + proc.stderr


def _pytest(*args: str, env: dict[str, str] | None = None) -> CommandResult:
    return _run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], env)


def both_clauses_in(root: Path) -> set[str]:
    """Every matrix clause key a `proves(row, clause, slice, step, tier, "BOTH")` marker under
    `root` names (read from the source, so nothing is collected or run)."""
    from tests.proof import transcribe as transcribe_mod

    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 6:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            values = [a.value if isinstance(a, ast.Constant) else None for a in node.args]
            if name == "proves" and values[5] == "BOTH" and isinstance(values[1], str):
                if transcribe_mod.MATRIX_CLAUSE_RE.match(values[1].split(":", 1)[0]):
                    found.add(values[1])
    return found


class LiveWorld:
    """The same attributes as `World`, each read from the checkout the first time it is used."""

    def __init__(self, commit: str) -> None:
        self.commit = commit

    def __getattr__(self, name: str) -> Any:
        loader = getattr(type(self), f"_load_{name}", None)
        if loader is None:
            raise AttributeError(name)
        value = loader(self)
        setattr(self, name, value)
        return value

    def _load_report(self) -> dict[str, dict[str, Any]]:
        from tests.proof import ledger as ledger_mod

        return ledger_mod.render()

    def _load_records(self) -> list[dict[str, Any]]:
        from tests.proof import results as results_mod

        return results_mod.read_records()

    def _load_labels(self) -> list[dict[str, Any]]:
        from tests.proof import meta as meta_mod

        return list(meta_mod._load_all_labels())  # noqa: SLF001

    def _load_clauses(self) -> list[dict[str, Any]]:
        from tests.proof import transcribe as transcribe_mod

        return list(transcribe_mod.load_matrix_map())

    def _load_both_clauses(self) -> set[str]:
        return both_clauses_in(ROOT / "tests")

    def _load_deferrals(self) -> list[dict[str, Any]]:
        from tests.proof import deferrals as deferrals_mod

        return deferrals_mod.load_deferrals()

    def _load_host_record(self) -> dict[str, Any] | None:
        from tests.proof.host import record as record_mod

        return record_mod.select("host-proc", self.commit, cwd=ROOT)

    def _load_s0_node(self) -> CommandResult:
        return _pytest("tests/proof/host/live_records.py::test_committed_records_valid_for_head")

    def _load_register(self) -> dict[str, bool]:
        from tests.proof import register as register_mod

        return {
            str(e["id"]): register_mod.active_phase(e) is not None
            for e in register_mod.load_entries()
        }

    def _load_register_violations(self) -> list[str]:
        from tests.proof import register as register_mod

        return [
            f"{v} is claimed while a present entry serves it"
            for v in register_mod.register_violations()
        ]

    def _load_s5_trl(self) -> CommandResult:
        return _pytest("tests/tree/joins/test_j_trl.py")

    def _load_s5_lift(self) -> CommandResult:
        """`lift_set_check` over the ledger this module rendered: S5 sets `LEDGER_ENV` itself
        (IC-13), so neither the ckpt job nor a person passes it."""
        records = lift_ledger_records(self.records, self.host_record)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text(json.dumps(records), encoding="utf-8")
            return _pytest(
                "tests/tree/joins/lift_set_check.py::test_lift_set_closed",
                env={LEDGER_ENV: str(ledger)},
            )

    def _differ(self, mode: str, *args: str) -> CommandResult:
        return _run([PY, "-m", "tests.proof.differ", mode, *args])

    def _load_d4(self) -> CommandResult:
        return self._differ("d4")

    def _load_d5(self) -> CommandResult:
        return self._differ("d5")

    def _load_d6(self) -> CommandResult:
        return self._differ("d6")

    def _load_d7(self) -> CommandResult:
        return self._differ("d7")

    def _load_spine(self) -> CommandResult:
        return _pytest("-m", "spine")

    def _load_d2_single(self) -> CommandResult:
        return self._differ("d2", "--reader", "wr-ckpt/single")

    def _load_rollback(self) -> list[dict[str, Any]]:
        data = tomllib.loads((ROOT / "tests" / "proof" / "rollback.toml").read_text())
        return list(data.get("boundary", []))

    def _load_d2_exceptions(self) -> list[dict[str, Any]]:
        data = tomllib.loads((ROOT / "tests" / "proof" / "d2_exceptions.toml").read_text())
        return list(data.get("exception", []))

    def _load_open_questions(self) -> CommandResult:
        return _run([PY, "-m", "tests.proof.meta", "open-questions"])

    def _load_open_question_ids(self) -> set[str]:
        from tests.proof import meta as meta_mod

        return {str(o["id"]) for o in meta_mod.load_open_questions()}

    def _load_slice_a_states(self) -> list[str]:
        from tests.proof import fossils as fossils_mod

        band = FOSSILS_ROOT / SLICE_A_FOSSILS
        if not (band / "MANIFEST.toml").exists():
            return []
        return [
            sid
            for sid, (b, entry) in fossils_mod.load_states(FOSSILS_ROOT).items()
            if b == SLICE_A_FOSSILS and not entry.get("absent")
        ]

    def _load_d2_head(self) -> CommandResult:
        """`differ d2` takes a root of bands (the plan's literal `--fossils .../slice-a` names a
        band and would check nothing), so it is given a scratch root holding the slice-a band."""
        band = FOSSILS_ROOT / SLICE_A_FOSSILS
        if not band.is_dir():
            return 1, f"tests/fixtures/fossils/{SLICE_A_FOSSILS}/ is absent"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / SLICE_A_FOSSILS).symlink_to(band)
            return self._differ("d2", "--reader", self.commit, "--fossils", tmp)

    def _load_lint(self) -> dict[str, CommandResult]:
        return {
            "ruff check": _run([PY, "-m", "ruff", "check", *LINT_PATHS]),
            "ruff format --check": _run([PY, "-m", "ruff", "format", "--check", *LINT_PATHS]),
            "mypy": _run([PY, "-m", "mypy"]),
        }

    def _load_review(self) -> dict[str, Any] | None:
        from tests.proof import reviews as reviews_mod

        path = reviews_mod.REVIEWS_DIR / REVIEW_FILE
        if not path.exists():
            return None
        try:
            record = reviews_mod.load(path)
        except reviews_mod.ReviewSchemaError as exc:
            return {"record": None, "error": str(exc)}
        shown = {f"review:{r['id']}" for r in reviews_mod.load_valid() if r["outcome"] == "pass"}
        return {
            "record": record,
            "error": None,
            "ancestor": fence_mod.is_ancestor(ROOT, record["sha"], self.commit),
            "shown": f"review:{REVIEW}" in shown,
        }


_WORLDS: dict[str, LiveWorld] = {}


def make_world(commit: str) -> World | LiveWorld:
    """The world every condition reads; the self-test replaces this seam with a synthetic one."""
    if commit not in _WORLDS:
        _WORLDS[commit] = LiveWorld(commit)
    return _WORLDS[commit]


def _condition(cond_id: str, verdict: Callable[[Any], tuple[bool, str]]) -> ckpt_mod.Condition:
    def check(commit: str) -> tuple[bool, str]:
        try:
            return verdict(make_world(commit))
        except Exception as exc:  # a condition that cannot be read is not passing
            return False, f"cannot evaluate: {type(exc).__name__}: {exc}"

    return ckpt_mod.Condition(cond_id, check)


VERDICTS: dict[str, Callable[[Any], tuple[bool, str]]] = {
    "S0": verdict_s0,
    "S2": verdict_s2,
    "S3": verdict_s3,
    "S4": verdict_s4,
    "S5": verdict_s5,
    "S6": verdict_s6,
    "S7": verdict_s7,
    "S8": verdict_s8,
    "S9": verdict_s9,
    "S10": verdict_s10,
    "S11": verdict_s11,
    "S12": verdict_s12,
    "S13": verdict_s13,
    "S14": verdict_s14,
}

CONDITIONS = [_condition(f"J-SLICE-A-{sid}", verdict) for sid, verdict in VERDICTS.items()]

assert [c.id for c in CONDITIONS] == [
    f"J-SLICE-A-S{n}" for n in (0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
]

__all__ = ["TRIGGER_MERGE", "TAG", "CONDITIONS", "World", "make_world", "LiveWorld", "VERDICTS"]
