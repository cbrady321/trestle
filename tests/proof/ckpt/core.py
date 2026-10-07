"""`ckpt core`: the J-CORE condition module (CM-5, MC-28; L.CL-D1.4).

`TRIGGER_MERGE = "J-CORE"`, `TAG = "wr-ckpt/core"`. The MC-28 framework evaluates it only on the
newest commit carrying `WR-Merge: J-CORE` (CM-5, DM-11); `--preview` evaluates the PR head, and
every condition here reads only what a PR head already holds, so `--preview` covers all eleven
(none is `merge_only`). The module is named neither `test_*.py` nor `*_test.py`: its live
conditions run only under `meta ckpt core` (DM-80); its default-collected self-test
(`tests/core/docs/test_cl_d1_ckpt.py`) plants synthetic worlds and never runs a live condition.

The conditions are plans/core.md `### J-CORE`, (a)-(k):

  (a) the 14 core clause parts PROVEN@CI through a named gate, and the venue-BOTH clauses and
      labels also PROVEN@HOST from the host-proc record `record.select` picks (CM-6)
  (b) every one of the 49 core-tagged rows has a claimed core clause or label, or is P0-owned
  (c) CM-8's band rule at the candidate: cited deferrals; core-band closings PROVEN; every core
      label PROVEN or deferred
  (d) no core P0 target is still xfail; each passes under `--runxfail`
  (e) CSC-14, mechanically: the straddle node set S registers only the one claim label
  (f) `meta register` is clean; TM-C1..3 present, T-1/T-2/T-3/TM-P0-1/TM-P0-12 absent
  (g) the TM-C4b variant is present as strict xfail (TM-C4a, the K-1 variant, left with
      JOIN_ACROSS_REPUBLISH in v0.4)
  (h) cell A9 is fully green and claimed
  (i) RV-1, RV-3, RV-4 and RV-5 core review records pass and are shown by the ledger
  (j) the core fossils are MANIFEST-complete; d2 (reader s0) has 0 diffs; d1 --strict has none
      unexpected; open questions pass
  (k) the declined-variant path: an item is declined only when its MC-CORE-12 switch holds the
      declined value on HEAD, and then renders `na("K-n declined")`, never PROVEN

Every condition is a pure function of a `World` (plain data). `LiveWorld` fills the same
attributes lazily from the checkout; the self-test builds synthetic `World`s.
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

TRIGGER_MERGE = "J-CORE"
TAG = "wr-ckpt/core"

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable
PROVEN = "PROVEN"

# (a) the 14 core clause parts (plans/core.md ### (b)); the four venue-BOTH ones
CORE_PARTS = [
    "A1.1:core",
    "A1.3:core",
    "A1.4",
    "A2.2",
    "A3.1",
    "A5.1",
    "A5.2",
    "A6.1:core",
    "A6.2:core",
    "A8.2",
    "A9.1",
    "A9.2",
    "A9.3",
    "A9.4",
]
BOTH_PARTS = ["A5.1", "A6.1:core", "A6.2:core", "A8.2"]
NAMED_GATES = ("ci-test", "ci-spine", "ci-long")  # CSC-13

# (b) the 49 core-tagged rows (plans/core.md ## Coverage (a)): 40 owned by this phase, 9 by P0
CORE_ROWS = [
    "WR-TERM-2", "WR-TERM-3", "WR-TERM-5", "WR-TERM-7",
    "WR-PLAN-4", "WR-PLAN-5", "WR-PLAN-6", "WR-PLAN-7", "WR-PLAN-8", "WR-PLAN-9",
    "WR-IDEM-1", "WR-IDEM-2", "WR-DEADLINE-1", "WR-DEADLINE-2",
    "WR-CANCEL-1", "WR-CANCEL-2", "WR-CANCEL-3", "WR-CANCEL-5", "WR-CANCEL-6",
    "WR-OWN-1", "WR-OWN-3", "WR-OWN-5", "WR-OWN-6", "WR-OWN-8",
    "WR-EVID-1", "WR-EVID-2", "WR-EVID-4", "WR-EVID-5", "WR-EVID-6", "WR-EVID-7",
    "WR-EVID-8", "WR-EVID-9", "WR-EVID-11",
    "WR-AUTH-1", "WR-AUTH-2", "WR-AUTH-4", "WR-AUTH-5",
    "WR-PROOF-8", "WR-PROOF-10", "WR-CON-6",
]  # fmt: skip
P0_OWNED_ROWS = [
    "WR-PROOF-2", "WR-PROOF-3", "WR-PROOF-5", "WR-PROOF-7", "WR-PROOF-9",
    "WR-CON-1", "WR-CON-2", "WR-CON-4", "WR-CON-5",
]  # fmt: skip
P0_OWNER_NODE = "wr-proof"

# (d) the P0 gaps this phase flips
CORE_GAPS = [f"G-{lane}{n}" for lane, count in (("A", 4), ("B", 5), ("C", 4), ("D", 5))
             for n in range(1, count + 1)]  # fmt: skip

# (e) CSC-14
CLAIM_LABEL = "WR-CANCEL-3:s0-straddle-stop-unconfirmed-never-clean"
WITHDRAWN_LABEL = "WR-CANCEL-3:process-alive-after-recovery-s0-straddle"
STRADDLE_CALLS = {"spawn_s0_shaped_orphan", "STRADDLE_HOME"}
STRADDLE_FIXTURE = "fossils/s0/straddle"

# (f) the register (CM-7)
REGISTER_PRESENT = {"TM-C1": "L.SV-3.6", "TM-C2": "L.SV-3.6", "TM-C3": "L.TR-6.8"}
REGISTER_ABSENT = ["T-1", "T-2", "T-3", "TM-P0-1", "TM-P0-12"]
# under a decline the entry it restores is present again, named-not-removed
RESTORED_BY = {"T-3": "CK-3/4", "TM-P0-1": "CK-14"}
NAMED_NOT_REMOVED = "named-not-removed"

# (g) the written variants (C.9): merge whose decline relaxes it -> (label, entry)
VARIANTS = {
    "TM-C4b": ("WR-PLAN-9:variant-refuse-written", "CK-3/4"),
}

# (i) the reviews this checkpoint needs (RV-5 at every checkpoint)
REVIEWS = ["RV-1", "RV-3", "RV-4", "RV-5"]

# (k) the CK items; a decline the P0 target of which J-CORE (d) excuses
CK_MERGES = ["CK-8", "CK-14", "CK-1", "CK-3/4"]
DECLINE_GAPS = {"CK-1": {"G-C3"}, "CK-3/4": {"G-B2"}}
DECLINED_REASON = "{k} declined"

FOSSILS_ROOT = ROOT / "tests" / "fixtures" / "fossils"
CORE_FOSSILS = FOSSILS_ROOT / "core"


# ---------------------------------------------------------------------------
# the world: every input a condition reads
# ---------------------------------------------------------------------------


@dataclass
class World:
    """Plain data: the self-test builds these; `LiveWorld` fills the same names lazily."""

    report: dict[str, dict[str, Any]] = field(default_factory=dict)  # the rendered MC-02 ledger
    records: list[dict[str, Any]] = field(default_factory=list)  # results/*.jsonl records
    labels: list[dict[str, Any]] = field(default_factory=list)  # labels.d/*.toml (CSC-1)
    clauses: list[dict[str, Any]] = field(default_factory=list)  # matrix_map.toml (MC-03)
    row_owners: dict[str, str] = field(default_factory=dict)  # row id -> owner node (MC-04)
    deferrals: list[dict[str, Any]] = field(default_factory=list)  # deferrals.toml (CM-8)
    core_merges: set[str] = field(default_factory=set)  # the band's merge ids
    declines: dict[str, dict[str, Any]] = field(default_factory=dict)  # ck_drill.DECLINES
    declined: set[str] = field(default_factory=set)  # CK merges whose switch is declined on HEAD
    host_record: dict[str, Any] | None = None  # record.select("host-proc", HEAD)
    target_audit: dict[str, Any] = field(default_factory=dict)  # `-m target --runxfail` audit
    gap_entries: dict[str, str] = field(default_factory=dict)  # core gap -> entry outcome
    nodes: list[dict[str, Any]] = field(default_factory=list)  # every collected node
    straddle_nodeids: set[str] = field(default_factory=set)  # the node set S (e)
    run_nodes: Callable[[list[str]], dict[str, str]] = lambda ids: {}  # noqa: E731
    register: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> entry + `present`
    register_violations: list[str] = field(default_factory=list)  # `meta register`'s rule
    reviews: dict[str, dict[str, Any]] = field(default_factory=dict)  # RV id -> record/error
    fossils_missing: list[str] = field(default_factory=list)  # (j) MANIFEST-completeness
    d1: tuple[int, str] = (0, "")
    d2: tuple[int, str] = (0, "")
    open_questions: tuple[int, str] = (0, "")
    open_question_ids: set[str] = field(default_factory=set)
    divergence: list[dict[str, Any]] = field(default_factory=list)  # divergence.toml entries


def clause_parts(clause: dict[str, Any]) -> list[str]:
    """A matrix clause's ledger keys: its id, or `<id>:<part>` for each part (MC-03)."""
    if "parts" in clause:
        return [f"{clause['id']}:{part['name']}" for part in clause["parts"]]
    return [str(clause["id"])]


