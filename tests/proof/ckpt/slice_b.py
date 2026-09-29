"""`ckpt slice-b`: the J-SLICE-B condition module (CM-5, MC-28; L.RB-0.5, completed by L.RB-12.4).

`TRIGGER_MERGE = "J-SLICE-B"`, `TAG = "wr-ckpt/slice-b"`. The MC-28 framework evaluates it only on
the newest commit carrying `WR-Merge: J-SLICE-B` (DM-11); `--preview` evaluates the PR head and
`--dry` lists every unmet reason on any commit. The module is named neither `test_*.py` nor
`*_test.py`: its live conditions run only under `meta ckpt slice-b` (DM-80); its default-collected
self-test (`tests/proof/b/test_ckpt_slice_b.py`) plants synthetic worlds and never runs a live
condition. The module is finished in product merges (here and in L.RB-12.4) because the J-SLICE-B
lane's globs cover fossils and reviews only (CM-5), and a later edit would make its records
inadmissible (CM-6).

The conditions are plans/b-slice-b.md `### J-SLICE-B` / MJ.J-SLICE-B step 5, (a)-(h) here and (i),
the column-B condition, added by L.RB-12.4:

  (a) every one of the 25 Slice B clauses (one condition per clause id, so `--dry` lists each) and
      every label declared for step B (one condition) is PROVEN@HOST, PROVEN@CI, STUB-PROVEN or
      registered as declared (`gated_on`, `na(reason)`); a DOCKER-tier clause or label counts only
      through an admissible host-docker record (CSC-9)
  (b) every record used is admissible for the anchor (CM-6)
  (c) the role-2 pair (host-proc + its same-sha host-docker) is the one `record.select` and
      `record.paired_docker` return at the anchor; `record.pass_set_violations` is empty for both;
      the host-docker record has `diff.unattributed == []`, `engine_state_changed == false`, status
      PASSED, and its results include every docker_host node and the legacy live-compose node.
      L.RB-12.4 routes the pair through L.RB-12.7's `record_facts.py`.
  (d) every docker_host node has its twin (MC-B-03) and no twin registers a matrix clause
  (e) every label ending `stub_labels.toml`'s `twin_suffix` is declared `stub_proven`; every other
      `stub_proven` label has its MC-B-05 row; B4.5 and B9.1 carry no STUB label (DM-29)
  (f) every deferral whose `closes_at` lies in B4 has its label PROVEN at the candidate (CM-8)
  (g) `differ d8` shows no divergence
  (h) the gated registrations are present: OPEN-MISE-HOST, OQ-25, OQ-26 x2, F-12, F-B3-2, F-13(d)

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
from typing import Any

from tests.proof import ckpt as ckpt_mod
from tests.proof import fence as fence_mod
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


def _record_hit(record: dict[str, Any] | None, key: str) -> tuple[bool, bool]:
    """(the record's results name `key`, every such result PASSED)."""
    if record is None:
        return False, False
    hits = [r for r in record.get("results", []) if key in (r.get("labels") or [])]
    return bool(hits), bool(hits) and all(r.get("outcome") == "PASSED" for r in hits)


# ---------------------------------------------------------------------------
# (a) every clause and label proven, stub-proven or declared
# ---------------------------------------------------------------------------


def _evidence(w: World, key: str, tiers: list[Any], venues: list[Any], stub: bool) -> str | None:
    """The satisfied status of a ledger key the nodes registering it declare `tiers`/`venues` for,
    or a problem string. A DOCKER-tier key counts only through an admissible host-docker record."""
    if not _proven(w, key):
        return f"{key} is not PROVEN in the ledger"
    if any(_is_docker(t) for t in tiers):
        has, passed = _record_hit(w.host_docker, key)
        if w.host_docker is None:
            return f"{key} is DOCKER-tier: no admissible host-docker record (CI-only evidence)"
        if not has or not passed:
            return f"{key} is DOCKER-tier: not PASSED in the admissible host-docker record"
        return PROVEN_HOST
    if any(v in ("HOST", "BOTH") for v in venues):
        has, passed = _record_hit(w.host_proc, key)
        if w.host_proc is None:
            return f"{key} is venue HOST: no admissible host-proc record"
        if not has or not passed:
            return f"{key} is venue HOST: not PASSED in the admissible host-proc record"
        return PROVEN_HOST
    return STUB_PROVEN if stub else PROVEN_CI


def label_status(w: World, label: dict[str, Any]) -> tuple[str | None, str | None]:
    """(status, problem) of one declared label: exactly one of them is `None`."""
    label_id = str(label["id"])
    posture = label.get("posture")
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


def clause_status(w: World, clause_id: str) -> tuple[str | None, str | None]:
    """(status, problem) of one clause: its ledger key, through the tier and venue of the labels
    on the nodes that register it (a stub label makes it STUB-PROVEN)."""
    by_id = label_index(w)
    registrants = [n for n in w.nodes if clause_id in n["labels"]]
    node_labels = [by_id[lb] for n in registrants for lb in n["labels"] if lb in by_id]
    stub_labels = [lb for lb in node_labels if lb.get("posture") == "stub_proven"]
    clause = next((c for c in w.clauses if str(c["id"]) == clause_id), {})
    if clause.get("stub_label_required") and not stub_labels and _proven(w, clause_id):
        return None, f"{clause_id} requires a STUB label and no registering node carries one"
    result = _evidence(
        w,
        clause_id,
        [lb.get("tier") for lb in node_labels],
        [lb.get("venue") for lb in node_labels],
        bool(stub_labels),
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

    problems = []
    if w.host_proc is None:
        problems.append(
            "no admissible host-proc record at the anchor: a record is stale once any non-record "
            "path changed after its sha (CM-6)"
        )
    if w.host_docker is None:
        problems.append("no host-docker record at the host-proc record's sha (one role-2 run)")
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
                problems.append(f"host-docker results omit {nodeid}")
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

    def _load_host_proc(self) -> dict[str, Any] | None:
        from tests.proof.host import record as record_mod

        return record_mod.select("host-proc", self.commit, cwd=ROOT)

    def _load_host_docker(self) -> dict[str, Any] | None:
        from tests.proof.host import record as record_mod

        proc = self.host_proc
        return None if proc is None else record_mod.paired_docker(proc, cwd=ROOT)

    def _load_admissible(self) -> Callable[[dict[str, Any]], tuple[bool, str | None]]:
        from tests.proof.host import record as record_mod

        return lambda record: record_mod.is_admissible(record, self.commit, ROOT)

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
]

__all__ = ["TRIGGER_MERGE", "TAG", "CONDITIONS", "World", "make_world", "LiveWorld"]
