"""`ckpt slice-b`: the J-SLICE-B condition module (CM-5, MC-28; L.RB-0.5, completed by L.RB-12.4).

`TRIGGER_MERGE = "J-SLICE-B"`, `TAG = "wr-ckpt/slice-b"`. The MC-28 framework evaluates it only on
the newest commit carrying `WR-Merge: J-SLICE-B` (DM-11); `--preview` evaluates the PR head and
`--dry` lists every unmet reason on any commit. The module is named neither `test_*.py` nor
`*_test.py`: its live conditions run only under `meta ckpt slice-b` (DM-80); its default-collected
self-test (`tests/proof/b/test_ckpt_slice_b.py`) plants synthetic worlds and never runs a live
condition. The module is finished in product merges (here and in L.RB-12.4) because the J-SLICE-B
lane's globs cover fossils and reviews only (CM-5), and a later edit would make its records
inadmissible (CM-6).

The conditions are plans/b-slice-b.md `### J-SLICE-B` / MJ.J-SLICE-B step 5, (a)-(i); L.RB-12.4
completes the module (the role-2 pair through L.RB-12.7's resolver, the part-marker check of (a),
and the column-B condition (i)):

  (a) every one of the 25 Slice B clauses (one condition per clause id, so `--dry` lists each) and
      every label declared for step B (one condition) is PROVEN@HOST, PROVEN@CI, STUB-PROVEN or
      registered as declared (`gated_on`, `na(reason)`); a DOCKER-tier clause or label counts only
      through an admissible host-docker record (CSC-9). A key's venue is its labels' and its
      `proves()` markers'; a HOST key (DOCKER tier, a docker_host registrant, or venue HOST) is
      PROVEN@HOST through the admissible role-2 record alone, since CI deselects its nodes and the
      ledger the ckpt job renders holds only the CI shards' results; a BOTH key needs both halves
      (the ledger and the record), a CI key the ledger (the slice-a `key_problems` rule, the
      J-SINGLE venue-BOTH rule). Band preflight (J-SINGLE-R2 rule 2): a
      clause is judged only when a collected, non-twin node names the part id itself in a
      `proves()` marker (a row label that composes it does not count), so a missing marker is
      listed by `--dry` before any carrier is spent
  (b) every record used is admissible for the anchor (CM-6)
  (c) the role-2 pair (host-proc + its same-sha host-docker) is the one L.RB-12.7's
      `record_facts.py` resolves through `record.select` and `record.paired_docker` at the anchor A
      (the trigger commit when the ckpt job evaluates it; the PR head H under `--preview` and
      `--dry`, which select the same pair: the merge commit's tree is H's and adds no record, B6-3),
      so a re-run's pair supersedes an older attempt's and a triage or per-merge record is never
      read;
      `record.pass_set_violations` is empty for both; the host-docker record has
      `diff.unattributed == []`, `engine_state_changed == false`, status PASSED, and its results
      include every docker_host node and the legacy live-compose node
  (d) every docker_host node has its twin (MC-B-03) and no twin registers a matrix clause
  (e) every label ending `stub_labels.toml`'s `twin_suffix` is declared `stub_proven`; every other
      `stub_proven` label has its MC-B-05 row; B4.5 and B9.1 carry no STUB label (DM-29)
  (f) every deferral whose `closes_at` lies in B4 has its label PROVEN at the candidate (CM-8);
      G-E2's `WR-PROOF-2:pack-docker-live` (closes at NW-2) is proven where the plan puts its
      evidence, the legacy live-compose node PASSED in the admissible host-docker record
      (`DOCKER_EVIDENCED`), never the CI ledger, where that node's skip is UNPROVEN by design
  (g) `differ d8` shows no divergence
  (h) the gated registrations are present: OPEN-MISE-HOST, OQ-25, OQ-26 x2, F-12, F-B3-2, F-13(d)
  (i) column B of WR-PROOF-1, evaluated here over the MC-02 ledger and the admissible host records
      (the column enforce form is on CSC-5's withdrawn list): every Slice B cell B1-B9 is green,
      each of its clauses PROVEN@HOST or PROVEN@CI or STUB-PROVEN with its label, and a
      DOCKER-tier clause counts only through an admissible host-docker record

Every condition is a pure function of a `World` (plain data). `LiveWorld` fills the same attributes
lazily from the checkout; the self-test builds synthetic `World`s.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from tests.proof import ckpt as ckpt_mod
from tests.proof import fence as fence_mod
from tests.proof.b import record_facts as record_facts_mod
from tests.proof.b import stub_labels as stub_labels_mod
from tests.proof.b import twin_audit as twin_audit_mod

TRIGGER_MERGE = "J-SLICE-B"
TAG = "wr-ckpt/slice-b"

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable
PROVEN = "PROVEN"

# how a clause or label is satisfied (the ledger's status is PROVEN or UNPROVEN only)
PROVEN_HOST = "PROVEN@HOST"
PROVEN_CI = "PROVEN@CI"
STUB_PROVEN = "STUB-PROVEN"
DECLARED = "declared"
SATISFIED = (PROVEN_HOST, PROVEN_CI, STUB_PROVEN, DECLARED)

# B4's merges (plans/b-slice-b.md merge order): a deferral closing in one is B's to prove (f)
B4_MERGES = frozenset(
    {"NW-1", "NW-2", "NW-3"} | {f"RB-{n}" for n in range(0, 14)}
)  # fmt: skip
DEFERRAL_LABELS = (
    "WR-CANCEL-5:adapter-contract-suite",
    "WR-OWN-1:created-container-released",
    "WR-OWN-6:docker-inventory",
    "WR-OWN-3:created-container-stopped-on-success",
)
# CM-6's role-2 pass set names this unmarked legacy node beside every docker_host node
LEGACY_LIVE_NODE = (
    "packages/trestle-packs/tests/test_docker_integration.py::test_stack_runner_live_compose"
)
# (f): a label the plan evidences only through one node of the host-docker record (G-E2; L.NW-2.8,
# L.J-SLICE-B.2: "the results naming both are the evidence for WR-PROOF-2:pack-docker-live"). P0's
# compat map registers the label on the unmarked legacy node; CI collects that node (it is not a
# docker_host node, MC-B-03) and its skip there renders the label UNPROVEN by design (P0's G-E2
# pin, `test_alpine_skip_renders_unproven`), so the CI ledger can never prove it. The docker gate
# runs the node, where a skip is FAILED: the label is PROVEN@HOST when that node's result in the
# admissible host-docker record PASSED and carries the label.
DOCKER_EVIDENCED = {"WR-PROOF-2:pack-docker-live": LEGACY_LIVE_NODE}
# (h): the gated registrations, each id with the number of step-B labels bound to it (`oq`)
GATED_REGISTRATIONS = {
    "OPEN-MISE-HOST": 1,
    "OQ-25": 1,
    "OQ-26": 2,
    "F-12": 1,
    "F-B3-2": 1,
    "F-13(d)": 1,
}
B_STEP = "B"
SLICE_B_CLAUSE_COUNT = 25


def slice_b_clause_ids(clauses: list[dict[str, Any]]) -> list[str]:
    return sorted(
        str(c["id"]) for c in clauses if c.get("step") == B_STEP and str(c["id"]).startswith("B")
    )


# ---------------------------------------------------------------------------
# the world: every input a condition reads
# ---------------------------------------------------------------------------


@dataclass
class World:
    """Plain data: the self-test builds these; `LiveWorld` fills the same names lazily."""

    report: dict[str, dict[str, Any]] = field(default_factory=dict)  # the rendered MC-02 ledger
    labels: list[dict[str, Any]] = field(default_factory=list)  # labels.d/*.toml (CSC-1)
    clauses: list[dict[str, Any]] = field(default_factory=list)  # matrix_map.toml (MC-03)
    deferrals: list[dict[str, Any]] = field(default_factory=list)  # deferrals.toml (CM-8)
    nodes: list[dict[str, Any]] = field(default_factory=list)  # collected: nodeid, labels, markers
    host_proc: dict[str, Any] | None = None  # the role-2 host-proc record at the anchor
    host_docker: dict[str, Any] | None = None  # its same-sha host-docker record
    anchor: str | None = None  # the commit those records were resolved at (H, or the tag commit)
    admissible: Callable[[dict[str, Any]], tuple[bool, str | None]] = (
        lambda record: (True, None)  # noqa: E731
    )
    stub: stub_labels_mod.StubLabels = field(default_factory=stub_labels_mod.StubLabels)
    landed: set[str] | None = None  # leaf ids in the history; None = every row required
    d8: tuple[int, str] = (0, "")
    open_question_ids: set[str] = field(default_factory=set)


def _fail(problems: list[str], limit: int = 6) -> tuple[bool, str]:
    if not problems:
        return True, ""
    shown = problems[:limit]
    more = f" (+{len(problems) - limit} more)" if len(problems) > limit else ""
    return False, "; ".join(shown) + more


def label_index(w: World) -> dict[str, dict[str, Any]]:
    return {str(lb["id"]): lb for lb in w.labels}


def b_labels(w: World) -> list[dict[str, Any]]:
    """The labels Slice B declares: `step = "B"` in labels.d (twins and the four core deferrals
    included; the five P0 labels are P0's, declared in its own fragments)."""
    return [lb for lb in w.labels if lb.get("step") == B_STEP]


def _is_docker(tier: Any) -> bool:
    return "DOCKER" in str(tier).upper().replace("+", " ").split()


def _proven(w: World, key: str) -> bool:
    return w.report.get(key, {}).get("status") == PROVEN


HOST_DOCKER = "host-docker"
HOST_PROC = "host-proc"


def _record_hit(record: dict[str, Any] | None, key: str) -> tuple[bool, bool]:
    """(the record's results name `key`, every such result PASSED)."""
    if record is None:
        return False, False
    hits = [r for r in record.get("results", []) if key in (r.get("labels") or [])]
    return bool(hits), bool(hits) and all(r.get("outcome") == "PASSED" for r in hits)


# ---------------------------------------------------------------------------
# (a) every clause and label proven, stub-proven or declared
# ---------------------------------------------------------------------------


def _host_half(w: World, key: str, gate: str) -> str | None:
    """`None` when `key` is PASSED in the role-2 `gate` record (results name it and every one of
    them PASSED) and that record is admissible for the anchor (CM-6), else the problem."""
    record = w.host_docker if gate == HOST_DOCKER else w.host_proc
    what = "DOCKER-tier" if gate == HOST_DOCKER else "venue HOST"
    if record is None:
        tail = " (CI-only evidence)" if gate == HOST_DOCKER else ""
        return f"{key} is {what}: no admissible {gate} record{tail}"
    ok, why = w.admissible(record)
    if not ok:
        detail = f": {why}" if why else ""
        return (
            f"{key} is {what}: no admissible {gate} record (record {str(record.get('sha'))[:12]} "
            f"is not admissible for the anchor{detail}, CM-6)"
        )
    has, passed = _record_hit(record, key)
    if not has or not passed:
        return f"{key} is {what}: not PASSED in the admissible {gate} record"
    return None


def _evidence(
    w: World,
    key: str,
    tiers: list[Any],
    venues: list[Any],
    stub: bool,
    docker_node: bool = False,
) -> str | None:
    """The satisfied status of a ledger key the labels and markers registering it declare `tiers` /
    `venues` for (`docker_node`: a docker_host node registers it), or a problem string.

    * DOCKER (a DOCKER tier, or a docker_host registrant): PASSED in the admissible host-docker
      record (CSC-9);
    * venue HOST: PASSED in the admissible host-proc record;
    * venue BOTH: both halves, PROVEN in the ledger and PASSED in the admissible host record;
    * otherwise (venue CI): PROVEN in the ledger.

    A HOST half never reads the ledger: CI deselects docker_host and host_only nodes (CSC-9) and the
    ckpt job renders the ledger from the CI shards' results only, so the committed role-2 record is
    the one place a HOST result exists (slice-a's `key_problems`, the J-SINGLE venue-BOTH rule). A
    record's results are those of its own gate run; an inadmissible record is no evidence."""
    docker = docker_node or any(_is_docker(t) for t in tiers)
    host = docker or any(v in ("HOST", "BOTH") for v in venues)
    ci = not host or any(v in ("CI", "BOTH") for v in venues)
    if ci and not _proven(w, key):
        return f"{key} is not PROVEN in the ledger"
    if host:
        return _host_half(w, key, HOST_DOCKER if docker else HOST_PROC) or PROVEN_HOST
    return STUB_PROVEN if stub else PROVEN_CI


def _docker_node_evidence(w: World, label_id: str, nodeid: str) -> str | None:
    """`None` when `nodeid` PASSED, carrying `label_id`, in the admissible host-docker record and
    every other result naming the label PASSED too, else the problem (`DOCKER_EVIDENCED`)."""
    problem = _host_half(w, label_id, HOST_DOCKER)
    if problem:
        return problem
    assert w.host_docker is not None  # `_host_half` found it
    if not any(
        r.get("nodeid") == nodeid
        and r.get("outcome") == "PASSED"
        and label_id in (r.get("labels") or [])
        for r in w.host_docker.get("results", [])
    ):
        return f"{label_id}: {nodeid} is not PASSED with the label in the host-docker record"
    return None


def label_status(w: World, label: dict[str, Any]) -> tuple[str | None, str | None]:
    """(status, problem) of one declared label: exactly one of them is `None`."""
    label_id = str(label["id"])
    posture = label.get("posture")
    if label_id in DOCKER_EVIDENCED and posture not in ("gated_on", "na"):
        problem = _docker_node_evidence(w, label_id, DOCKER_EVIDENCED[label_id])
        return (None, problem) if problem else (PROVEN_HOST, None)
    if posture == "gated_on":
        if not str(label.get("oq", "")).strip():
            return None, f"{label_id} is gated_on with no open question"
        return DECLARED, None
    if posture == "na":
        if not str(label.get("reason", "")).strip():
            return None, f"{label_id} is na with no reason"
        return DECLARED, None
    result = _evidence(
        w, label_id, [label.get("tier")], [label.get("venue")], posture == "stub_proven"
    )
    if result in SATISFIED:
        return result, None
    return None, result


def is_twin_node(node: dict[str, Any], suffix: str) -> bool:
    """A CI twin (MC-B-03): under a `twin/` directory or registering a `<label><twin_suffix>`. A
    twin never carries a matrix part (CSC-8, `twin_audit`), so it never counts as naming one."""
    return "/twin/" in str(node["nodeid"]) or any(
        str(label).endswith(suffix) for label in node["labels"]
    )


def part_registrants(w: World, clause_id: str) -> list[dict[str, Any]]:
    """The collected non-twin nodes whose `proves()` markers name the part id itself. The audit
    plugin resolves a matrix id used as a marker label into the node's labels; a row label that only
    `composes` the part is not the part id (J-SINGLE-R2: a part no marker names has no ledger key at
    all, and is found only after the carrier is spent)."""
    suffix = w.stub.twin_suffix
    return [n for n in w.nodes if clause_id in n["labels"] and not is_twin_node(n, suffix)]


def clause_status(w: World, clause_id: str) -> tuple[str | None, str | None]:
    """(status, problem) of one clause: through the tier and venue of the labels on the nodes that
    register it and of their markers naming it, and whether a docker_host node registers it (a stub
    label makes a CI-only clause STUB-PROVEN)."""
    by_id = label_index(w)
    registrants = part_registrants(w, clause_id)
    if not registrants:
        return None, (
            f"{clause_id}: no collected non-twin node names it in a proves() marker "
            "(a row label that composes it does not count)"
        )
    node_labels = [by_id[lb] for n in registrants for lb in n["labels"] if lb in by_id]
    stub_labels = [lb for lb in node_labels if lb.get("posture") == "stub_proven"]
    clause = next((c for c in w.clauses if str(c["id"]) == clause_id), {})
    if clause.get("stub_label_required") and not stub_labels and _proven(w, clause_id):
        return None, f"{clause_id} requires a STUB label and no registering node carries one"
    marks = [m for n in registrants for m in (n.get("marked") or {}).get(clause_id, [])]
    result = _evidence(
        w,
        clause_id,
        [lb.get("tier") for lb in node_labels] + [m[0] for m in marks],
        [lb.get("venue") for lb in node_labels] + [m[1] for m in marks],
        bool(stub_labels),
        any(n.get("docker_host") for n in registrants),
    )
    if result in SATISFIED:
        return result, None
    return None, result


def verdict_a_clause(w: World, clause_id: str) -> tuple[bool, str]:
    ids = slice_b_clause_ids(w.clauses)
    if clause_id not in ids:
        return False, f"{clause_id} is not a Slice B clause of the matrix map"
    _status, problem = clause_status(w, clause_id)
    return _fail([problem] if problem else [])


def verdict_a_labels(w: World) -> tuple[bool, str]:
    declared = b_labels(w)
    if not declared:
        return False, "no step-B label is declared (labels.d/slice-b.toml is absent or empty)"
    problems = []
    for label in declared:
        _status, problem = label_status(w, label)
        if problem:
            problems.append(problem)
    return _fail(problems)


def render_status(w: World, key: str) -> str:
    """The status a clause or label renders under this module: a satisfied one names how, else
    UNPROVEN (`--dry` lists the reason)."""
    label = label_index(w).get(key)
    status, _problem = (
        label_status(w, label) if label is not None else clause_status(w, key)
    )  # fmt: skip
    return status or "UNPROVEN"


# ---------------------------------------------------------------------------
# (b) every record used is admissible
# ---------------------------------------------------------------------------


def _used(w: World) -> list[tuple[str, dict[str, Any]]]:
    return [(g, r) for g, r in (("host-proc", w.host_proc), ("host-docker", w.host_docker)) if r]


def verdict_b(w: World) -> tuple[bool, str]:
    problems = []
    for gate, record in _used(w):
        ok, why = w.admissible(record)
        if not ok:
            detail = f": {why}" if why else ""
            problems.append(
                f"{gate} record {str(record.get('sha'))[:12]} is not admissible for the anchor "
                f"(its sha is not an ancestor, or a non-record path changed since){detail} (CM-6)"
            )
    return _fail(problems)


# ---------------------------------------------------------------------------
# (c) the role-2 pair
# ---------------------------------------------------------------------------


def docker_host_nodeids(w: World) -> list[str]:
    return sorted(n["nodeid"] for n in w.nodes if n.get("docker_host"))


def verdict_c(w: World) -> tuple[bool, str]:
    from tests.proof.host import record as record_mod

    where = f" (anchor {str(w.anchor)[:12]})" if getattr(w, "anchor", None) else ""
    problems = []
    if w.host_proc is None:
        problems.append(
            "no role-2 host-proc record resolves at the anchor: the record `select` returns there "
            "is not a J-SLICE-B run record (a triage, catch-up or per-merge record is never read), "
            "is stale once any non-record path changed after its sha (CM-6), or there is none and "
            f"no wr-ckpt/slice-b tag{where}"
        )
    if w.host_docker is None:
        problems.append(
            f"no host-docker run record at the host-proc record's sha (one role-2 run){where}"
        )
    labels = label_index(w)
    for gate, record in _used(w):
        for violation in record_mod.pass_set_violations(record, labels):
            problems.append(f"{gate} pass set: {violation}")
    docker = w.host_docker
    if docker is not None:
        if docker.get("status") != "PASSED":
            problems.append(f"host-docker record status is {docker.get('status')!r}, not PASSED")
        diff = docker.get("diff") or {}
        if diff.get("unattributed") != []:
            problems.append(
                f"host-docker diff.unattributed is {diff.get('unattributed')!r}, not []"
            )
        if diff.get("engine_state_changed") is not False:
            problems.append("host-docker diff.engine_state_changed is not false (WR-CON-3)")
        recorded = {r.get("nodeid") for r in docker.get("results", [])}
        for nodeid in [*docker_host_nodeids(w), LEGACY_LIVE_NODE]:
            if nodeid not in recorded:
                problems.append(f"host-docker results omit {nodeid}{where}")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (d) twins, (e) stub labels, (f) deferrals, (g) d8, (h) gated registrations
# ---------------------------------------------------------------------------


def verdict_d(w: World) -> tuple[bool, str]:
    return _fail(twin_audit_mod.twin_problems(w.nodes, label_index(w), w.stub.twin_suffix))


def verdict_e(w: World) -> tuple[bool, str]:
    problems = stub_labels_mod.problems(w.stub, w.labels, w.landed)
    by_id = label_index(w)
    seen: set[str] = set()
    for node in w.nodes:
        for label in node["labels"]:
            if label.endswith(w.stub.twin_suffix) and label not in seen:
                seen.add(label)
                if by_id.get(label, {}).get("posture") != "stub_proven":
                    problems.append(f"twin label {label} is not declared stub_proven (CSC-8)")
    problems += [
        f"twin label {lb['id']} is not declared stub_proven (CSC-8)"
        for lb in w.labels
        if str(lb["id"]).endswith(w.stub.twin_suffix) and lb.get("posture") != "stub_proven"
    ]
    problems += twin_audit_mod.adversary_problems(
        w.nodes, by_id, w.stub.twin_suffix, stub_labels_mod.NO_STUB_CLAUSES
    )
    return _fail(problems)


def verdict_f(w: World) -> tuple[bool, str]:
    by_id = label_index(w)
    problems = []
    for entry in w.deferrals:
        if entry.get("closes_at") not in B4_MERGES:
            continue
        label = entry["label"]
        declared = by_id.get(label)
        if declared is None:
            problems.append(f"{label}: closes_at={entry['closes_at']!r} but is not declared")
            continue
        status, problem = label_status(w, declared)
        if status not in (PROVEN_HOST, PROVEN_CI, STUB_PROVEN):
            problems.append(
                f"{label}: closes_at={entry['closes_at']!r} but not PROVEN ({problem or status})"
            )
    return _fail(problems)


def verdict_g(w: World) -> tuple[bool, str]:
    if w.d8[0] != 0:
        return False, f"differ d8: exit {w.d8[0]}: {w.d8[1][-200:]}"
    return True, ""


def verdict_h(w: World) -> tuple[bool, str]:
    problems = []
    bound: dict[str, int] = {}
    for label in b_labels(w):
        oq = label.get("oq")
        if oq:
            bound[str(oq)] = bound.get(str(oq), 0) + 1
    for oq, want in GATED_REGISTRATIONS.items():
        if oq not in w.open_question_ids:
            problems.append(f"{oq} is not in open_questions.toml")
        if bound.get(oq, 0) < want:
            problems.append(f"{oq}: {bound.get(oq, 0)} step-B label(s) bound, expected {want}")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (i) column B of WR-PROOF-1
# ---------------------------------------------------------------------------


class _Overlay:
    """A world with some attributes replaced (a `World` and a `LiveWorld` alike)."""

    def __init__(self, base: Any, **override: Any) -> None:
        self._base = base
        self._override = override

    def __getattr__(self, name: str) -> Any:
        if name in self._override:
            return self._override[name]
        return getattr(self._base, name)


def column_b_cells(clauses: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Cell (`one_terminal_answer:B`) -> the ids of its Slice B clauses, in matrix-map order: every
    cell of column B of the guarantee x slice matrix, whether or not it holds a clause."""
    cells: dict[str, list[str]] = {}
    for clause in clauses:
        cell = str(clause.get("cell", ""))
        if cell.endswith(":A"):
            cells.setdefault(cell[:-2] + ":B", [])
        elif cell.endswith(":B"):
            cells.setdefault(cell, []).append(str(clause["id"]))
    return cells


def verdict_i(w: Any) -> tuple[bool, str]:
    """Column B (WR-PROOF-1): every cell B1-B9 is green. A clause counts as `clause_status` reads
    it (PROVEN@HOST, PROVEN@CI, or STUB-PROVEN through its label), over the admissible host records
    only: a record that CM-6 makes inadmissible for the anchor is no evidence, so a DOCKER-tier
    clause resting on it reads as CI-only. A cell with no clause is not green: the matrix map
    carries no `n/a` reason for a Slice B cell."""

    def usable(record: dict[str, Any] | None) -> dict[str, Any] | None:
        return record if record is not None and w.admissible(record)[0] else None

    view = cast(
        World, _Overlay(w, host_proc=usable(w.host_proc), host_docker=usable(w.host_docker))
    )
    cells = column_b_cells(w.clauses)
    if not cells:
        return False, "the matrix map has no clause of cells B1..B9"
    problems = []
    for cell, ids in cells.items():
        if not ids:
            problems.append(f"{cell}: no Slice B clause, and the matrix map gives no n/a reason")
        for clause_id in ids:
            _status, problem = clause_status(view, clause_id)
            if problem:
                problems.append(f"{cell}: {problem}")
    return _fail(problems)


# ---------------------------------------------------------------------------
# the live world
# ---------------------------------------------------------------------------


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """One bounded subprocess in the repository root; a timeout is a failed run (exit 124)."""
    try:
        return subprocess.run(
            argv, cwd=ROOT, capture_output=True, text=True, timeout=fence_mod.HOST_RUN_MAX
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "timed out")


class LiveWorld:
    """The same attributes as `World`, each read from the checkout the first time it is used.

    The role-2 pair is the one `record_facts.resolve` returns at `commit` (L.RB-12.7): the trigger
    commit when the ckpt job evaluates, the PR head under `--preview` and `--dry`."""

    def __init__(self, commit: str, root: Path | None = None) -> None:
        self.commit = commit
        self.root = root or ROOT

    def __getattr__(self, name: str) -> Any:
        loader = getattr(type(self), f"_load_{name}", None)
        if loader is None:
            raise AttributeError(name)
        value = loader(self)
        setattr(self, name, value)
        return value

    def _load_report(self) -> dict[str, dict[str, Any]]:
        from tests.proof import ledger as ledger_mod

        try:
            return ledger_mod.render()
        except ledger_mod.VacuousLedgerError:
            return {}

    def _load_labels(self) -> list[dict[str, Any]]:
        from tests.proof import meta as meta_mod

        return list(meta_mod._load_all_labels())  # noqa: SLF001

    def _load_clauses(self) -> list[dict[str, Any]]:
        from tests.proof import transcribe as transcribe_mod

        return list(transcribe_mod.load_matrix_map())

    def _load_deferrals(self) -> list[dict[str, Any]]:
        from tests.proof import deferrals as deferrals_mod

        return deferrals_mod.load_deferrals()

    def _load_nodes(self) -> list[dict[str, Any]]:
        return twin_audit_mod.collect_nodes()

    def _load_pair(self) -> record_facts_mod.Pair | None:
        return record_facts_mod.resolve(self.commit, self.root)

    def _load_anchor(self) -> str:
        pair = self.pair
        return self.commit if pair is None else pair.anchor

    def _load_host_proc(self) -> dict[str, Any] | None:
        pair = self.pair
        return None if pair is None else pair.host_proc

    def _load_host_docker(self) -> dict[str, Any] | None:
        pair = self.pair
        return None if pair is None else pair.host_docker

    def _load_admissible(self) -> Callable[[dict[str, Any]], tuple[bool, str | None]]:
        from tests.proof.host import record as record_mod

        return lambda record: record_mod.is_admissible(record, self.anchor, self.root)

    def _load_stub(self) -> stub_labels_mod.StubLabels:
        return stub_labels_mod.load()

    def _load_landed(self) -> set[str] | None:
        return stub_labels_mod.landed_leaves(ROOT)

    def _load_d8(self) -> tuple[int, str]:
        proc = _run([PY, "-m", "tests.proof.differ", "d8"])
        return proc.returncode, proc.stdout + proc.stderr

    def _load_open_question_ids(self) -> set[str]:
        from tests.proof import meta as meta_mod

        return {str(o["id"]) for o in meta_mod.load_open_questions()}


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


def _clause_ids() -> list[str]:
    data = tomllib.loads((ROOT / "tests" / "proof" / "matrix_map.toml").read_text())
    return slice_b_clause_ids(list(data.get("clause", [])))


B_CLAUSE_IDS = _clause_ids()
assert len(B_CLAUSE_IDS) == SLICE_B_CLAUSE_COUNT, B_CLAUSE_IDS


def _clause_condition(clause_id: str) -> ckpt_mod.Condition:
    return _condition(f"J-SLICE-B-a:{clause_id}", lambda w: verdict_a_clause(w, clause_id))


CONDITIONS = [
    *(_clause_condition(clause_id) for clause_id in B_CLAUSE_IDS),
    _condition("J-SLICE-B-a:labels", verdict_a_labels),
    _condition("J-SLICE-B-b", verdict_b),
    _condition("J-SLICE-B-c", verdict_c),
    _condition("J-SLICE-B-d", verdict_d),
    _condition("J-SLICE-B-e", verdict_e),
    _condition("J-SLICE-B-f", verdict_f),
    _condition("J-SLICE-B-g", verdict_g),
    _condition("J-SLICE-B-h", verdict_h),
    _condition("J-SLICE-B-i", verdict_i),
]

__all__ = ["TRIGGER_MERGE", "TAG", "CONDITIONS", "World", "make_world", "LiveWorld"]