@dataclass
class Excused:
    """What the declined CK items (k) take out of (a), (b), (c), (d), (f) and (j)."""

    labels: set[str] = field(default_factory=set)
    clauses: set[str] = field(default_factory=set)
    gaps: set[str] = field(default_factory=set)
    ks: set[str] = field(default_factory=set)

    @property
    def ids(self) -> set[str]:
        return self.labels | self.clauses


def excused(w: World) -> Excused:
    out = Excused()
    for merge in sorted(w.declined):
        decline = w.declines.get(merge)
        if decline is None:
            continue
        out.labels |= set(decline.get("labels", []))
        out.clauses |= set(decline.get("clauses", []))
        out.gaps |= DECLINE_GAPS.get(merge, set())
        k = decline["k"]
        out.ks |= {k} if isinstance(k, str) else set(k)
    return out


def _proven(w: World, key: str) -> bool:
    return w.report.get(key, {}).get("status") == PROVEN


def _fail(problems: list[str], limit: int = 6) -> tuple[bool, str]:
    if not problems:
        return True, ""
    shown = problems[:limit]
    more = f" (+{len(problems) - limit} more)" if len(problems) > limit else ""
    return False, "; ".join(shown) + more


# ---------------------------------------------------------------------------
# (a) core clause parts PROVEN@CI through a named gate, BOTH also PROVEN@HOST
# ---------------------------------------------------------------------------


def _named_gate_pass(records: list[dict[str, Any]], key: str) -> bool:
    return any(
        key in (r.get("labels") or [])
        and r.get("gate") in NAMED_GATES
        and r.get("venue") == "CI"
        and r.get("outcome") == "passed"
        and str(r.get("interpreter", "")).startswith("3.12")
        for r in records
    )


def _host_proven(record: dict[str, Any] | None, key: str) -> bool:
    if record is None:
        return False
    hits = [r for r in record.get("results", []) if key in (r.get("labels") or [])]
    return bool(hits) and all(r.get("outcome") == "PASSED" for r in hits)


def core_both_labels(w: World) -> list[str]:
    """Every venue-BOTH core label that is a claim (a `na`/`shape` label is no claim)."""
    return sorted(
        lb["id"]
        for lb in w.labels
        if lb.get("step") == "core" and lb.get("venue") == "BOTH" and lb.get("posture") == "claim"
    )


def verdict_a(w: World) -> tuple[bool, str]:
    ex = excused(w)
    problems: list[str] = []
    for part in CORE_PARTS:
        if part in ex.clauses:
            continue
        if not _proven(w, part):
            problems.append(f"{part} is not PROVEN in the ledger")
        elif not _named_gate_pass(w.records, part):
            problems.append(f"{part} is not PROVEN@CI through a named gate {NAMED_GATES}")
    both = [p for p in BOTH_PARTS if p not in ex.clauses]
    both += [lb for lb in core_both_labels(w) if lb not in ex.labels]
    if both:
        if w.host_record is None:
            problems.append(
                f"no admissible host-proc record for HEAD (venue-BOTH: {both[:3]}...): a record "
                "is stale once any non-record path changed after its sha (CM-6)"
            )
        else:
            unproven = [key for key in both if not _host_proven(w.host_record, key)]
            if unproven:
                problems.append(f"not PROVEN@HOST in the host-proc record: {unproven}")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (b) every core-tagged row has a claimed core clause or label
# ---------------------------------------------------------------------------


def row_claims(w: World, row: str) -> tuple[list[str], list[str]]:
    """(claimed, declared): the row's core claim keys seen in the rendered ledger, and every core
    claim key declared for it (labels.d claim/na labels; matrix clauses crediting the row)."""
    declared: list[str] = []
    for label in w.labels:
        if label.get("row") == row and label.get("step") == "core":
            if label.get("posture") in ("claim", "na"):
                declared.append(label["id"])
    for clause in w.clauses:
        if row in clause.get("rows", []):
            declared.extend(clause_parts(clause))
    claimed = [
        key
        for key in declared
        if key in w.report and not _na_label(w, key)
    ]  # fmt: skip
    return claimed, declared


def _na_label(w: World, key: str) -> bool:
    return any(lb["id"] == key and lb.get("posture") == "na" for lb in w.labels)


def verdict_b(w: World) -> tuple[bool, str]:
    ex = excused(w)
    problems = []
    for row in CORE_ROWS + P0_OWNED_ROWS:
        claimed, declared = row_claims(w, row)
        if claimed:
            continue
        if row in P0_OWNED_ROWS and w.row_owners.get(row) == P0_OWNER_NODE:
            continue
        if declared and all(key in ex.ids for key in declared):
            continue  # its only claims are items a declined CK item renders na (k)
        problems.append(f"core row {row} has no claimed core clause or label")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (c) CM-8's band rule at the candidate
# ---------------------------------------------------------------------------


def verdict_c(w: World) -> tuple[bool, str]:
    from tests.proof import deferrals as deferrals_mod

    ex = excused(w)
    problems = []
    for entry in w.deferrals:
        if not str(entry.get("citation", "")).strip():
            problems.append(f"deferral {entry.get('label')}: no citation")
    proven = {key for key in w.report if _proven(w, key)}
    problems += deferrals_mod.band_rule_violations(w.deferrals, w.core_merges, proven)
    deferred = {e["label"] for e in w.deferrals if str(e.get("citation", "")).strip()}
    for label in w.labels:
        if label.get("step") != "core" or label.get("posture") not in ("claim", "deferred"):
            continue
        lid = label["id"]
        if lid in ex.labels or lid in proven or lid in deferred:
            continue
        problems.append(f"core label {lid} is neither PROVEN nor deferred with a citation")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (d) no core P0 target still xfail; each passes under --runxfail
# ---------------------------------------------------------------------------


def verdict_d(w: World) -> tuple[bool, str]:
    ex = excused(w)
    problems = []
    outcomes = w.target_audit.get("outcomes", {})
    for node in w.target_audit.get("nodes", []):
        gap = node.get("gap")
        if gap not in CORE_GAPS or gap in ex.gaps:
            continue
        got = outcomes.get(node["nodeid"], {}).get("outcome")
        if node.get("strict_xfail"):
            problems.append(f"{gap}: {node['nodeid']} is still a strict xfail target")
        elif got != "passed":
            problems.append(f"{gap}: {node['nodeid']} did not pass under --runxfail ({got})")
    for gap, outcome in sorted(w.gap_entries.items()):
        if gap in CORE_GAPS and gap not in ex.gaps and outcome != "passed":
            problems.append(f"{gap}: its entry test is {outcome}, not passed under --runxfail")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (e) CSC-14, mechanically
# ---------------------------------------------------------------------------


def verdict_e(w: World) -> tuple[bool, str]:
    problems = []
    by_id = {n["nodeid"]: n for n in w.nodes}
    for nodeid in sorted(w.straddle_nodeids):
        labels = set(by_id.get(nodeid, {}).get("labels", []))
        extra = sorted(labels - {CLAIM_LABEL})
        if extra:
            problems.append(f"straddle node {nodeid} registers {extra}, not only {CLAIM_LABEL}")
    for node in w.nodes:
        if WITHDRAWN_LABEL in node.get("labels", []):
            problems.append(f"{node['nodeid']} registers the withdrawn label {WITHDRAWN_LABEL}")
    if not _proven(w, CLAIM_LABEL):
        problems.append(f"{CLAIM_LABEL} is not PROVEN")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (f) the register
# ---------------------------------------------------------------------------


def verdict_f(w: World) -> tuple[bool, str]:
    problems = list(w.register_violations)
    for entry_id, remover in REGISTER_PRESENT.items():
        entry = w.register.get(entry_id)
        if entry is None or not entry.get("present"):
            problems.append(f"{entry_id} is absent, expected present until {remover}")
        elif entry.get("removed_by") != remover:
            problems.append(f"{entry_id} removed_by {entry.get('removed_by')!r}, not {remover!r}")
    for entry_id in REGISTER_ABSENT:
        entry = w.register.get(entry_id)
        merge = RESTORED_BY.get(entry_id)
        if merge is not None and merge in w.declined:
            k = _primary_k(w.declines[merge])
            if entry is None or not entry.get("present"):
                problems.append(f"{entry_id} must be present again after the {k} decline")
            elif (
                entry.get("removed_by") != NAMED_NOT_REMOVED
                or entry.get("citation") != DECLINED_REASON.format(k=k)
                or entry.get("serves") != []
            ):
                problems.append(
                    f"{entry_id} is restored but not {NAMED_NOT_REMOVED} / "
                    f"'{DECLINED_REASON.format(k=k)}' / serves = []"
                )
        elif entry is not None and entry.get("present"):
            problems.append(
                f"{entry_id} is still present (its remover is {entry.get('removed_by')})"
            )
    return _fail(problems)


def _primary_k(decline: dict[str, Any]) -> str:
    k = decline["k"]
    return k if isinstance(k, str) else k[0]


# ---------------------------------------------------------------------------
# (g) the written variants
# ---------------------------------------------------------------------------


def verdict_g(w: World) -> tuple[bool, str]:
    problems = []
    for entry_id, (label, _merge) in VARIANTS.items():
        nodeids = [n["nodeid"] for n in w.nodes if label in n.get("labels", [])]
        if not nodeids:
            problems.append(f"{entry_id}: no node registers {label}")
            continue
        outcomes = w.run_nodes(nodeids)
        want = "xfailed"
        for nodeid in nodeids:
            got = outcomes.get(nodeid)
            if got != want:
                problems.append(f"{entry_id}: {nodeid} is {got}, expected {want} (strict xfail)")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (h) cell A9 fully green and claimed
# ---------------------------------------------------------------------------


def verdict_h(w: World) -> tuple[bool, str]:
    parts = [p for c in w.clauses if str(c["id"]).startswith("A9.") for p in clause_parts(c)]
    problems = [f"cell A9: {p} is not PROVEN" for p in parts if not _proven(w, p)]
    if not parts:
        problems.append("cell A9: no clause A9.* in the matrix map")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (i) the review records
# ---------------------------------------------------------------------------


def verdict_i(w: World) -> tuple[bool, str]:
    problems = []
    for rv in REVIEWS:
        info = w.reviews.get(rv)
        if info is None:
            problems.append(f"{rv}-core.toml is absent")
            continue
        if info.get("error"):
            problems.append(f"{rv}-core.toml: {info['error']}")
            continue
        record = info["record"]
        if record.get("outcome") != "pass":
            problems.append(f"{rv} outcome is {record.get('outcome')!r}, expected pass")
        elif not info.get("ancestor"):
            problems.append(f"{rv} reviewed sha {record.get('sha')} is not an ancestor of HEAD")
        elif not info.get("shown"):
            problems.append(f"the ledger does not show review:{rv}")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (j) fossils, d2, d1, open questions
# ---------------------------------------------------------------------------


def d1_unexpected(w: World, output: str) -> list[str]:
    """`differ d1 --strict` complaint lines that are not the K-n divergence of a declined item."""
    ex = excused(w)
    facet_ks: dict[str, set[str]] = {}
    for entry in w.divergence:
        facet_ks.setdefault(str(entry.get("facet")), set()).add(str(entry.get("row_or_k")))
    unexpected = []
    for line in output.splitlines():
        if not line.startswith("d1:"):
            continue
        facet = re.search(r"facet '([^']+)'", line)
        entry = re.search(r"divergence entry (\S+)", line)
        if entry and entry.group(1) in ex.ks:
            continue
        if facet and facet_ks.get(facet.group(1)) and facet_ks[facet.group(1)] <= ex.ks:
            continue
        unexpected.append(line)
    return unexpected


def verdict_j(w: World) -> tuple[bool, str]:
    problems = []
    if w.fossils_missing:
        problems.append(f"core fossils are not MANIFEST-complete: {w.fossils_missing[:4]}")
    if w.d2[0] != 0:
        problems.append(f"differ d2 --reader s0: exit {w.d2[0]}: {w.d2[1][-200:]}")
    if w.d1[0] != 0:
        # a complaint that is a declined item's K-n divergence is accepted (k)
        problems += [f"differ d1 --strict: {line}" for line in d1_unexpected(w, w.d1[1])]
    if w.open_questions[0] != 0:
        problems.append(f"meta open-questions: exit {w.open_questions[0]}")
    if "Q-STRADDLE-ORPHAN" in w.open_question_ids:
        problems.append("Q-STRADDLE-ORPHAN is answered and must not be listed")
    return _fail(problems)


# ---------------------------------------------------------------------------
# (k) the declined-variant path
# ---------------------------------------------------------------------------


def verdict_k(w: World) -> tuple[bool, str]:
    problems = []
    for merge in CK_MERGES:
        decline = w.declines.get(merge)
        if decline is None:
            problems.append(f"{merge}: no DECLINE entry (tests/core/tooling/declines)")
            continue
        k = _primary_k(decline)
        reason = DECLINED_REASON.format(k=k)
        labels = {lb["id"]: lb for lb in w.labels}
        if merge in w.declined:
            for label_id in decline.get("labels", []):
                label = labels.get(label_id)
                if label is None or label.get("posture") != "na" or label.get("reason") != reason:
                    problems.append(f"{merge}: {label_id} is not na('{reason}') on HEAD")
            for key in list(decline.get("labels", [])) + list(decline.get("clauses", [])):
                if _proven(w, key):
                    problems.append(f"{merge} is declined but {key} renders PROVEN, not na")
        else:
            for label_id in decline.get("labels", []):
                label = labels.get(label_id, {})
                if label.get("posture") == "na" and label.get("reason") == reason:
                    problems.append(
                        f"{label_id} is na('{reason}') but the {merge} decline patch is not on HEAD"
                    )
    return _fail(problems)


# ---------------------------------------------------------------------------
# the live world
# ---------------------------------------------------------------------------


def _run(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """One bounded subprocess in the repository root; a timeout is a failed run (exit 124)."""
    try:
        return subprocess.run(
            argv,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=fence_mod.HOST_RUN_MAX,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "timed out")


def _audit(pytest_args: list[str]) -> dict[str, Any]:
    """One pytest run under `audit_plugin`: its collected `nodes` and call `outcomes`."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "audit.json"
        env = dict(os.environ, TRESTLE_AUDIT_OUT=str(out))
        _run([PY, "-m", "pytest", "-q", "-p", "tests.proof.audit_plugin", *pytest_args], env)
        return json.loads(out.read_text()) if out.exists() else {"nodes": [], "outcomes": {}}


def switch_declined(decline: dict[str, Any], root: Path) -> bool:
    """MC-CORE-12: the switch of `decline` holds its declined value on `root`'s tree. A module
    constant is read from source; K-14's switch is the typecheck step's command."""
    switch = decline["switch"]
    if "module" in switch:
        base = switch["module"].replace(".", "/")
        for candidate in (root / f"{base}.py", root / base / "__init__.py"):
            if candidate.is_file():
                pattern = rf"^{re.escape(switch['name'])}(?:\s*:\s*[^=\n]+?)?\s*=\s*(\S+)"
                match = re.search(pattern, candidate.read_text(), re.M)
                return match is not None and match.group(1) == repr(switch["declined"])
        return False
    pair = switch["command"]
    ci = (root / ".github" / "workflows" / "ci.yml").read_text()

    def has(command: str) -> bool:
        return re.search(rf"^\s*(?:- )?run: {re.escape(command)}\s*$", ci, re.M) is not None

    return has(pair["to"]) and not has(pair["from"])


def straddle_nodeids(nodes: list[dict[str, Any]], root: Path) -> set[str]:
    """The node set S: nodes whose function body (AST) calls `spawn_s0_shaped_orphan` or reads
    the s0 straddle fixture."""
    found: set[str] = set()
    trees: dict[str, ast.Module | None] = {}
    for node in nodes:
        path, _, rest = node["nodeid"].partition("::")
        if path not in trees:
            file = root / path
            try:
                trees[path] = ast.parse(file.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                trees[path] = None
        tree = trees[path]
        if tree is None or not rest:
            continue
        func_name = rest.split("::")[-1].split("[")[0]
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef) and func.name == func_name:
                if _reads_straddle(func):
                    found.add(node["nodeid"])
    return found


def _reads_straddle(func: ast.AST) -> bool:
    for sub in ast.walk(func):
        if isinstance(sub, ast.Name) and sub.id in STRADDLE_CALLS:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in STRADDLE_CALLS:
            return True
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if STRADDLE_FIXTURE in sub.value or sub.value == "straddle":
                return True
    return False


def gap_entry_node_ids(entry: str, collected: list[str]) -> list[str]:
    """The collected node ids that stand for a gap's `entry` (gaps.toml, "module:function"):
    the test itself, or, when the entry is a helper its module's tests call (lane C, e.g.
    `test_g_c1:wait_at_budget`), every collected test of that module. A helper is no node id,
    and one unknown id makes pytest refuse the whole run, so only collected ids are returned."""
    module, _, func = entry.partition(":")
    path = f"{module.replace('.', '/')}.py"
    nodeid = f"{path}::{func}"
    if nodeid in collected or any(n.startswith(f"{nodeid}[") for n in collected):
        return [n for n in collected if n == nodeid or n.startswith(f"{nodeid}[")]
    return [n for n in collected if n.startswith(f"{path}::")]


def gap_entry_outcomes(
    gaps: list[dict[str, Any]],
    collected: list[str],
    audit: Callable[[list[str]], dict[str, Any]],
) -> dict[str, str]:
    """(d): each core gap's entry outcome under `--runxfail`: `passed` only when every node
    that stands for its entry passed; `no result` when none was collected."""
    targets: dict[str, list[str]] = {}
    for gap in gaps:
        entry = str(gap.get("entry", "pending"))
        if gap["id"] in CORE_GAPS and ":" in entry:
            targets[gap["id"]] = gap_entry_node_ids(entry, collected)
    run = sorted({n for ids in targets.values() for n in ids})
    outcomes = audit(["--runxfail", *run]).get("outcomes", {}) if run else {}
    result: dict[str, str] = {}
    for gap, ids in targets.items():
        got = [str(outcomes.get(n, {}).get("outcome", "no result")) for n in ids]
        if not got:
            result[gap] = "no result"
        elif all(g == "passed" for g in got):
            result[gap] = "passed"
        else:
            result[gap] = next(g for g in got if g != "passed")
    return result


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

    def _load_row_owners(self) -> dict[str, str]:
        from tests.proof import transcribe as transcribe_mod

        return {str(r["id"]): str(r["owner_node"]) for r in transcribe_mod.load_row_owners()}

    def _load_deferrals(self) -> list[dict[str, Any]]:
        from tests.proof import deferrals as deferrals_mod

        return deferrals_mod.load_deferrals()

    def _load_core_merges(self) -> set[str]:
        data = tomllib.loads((ROOT / "tests" / "proof" / "fence.d" / "core.toml").read_text())
        return {str(g["merge"]) for g in data.get("gate", [])}

    def _load_declines(self) -> dict[str, dict[str, Any]]:
        from tests.core.tooling import ck_drill

        return dict(ck_drill.DECLINES)

    def _load_declined(self) -> set[str]:
        return {m for m, d in self.declines.items() if switch_declined(d, ROOT)}

    def _load_host_record(self) -> dict[str, Any] | None:
        from tests.proof.host import record as record_mod

        return record_mod.select("host-proc", self.commit, cwd=ROOT)

    def _load_target_audit(self) -> dict[str, Any]:
        return _audit(["-m", "target", "--runxfail"])

    def _load_gap_entries(self) -> dict[str, str]:
        from tests.proof import meta as meta_mod

        gaps = meta_mod._load_inventories()[0]  # noqa: SLF001
        return gap_entry_outcomes(gaps, [n["nodeid"] for n in self.nodes], _audit)

    def _load_nodes(self) -> list[dict[str, Any]]:
        return list(_audit(["--collect-only"]).get("nodes", []))

    def _load_straddle_nodeids(self) -> set[str]:
        return straddle_nodeids(self.nodes, ROOT)

    def _load_run_nodes(self) -> Callable[[list[str]], dict[str, str]]:
        def run(nodeids: list[str]) -> dict[str, str]:
            outcomes = _audit(list(nodeids)).get("outcomes", {})
            return {n: str(outcomes.get(n, {}).get("outcome")) for n in nodeids}

        return run

    def _load_register(self) -> dict[str, dict[str, Any]]:
        from tests.proof import register as register_mod

        out: dict[str, dict[str, Any]] = {}
        for entry in register_mod.load_entries():
            out[entry["id"]] = dict(entry, present=register_mod.active_phase(entry) is not None)
        return out

    def _load_register_violations(self) -> list[str]:
        from tests.proof import register as register_mod

        return [
            f"{v} is claimed while a present entry serves it"
            for v in register_mod.register_violations()
        ]

    def _load_reviews(self) -> dict[str, dict[str, Any]]:
        from tests.proof import reviews as reviews_mod

        out: dict[str, dict[str, Any]] = {}
        shown = {f"review:{r['id']}" for r in reviews_mod.load_valid() if r["outcome"] == "pass"}
        for rv in REVIEWS:
            path = reviews_mod.REVIEWS_DIR / f"{rv}-core.toml"
            if not path.exists():
                continue
            try:
                record = reviews_mod.load(path)
            except reviews_mod.ReviewSchemaError as exc:
                out[rv] = {"record": None, "error": str(exc)}
                continue
            out[rv] = {
                "record": record,
                "error": None,
                "ancestor": fence_mod.is_ancestor(ROOT, record["sha"], self.commit),
                "shown": f"review:{rv}" in shown,
            }
        return out

    def _load_fossils_missing(self) -> list[str]:
        from tests.proof import fossils as fossils_mod

        if not (CORE_FOSSILS / "MANIFEST.toml").exists():
            return ["tests/fixtures/fossils/core/MANIFEST.toml"]
        missing = []
        for sid, (band, entry) in fossils_mod.load_states(FOSSILS_ROOT).items():
            if entry.get("producer") in (None, "pending") or entry.get("absent"):
                continue
            if not (FOSSILS_ROOT / band / sid / "home").is_dir():
                missing.append(f"{band}/{sid}")
        return missing

    def _load_d1(self) -> tuple[int, str]:
        proc = _run([PY, "-m", "tests.proof.differ", "d1", "--strict"])
        return proc.returncode, proc.stdout + proc.stderr

    def _load_d2(self) -> tuple[int, str]:
        proc = _run([PY, "-m", "tests.proof.differ", "d2", "--reader", "s0"])
        return proc.returncode, proc.stdout + proc.stderr

    def _load_open_questions(self) -> tuple[int, str]:
        proc = _run([PY, "-m", "tests.proof.meta", "open-questions"])
        return proc.returncode, proc.stdout + proc.stderr

    def _load_open_question_ids(self) -> set[str]:
        from tests.proof import meta as meta_mod

        return {str(o["id"]) for o in meta_mod.load_open_questions()}

    def _load_divergence(self) -> list[dict[str, Any]]:
        from tests.proof import differ as differ_mod

        return differ_mod.load_divergence()


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


CONDITIONS = [
    _condition("J-CORE-a", verdict_a),
    _condition("J-CORE-b", verdict_b),
    _condition("J-CORE-c", verdict_c),
    _condition("J-CORE-d", verdict_d),
    _condition("J-CORE-e", verdict_e),
    _condition("J-CORE-f", verdict_f),
    _condition("J-CORE-g", verdict_g),
    _condition("J-CORE-h", verdict_h),
    _condition("J-CORE-i", verdict_i),
    _condition("J-CORE-j", verdict_j),
    _condition("J-CORE-k", verdict_k),
]

assert [c.id for c in CONDITIONS] == [f"J-CORE-{x}" for x in "abcdefghijk"]

__all__ = ["TRIGGER_MERGE", "TAG", "CONDITIONS", "World", "make_world", "LiveWorld"]
