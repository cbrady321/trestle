"""CLI entry point for proof-court reports (CSC-5).

`L.P0-0a.1` builds only the `baseline` subcommand. Later leaves
(`L.P0-0a.3`, `L.P0-0a.4`, `L.P0-0a.6`) extend this module with
`report`, `enforce`, `inventory`, `tolerances --list-s0-literal-sites`
style neighbors, and `mypy-ratchet`. The closure phase (`L.CZ.1`-`L.CZ.7`) turns the
report-mode checks into enforcing ones: `enforce --scope`, `audit-rows --enforce`,
`kdoc --enforce` and `open-questions --final`.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "tests" / "proof" / "baseline.json"
NODEIDS_PATH = ROOT / "tests" / "fixtures" / "golden" / "s0" / "nodeids.txt"
GAPS_PATH = ROOT / "tests" / "proof" / "gaps.toml"
FACETS_PATH = ROOT / "tests" / "proof" / "facets.toml"
FOSSIL_MANIFEST_PATH = ROOT / "tests" / "fixtures" / "fossils" / "s0" / "MANIFEST.toml"

BASELINE_FIELDS = [
    "root_passed",
    "root_failed",
    "packs_passed",
    "packs_skipped",
    "ruff_check",
    "ruff_format",
    "mypy_errors",
]


def _run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env=env)


def _pytest_counts(extra_args: list[str], env: dict[str, str] | None = None) -> tuple[int, int]:
    """Return (passed, skipped) parsed from a `pytest -q` summary line."""
    proc = _run([sys.executable, "-m", "pytest", "-q", *extra_args], env=env)
    output = proc.stdout + proc.stderr
    passed_match = re.search(r"(\d+) passed", output)
    skipped_match = re.search(r"(\d+) skipped", output)
    passed = int(passed_match.group(1)) if passed_match else 0
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    return passed, skipped


def measure_current() -> dict[str, object]:
    """Recompute pass/skip counts, ruff and mypy status for this interpreter.

    `root_passed`/`root_failed` count only the frozen S0 corpus
    (`--ignore=tests/proof`): tests/proof/** itself grows a new selftest
    module per leaf, and `python -m tests.proof.meta baseline` is one of
    the tests a full `pytest -q` run would collect, so counting it in
    would both double-count and self-invoke without bound. This is a
    deliberate delivery-time reading of "root_passed" as "the S0 count
    stays stable"; recorded as a plan-gap (p0-court.md L250 does not spell
    out the exclusion).
    """
    # `packages/trestle-packs/tests` is in `testpaths` from L.P0-0a.5 on, so
    # a bare `pytest -q` collects both; exclude it here to keep
    # `root_passed` meaning only the non-packs S0 corpus (measured
    # separately below), matching EV-01's separate root/packs fields.
    root_passed, _root_skipped = _pytest_counts(
        [
            "--ignore=tests/proof",
            "--ignore=tests/pins",
            # delivery-phase test trees (core, then A-1/A-2) are not the S0 corpus
            "--ignore=tests/core",
            "--ignore=tests/spine",
            "--ignore=tests/single",
            "--ignore=tests/tree",
            "--ignore-glob=packages/trestle-packs/*",
            # L.RB-0.1: the env tests are in `testpaths` too and are not the S0 corpus
            "--ignore-glob=packages/trestle-env/*",
        ]
    )
    # The packs half is run with the worktree's own trestle_packs source
    # prepended on PYTHONPATH. In CI, `pip install -e ".[dev,packs]"` makes
    # this a no-op (the editable install already resolves there first); on
    # a host whose ambient site-packages holds a stale non-editable
    # trestle_packs (this host, per L.P0-0a.1 current_behavior), the bare
    # `-c pyproject.toml --rootdir .` form alone still resolves the stale
    # copy and errors on collection, so this delivery adds the PYTHONPATH
    # prepend to make the measurement meaningful on both venues.
    packs_env = dict(os.environ)
    packs_src = str(ROOT / "packages" / "trestle-packs")
    packs_env["PYTHONPATH"] = packs_src + (
        (":" + packs_env["PYTHONPATH"]) if packs_env.get("PYTHONPATH") else ""
    )
    packs_passed, packs_skipped = _pytest_counts(
        ["-c", "pyproject.toml", "--rootdir", ".", "packages/trestle-packs/tests"],
        env=packs_env,
    )
    ruff_check = _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "trestle",
            "tests",
            "packages/trestle-packs",
            "scripts/smoke_packs.py",
            "scripts/demo_pack_workflows.py",
        ]
    )
    ruff_format = _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "trestle",
            "tests",
            "packages/trestle-packs",
            "scripts/smoke_packs.py",
            "scripts/demo_pack_workflows.py",
        ]
    )
    mypy = _run([sys.executable, "-m", "mypy", "trestle"])
    mypy_match = re.search(r"Found (\d+) error", mypy.stdout)
    mypy_errors = int(mypy_match.group(1)) if mypy_match else 0
    return {
        "root_passed": root_passed,
        "root_failed": 0,
        "packs_passed": packs_passed,
        "packs_skipped": packs_skipped,
        "ruff_check": "clean" if ruff_check.returncode == 0 else "dirty",
        "ruff_format": "clean" if ruff_format.returncode == 0 else "dirty",
        "mypy_errors": mypy_errors,
    }


def _explained_fields(explanations: list[dict[str, str]]) -> set[str]:
    covered: set[str] = set()
    for entry in explanations:
        for name in entry.get("field", "").split(","):
            covered.add(name.strip())
    return covered


def cmd_baseline(_args: argparse.Namespace) -> int:
    data = json.loads(BASELINE_PATH.read_text())
    ev01 = data["ev01"]
    ci = data.get("ci_312", {})
    explanations = ci.get("explanations", [])
    explained = _explained_fields(explanations)
    current = measure_current()

    ok = True
    for name in BASELINE_FIELDS:
        if current[name] != ev01[name] and name not in explained:
            ok = False
            print(f"UNEXPLAINED DIFF: {name}: ev01={ev01[name]!r} current={current[name]!r}")

    if not NODEIDS_PATH.exists():
        print(f"missing {NODEIDS_PATH}")
        return 1
    ids = [line for line in NODEIDS_PATH.read_text().splitlines() if line.strip()]
    if len(set(ids)) != 187:
        ok = False
        print(f"nodeids.txt has {len(set(ids))} unique ids, expected 187")

    return 0 if ok else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Render the derived proof ledger (MC-02): text, or `--json`."""
    from tests.proof import ledger as ledger_mod

    try:
        report = ledger_mod.render()
    except ledger_mod.VacuousLedgerError as exc:
        print(f"error: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0

    for clause in sorted(report):
        entry = report[clause]
        corroborating = " (corroborating 3.14 pass)" if entry["corroborating_314"] else ""
        print(f"{clause}: {entry['status']}{corroborating} [{entry['n_results']} result(s)]")
    return 0


# ---------------------------------------------------------------------------
# enforce (L.CZ.1): WR-PROOF-1 as the permanent post-delivery check
# ---------------------------------------------------------------------------

ENFORCE_SCOPES = ("ci", "checkpoint")
PROVEN = "PROVEN"
_MATRIX_ID_KEY = re.compile(r"^[AB]\d+\.\d+")


@dataclass
class EnforceWorld:
    """Everything `meta enforce` judges, as plain data: the self-tests build these; `live_world`
    fills the same names from the checkout. Nothing here reads a CI artifact: `report` is the
    current run's own MC-02 ledger, and the HOST records are committed files that
    `record.select` / `record.paired_docker` pick at `anchor` (CM-6, PC5-4)."""

    scope: str
    report: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: list[dict[str, Any]] = field(default_factory=list)
    clauses: list[dict[str, Any]] = field(default_factory=list)
    # matrix clause/part key -> the (tier, venue) each registering `proves()` marker declares
    markers: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    anchor: str | None = None
    host_proc: dict[str, Any] | None = None
    host_docker: dict[str, Any] | None = None
    admissible: Callable[[dict[str, Any]], tuple[bool, str | None]] = (
        lambda record: (True, None)  # noqa: E731
    )
    pass_set: Callable[[dict[str, Any]], list[str]] = lambda record: []  # noqa: E731


def _tier_tokens(tier: object) -> list[str]:
    return str(tier).upper().replace("+", " ").split()


def clause_keys(clauses: list[dict[str, Any]]) -> list[str]:
    """Every ledger key of the matrix: a clause id, or `<clause>:<part>` for each part of a
    clause that declares parts (`transcribe.matrix_ids` minus the parts' parents)."""
    keys: list[str] = []
    for clause in clauses:
        parts = clause.get("parts") or []
        if parts:
            keys += [f"{clause['id']}:{part['name']}" for part in parts]
        else:
            keys.append(str(clause["id"]))
    return keys


def scan_proves_markers(roots: list[Path]) -> dict[str, list[tuple[str, str]]]:
    """`proves(row, clause, slice, step, tier, venue)` markers naming a matrix clause, read from
    the source (nothing is imported, collected or run): clause key -> [(tier, venue), ...]."""
    found: dict[str, list[tuple[str, str]]] = {}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or len(node.args) < 6:
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name != "proves":
                    continue
                values = [a.value if isinstance(a, ast.Constant) else None for a in node.args[:6]]
                clause, tier, venue = values[1], values[4], values[5]
                if not (
                    isinstance(clause, str) and isinstance(tier, str) and isinstance(venue, str)
                ):
                    continue
                if _MATRIX_ID_KEY.match(clause.split(":", 1)[0]):
                    pair = (tier, venue)
                    if pair not in found.setdefault(clause, []):
                        found[clause].append(pair)
    return found


def _record_hits(record: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if record is None:
        return []
    return [r for r in record.get("results", []) if key in (r.get("labels") or [])]


def key_problems(w: EnforceWorld, key: str, pairs: list[tuple[str, str]]) -> list[str]:
    """One clause or label key at the (tier, venue) pairs its nodes declare. A CI or BOTH venue is
    PROVEN in the current run's ledger; a HOST or BOTH venue is PASSED in the selected host-proc
    record; a DOCKER tier is PASSED in the paired host-docker record. A HOST clause's status is
    re-rendered from the record and never from a result of this run (R2-5, R3-3)."""
    need_ledger = need_proc = need_docker = False
    for tier, venue in pairs:
        if "DOCKER" in _tier_tokens(tier):
            need_docker = True
        elif venue == "HOST":
            need_proc = True
        elif venue == "BOTH":
            need_ledger = need_proc = True
        else:
            need_ledger = True
    problems: list[str] = []
    if need_ledger and w.report.get(key, {}).get("status") != PROVEN:
        problems.append(f"{key} is not PROVEN in this run's ledger")
    for need, record, gate in (
        (need_proc, w.host_proc, "host-proc"),
        (need_docker, w.host_docker, "host-docker"),
    ):
        if not need:
            continue
        if record is None:
            problems.append(
                f"{key}: no admissible {gate} record at anchor {(w.anchor or 'none')[:12]}"
            )
            continue
        hits = _record_hits(record, key)
        if not hits and gate == "host-proc":
            # a docker_host node runs only in the docker gate (MC-B-03), so its HOST result is in
            # the paired host-docker record (as slice_b.py's clause_status reads it)
            hits = _record_hits(w.host_docker, key)
        if not hits or any(r.get("outcome") != "PASSED" for r in hits):
            problems.append(f"{key} is not PASSED in the {gate} record {str(record['sha'])[:12]}")
    return problems


def label_problems(w: EnforceWorld, label: dict[str, Any]) -> list[str]:
    """A label is green when its tier and venue are proven as `key_problems` reads them, or is
    declared: `gated_on` with an open question, `both_variant`, or `na` with a reason (C.9). A
    `shape` label is shape evidence and never a claim, so it needs no result (L.CZ.1.fix2)."""
    label_id = str(label["id"])
    posture = label.get("posture")
    if posture == "gated_on":
        return [] if str(label.get("oq", "")).strip() else [f"{label_id} is gated_on with no oq"]
    if posture == "both_variant":
        return []
    if posture == "na":
        return [] if str(label.get("reason", "")).strip() else [f"{label_id} is na with no reason"]
    if posture == "shape":  # shape evidence never counts as a claim (MC-04, CSC-1)
        return []
    pairs = [(str(label.get("tier")), str(label.get("venue")))]
    return key_problems(w, label_id, _docker_evidenced(label_id, pairs))


def _docker_evidenced(key: str, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """A key the plan evidences only through the host-docker record (slice_b.py's
    `DOCKER_EVIDENCED`: G-E2's `WR-PROOF-2:pack-docker-live`, whose CI skip renders UNPROVEN by
    design) is read at the DOCKER tier, never from the CI ledger."""
    from tests.proof.ckpt import slice_b as slice_b_mod

    return [("DOCKER", "HOST")] if key in slice_b_mod.DOCKER_EVIDENCED else pairs


def clause_problems(w: EnforceWorld, key: str) -> list[str]:
    pairs = w.markers.get(key)
    if not pairs:
        # registered through the compat map (no proves() marker in source): CI when it ran at all
        if key not in w.report:
            return [f"{key} has no registering node"]
        pairs = [("LOGIC", "CI")]
    return key_problems(w, key, _docker_evidenced(key, pairs))


def enforce_problems(w: EnforceWorld) -> list[str]:
    """WR-PROOF-1 over the whole declared universe: every matrix clause (part) and every CSC-1
    label green or declared (gated, both-variant, na). Empty means the check passes."""
    problems: list[str] = []
    for key in clause_keys(w.clauses):
        problems += clause_problems(w, key)
    for label in w.labels:
        problems += label_problems(w, label)
    if w.scope == "checkpoint":
        for gate, record in (("host-proc", w.host_proc), ("host-docker", w.host_docker)):
            if record is None:
                problems.append(f"no admissible {gate} record at the candidate (CM-6)")
                continue
            ok, why = w.admissible(record)
            if not ok:
                problems.append(f"{gate} record {str(record['sha'])[:12]} is not admissible: {why}")
            problems += [f"{gate} pass set: {v}" for v in w.pass_set(record)]
    return sorted(set(problems))


def scope_ci_anchor(
    ref: str = "HEAD", cwd: Path | None = None, check_run_reader: Any = None
) -> str | None:
    """CM-6's `--scope ci` anchor: the newest `WR-Merge: J-<NAME>` commit on `ref`'s first-parent
    history, of any checkpoint, that carries its success mark (`fence.ckpt_succeeded`, CM-5). A
    newer carrier whose evaluation failed is skipped, so the anchor is the last checkpoint that
    passed. `None` before any checkpoint has."""
    from tests.proof import fence as fence_mod
    from tests.proof import trailers as trailers_mod

    cwd = cwd or ROOT
    carriers = [
        (commit.sha, merge_id)
        for commit in trailers_mod._commits(ref, cwd)  # noqa: SLF001
        for kind, merge_id in commit.trailers()
        if kind == "WR-Merge" and merge_id in fence_mod.CKPT_SUCCESS_MARK
    ]
    for sha, merge_id in reversed(carriers):
        if fence_mod.ckpt_succeeded(merge_id, sha, cwd=cwd, check_run_reader=check_run_reader):
            return sha
    return None


def host_evidence(
    scope: str,
    *,
    cwd: Path | None = None,
    commit: str = "HEAD",
    check_run_reader: Any = None,
) -> dict[str, Any]:
    """The anchor and the HOST records of an enforce world, all through `tests/proof/host/record.py`
    (this module implements no selection): `ci` selects at the last successful checkpoint's
    carrier, `checkpoint` at the candidate `commit`."""
    from tests.proof import fence as fence_mod
    from tests.proof.host import record as record_mod

    cwd = cwd or ROOT
    if scope == "ci":
        anchor = scope_ci_anchor(commit, cwd, check_run_reader)
    else:
        anchor = fence_mod._git(cwd, "rev-parse", commit).stdout.strip()  # noqa: SLF001
    if anchor is None:
        return {"anchor": None, "host_proc": None, "host_docker": None}
    proc = record_mod.select("host-proc", anchor, cwd=cwd)
    docker = None if proc is None else record_mod.paired_docker(proc, cwd=cwd)
    labels = {str(lb["id"]): lb for lb in _load_all_labels()}
    return {
        "anchor": anchor,
        "host_proc": proc,
        "host_docker": docker,
        "admissible": lambda record: record_mod.is_admissible(record, anchor, cwd),
        "pass_set": lambda record: record_mod.pass_set_violations(record, labels),
    }


def live_world(scope: str, commit: str = "HEAD") -> EnforceWorld:
    from tests.proof import ledger as ledger_mod
    from tests.proof import transcribe as transcribe_mod

    report = ledger_mod.render()
    roots = [ROOT / "tests", *sorted((ROOT / "packages").glob("*/tests"))]
    return EnforceWorld(
        scope=scope,
        report=report,
        labels=_load_all_labels(),
        clauses=list(transcribe_mod.load_matrix_map()),
        markers=scan_proves_markers(roots),
        **host_evidence(scope, commit=commit),
    )


def cmd_enforce(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta enforce --scope ci|checkpoint` (L.CZ.1, CSC-5), the permanent
    post-delivery WR-PROOF-1 check: exit 1 on any clause or label neither green nor declared.
    `--scope ci` (the `proof-ledger` job, every PR and push) reads this run's CI results and
    re-renders HOST, BOTH and DOCKER statuses from the committed records at the last successful
    checkpoint; `--scope checkpoint` (the `ckpt` job, J-ROOT) reads the candidate's records and
    requires them admissible. `--print-mode` prints `enforce` and exits 0 (TM-P0-6's probe)."""
    if args.print_mode:
        print("enforce")
        return 0
    if args.scope is None:
        print("enforce: --scope ci|checkpoint is required")
        return 2

    from tests.proof import ledger as ledger_mod

    try:
        world = live_world(args.scope, args.commit or "HEAD")
    except ledger_mod.VacuousLedgerError as exc:
        print(f"error: {exc}")
        return 1
    problems = enforce_problems(world)
    for problem in problems:
        print(f"enforce --scope {args.scope}: {problem}")
    if not problems:
        print(f"enforce --scope {args.scope}: every clause and label is green or declared")
    return 1 if problems else 0


def _load_inventories() -> tuple[list[dict], list[dict], list[dict]]:
    gaps = tomllib.loads(GAPS_PATH.read_text()).get("gap", [])
    facets = tomllib.loads(FACETS_PATH.read_text()).get("facet", [])
    states = tomllib.loads(FOSSIL_MANIFEST_PATH.read_text()).get("state", [])
    return gaps, facets, states


def _state_pending(state: dict) -> bool:
    """A fossil state is pending iff its producer is "pending" and it is not
    declared `absent = true`: an absent state (K-13, e.g. `crashed`) has no
    S0 producer by design, so there is nothing to discharge."""
    return state.get("producer") == "pending" and not state.get("absent")


def cmd_inventory(args: argparse.Namespace) -> int:
    """Report the gap/facet/fossil-state inventories (L.P0-0a.4).

    `--strict`: exit 1 on any pending entry, or any `target = "required"`
    gap with no real entry point yet.
    `--count-pending [--lane X]`: print the pending count and exit 0 iff
    >=1 pending entry (1 otherwise) — a side-effect-free CM-7 probe
    (TM-P0-8). `--lane` filters to gaps owned by that lane; facets and
    fossil states (which are not lane-scoped in this delivery) are counted
    regardless of `--lane`.
    """
    gaps, facets, states = _load_inventories()

    if args.count_pending:
        lane = args.lane
        count = 0
        for g in gaps:
            if lane and g.get("lane") != lane:
                continue
            if g.get("entry") == "pending":
                count += 1
        if not lane:
            count += sum(1 for f in facets if f.get("extractor") == "pending")
            count += sum(1 for s in states if _state_pending(s))
        print(count)
        return 0 if count >= 1 else 1

    print(f"gaps: {len(gaps)} (21 expected)")
    print(f"facets: {len(facets)} (11 expected)")
    present_states = [s for s in states if not s.get("absent")]
    n_absent = len(states) - len(present_states)
    print(f"fossil states: {len(present_states)} present + {n_absent} absent (19 present expected)")

    if args.strict:
        pending = (
            [g["id"] for g in gaps if g.get("entry") == "pending"]
            + [f["id"] for f in facets if f.get("extractor") == "pending"]
            + [s["id"] for s in states if _state_pending(s)]
        )
        if pending:
            print(f"STRICT: {len(pending)} pending entries")
            return 1
    return 0


MATRIX_MAP_PATH = ROOT / "tests" / "proof" / "matrix_map.toml"


def cmd_check_map(_args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta check-map` (L.P0-0c.1): validate
    `matrix_map.toml`'s schema and the frozen MC-03 shape (65 clause ids,
    18 cells, 69 clause-parts, 3 stub-label-required, 2 adversary-stub)."""
    from tests.proof import transcribe as transcribe_mod

    data = tomllib.loads(MATRIX_MAP_PATH.read_text())
    clauses = data.get("clause", [])

    ok = True
    ids = []
    cells = set()
    n_parts = 0
    n_stub_req = 0
    n_adversary = 0
    id_re = transcribe_mod.MATRIX_CLAUSE_RE
    required_keys = {
        "id",
        "cell",
        "fragment",
        "rows",
        "rows_source",
        "stub_label_required",
        "adversary_stub",
    }
    for clause in clauses:
        missing = required_keys - set(clause)
        if missing or not ("step" in clause or "parts" in clause):
            print(f"check-map: {clause.get('id')}: missing key(s) {missing or {'step|parts'}}")
            ok = False
            continue
        cid = clause["id"]
        if not id_re.match(cid):
            print(f"check-map: {cid!r} does not match MC-03 clause id shape")
            ok = False
        ids.append(cid)
        cells.add(clause["cell"])
        n_parts += len(clause["parts"]) if "parts" in clause else 1
        if clause["stub_label_required"]:
            n_stub_req += 1
        if clause["adversary_stub"]:
            n_adversary += 1

    if len(ids) != len(set(ids)):
        print("check-map: duplicate clause id")
        ok = False
    if len(ids) != 65:
        print(f"check-map: {len(ids)} clause ids, expected 65")
        ok = False
    if len(cells) != 18:
        print(f"check-map: {len(cells)} cells, expected 18")
        ok = False
    if n_parts != 69:
        print(f"check-map: {n_parts} clause-parts, expected 69")
        ok = False
    if n_stub_req != 3:
        print(f"check-map: {n_stub_req} stub_label_required, expected 3")
        ok = False
    if n_adversary != 2:
        print(f"check-map: {n_adversary} adversary_stub, expected 2")
        ok = False

    return 0 if ok else 1


ROW_OWNERS_PATH = ROOT / "tests" / "proof" / "row_owners.toml"

# CSC-1 labels registry schema (exact key set; L.P0-0c.2). `id` and `row` are
# always required; every other key is optional.
CSC1_REQUIRED_KEYS = {"id", "row", "step", "slice", "tier", "venue", "posture", "declared_by"}
CSC1_OPTIONAL_KEYS = {"composes", "oq", "reason"}
CSC1_ALL_KEYS = CSC1_REQUIRED_KEYS | CSC1_OPTIONAL_KEYS
# C.9 / CSC-1: the only postures a label may carry. Whether a claim is
# proven is derived from results (MC-02), never a posture.
CSC1_POSTURES = ("claim", "gated_on", "both_variant", "deferred", "stub_proven", "shape", "na")


def _load_all_labels() -> list[dict[str, object]]:
    labels = []
    labels_dir = ROOT / "tests" / "proof" / "labels.d"
    for path in sorted(labels_dir.glob("*.toml")):
        data = tomllib.loads(path.read_text())
        labels.extend(data.get("label", []))
    return labels


def audit_rows_problems(
    rows: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
    world: EnforceWorld,
) -> list[str]:
    """`audit-rows --enforce` (L.CZ.2): each of the rows owns >= 1 CSC-1 label, or is credited
    through an MC-03 clause (`matrix_map.toml`'s `rows`), whose test passed in a named gate
    (`key_problems`: this run's ledger for a CI key, the selected HOST record for a HOST or
    DOCKER one), or a `gated_on` (open question) / `both_variant` / `na` (reason) label. A label
    whose test never ran renders UNPROVEN and does not count (WR-PROOF-2)."""
    by_row: dict[str, list[dict[str, Any]]] = {}
    for label in labels:
        by_row.setdefault(str(label.get("row")), []).append(label)
    credited: dict[str, list[dict[str, Any]]] = {}
    for clause in clauses:
        for row in clause.get("rows", []):
            credited.setdefault(row, []).append(clause)

    problems: list[str] = []
    for row in rows:
        rid = str(row["id"])
        owners, credits = by_row.get(rid, []), credited.get(rid, [])
        if not owners and not credits:
            problems.append(f"{rid}: no registered clause")
            continue
        why: list[str] = []
        closed = False
        for label in owners:
            found = label_problems(world, label)
            closed = closed or not found
            why += found
        for clause in credits:
            for key in clause_keys([clause]):
                found = clause_problems(world, key)
                closed = closed or not found
                why += found
        if not closed:
            problems.append(f"{rid}: no label or clause has a passing test ({why[0]})")
    return problems


def cmd_audit_rows(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta audit-rows [--enforce]` (L.P0-0c.2, L.CZ.2): validate the
    CSC-1 label schema exactly, then report every one of the 129 rows with no registered clause
    (a direct `<row>:<label>`, or credit through one of MC-03's matrix-credited rows,
    `matrix_map.toml`'s `rows` field). The report mode always exits 0 on a well-formed registry;
    `--enforce` exits 1 on any row without a passing test (`audit_rows_problems`)."""
    from tests.proof import transcribe as transcribe_mod

    labels = _load_all_labels()
    errors: list[str] = []
    for label in labels:
        extra = set(label) - CSC1_ALL_KEYS
        missing = CSC1_REQUIRED_KEYS - set(label)
        if extra or missing:
            errors.append(f"{label.get('id')}: bad schema (extra={extra}, missing={missing})")
            continue
        if label.get("posture") not in CSC1_POSTURES:
            errors.append(
                f"{label.get('id')}: posture={label.get('posture')!r} is not one of "
                f"{'|'.join(CSC1_POSTURES)} (C.9, CSC-1)"
            )
        if label.get("posture") == "gated_on" and not label.get("oq"):
            errors.append(f"{label.get('id')}: posture=gated_on requires an 'oq' field")
        composes = label.get("composes")
        if composes is not None:
            matrix_ids = transcribe_mod.matrix_ids()
            if composes not in matrix_ids and composes not in {
                c["id"] for c in transcribe_mod.load_matrix_map()
            }:
                errors.append(
                    f"{label.get('id')}: composes={composes!r} does not name an MC-03 clause/part"
                )

    if errors:
        print("audit-rows: label schema errors:")
        for e in errors:
            print(f"  {e}")
        return 1

    matrix_credited_rows: set[str] = set()
    for clause in tomllib.loads((ROOT / "tests" / "proof" / "matrix_map.toml").read_text()).get(
        "clause", []
    ):
        matrix_credited_rows.update(clause.get("rows", []))

    labeled_rows = {label["row"] for label in labels if "row" in label}
    rows = tomllib.loads(ROW_OWNERS_PATH.read_text()).get("row", [])
    uncovered = [
        r["id"] for r in rows if r["id"] not in labeled_rows and r["id"] not in matrix_credited_rows
    ]

    if getattr(args, "enforce", False):
        from tests.proof import ledger as ledger_mod

        try:
            world = live_world("ci")
        except ledger_mod.VacuousLedgerError as exc:
            print(f"error: {exc}")
            return 1
        problems = audit_rows_problems(rows, labels, world.clauses, world)
        for problem in problems:
            print(f"audit-rows --enforce: {problem}")
        print(f"audit-rows --enforce: {len(rows)} rows, {len(problems)} without a passing test")
        return 1 if problems else 0

    print(f"audit-rows: {len(rows)} rows, {len(uncovered)} with no registered clause (report mode)")
    if uncovered:
        for rid in uncovered:
            print(f"  unowned: {rid}")
    return 0


K_DOC_MAP_PATH = ROOT / "tests" / "proof" / "k_doc_map.toml"
OPEN_QUESTIONS_PATH = ROOT / "tests" / "proof" / "open_questions.toml"


def load_open_questions() -> list[dict[str, object]]:
    return list(tomllib.loads(OPEN_QUESTIONS_PATH.read_text()).get("oq", []))


# C.9's twelve open questions, the postures they may take, and the two the maintainer answered
# (maintainer_decisions_2026_09_28): those appear in no label's `oq`.
OQ_FINAL_IDS = frozenset(
    {
        "OQ-25", "OQ-26", "OQ-27", "OQ-29", "OQ-30", "OQ-31", "F-13(d)", "F-11(a)", "F-11(b)",
        "F-12", "F-B3-2", "OPEN-MISE-HOST",
    }
)  # fmt: skip
OQ_POSTURES = ("gated_on", "both_variant", "neutral")
OQ_ANSWERED = ("OQ-32", "Q-STRADDLE-ORPHAN")


def open_question_problems(
    labels: list[dict[str, Any]],
    oqs: list[dict[str, Any]],
    deferrals: list[dict[str, Any]],
) -> list[str]:
    """`open-questions --final` (L.CZ.7, CSC-15, DM-87): the twelve ids of `open_questions.toml`
    are exactly C.9's, each with a posture (gated_on, both_variant or neutral); every label bound
    to one carries gated_on or both_variant and none is claimed; OQ-32 and Q-STRADDLE-ORPHAN,
    answered, appear in no label's `oq` and in no open question; no deferral carries `until` (a
    PX-bound deferral)."""
    problems: list[str] = []
    ids = {str(o["id"]) for o in oqs}
    for missing in sorted(OQ_FINAL_IDS - ids):
        problems.append(f"{missing} is not in open_questions.toml")
    for extra in sorted(ids - OQ_FINAL_IDS):
        problems.append(f"{extra} is in open_questions.toml but is not one of C.9's twelve")
    for oq in oqs:
        if oq.get("posture") not in OQ_POSTURES:
            problems.append(
                f"{oq['id']}: posture {oq.get('posture')!r} is not {'|'.join(OQ_POSTURES)}"
            )
    for label in labels:
        oq = label.get("oq")
        if oq is None:
            continue
        if oq in OQ_ANSWERED:
            problems.append(
                f"{label.get('id')}: oq={oq!r} names a question the maintainer answered"
            )
        elif str(oq) not in ids:
            problems.append(f"{label.get('id')}: oq={oq!r} is not an open question")
        elif label.get("posture") not in ("gated_on", "both_variant"):
            problems.append(
                f"{label.get('id')}: is bound to {oq} but posture {label.get('posture')!r} "
                "claims it (must be gated_on or both_variant)"
            )
    for answered in OQ_ANSWERED:
        if answered in ids:
            problems.append(f"{answered} is answered, yet is in open_questions.toml")
    for entry in deferrals:
        if "until" in entry:
            problems.append(f"{entry.get('label')}: a PX-bound deferral (until={entry['until']!r})")
    return problems


def cmd_open_questions(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta open-questions [--final]` (L.P0-0c.3, L.CZ.7): exit 0 iff
    every label bound to a listed open-question id carries posture `gated_on` or `both_variant`
    and no claimed label or `proves` marker decides one. A label whose `oq` names an id outside
    `open_questions.toml` is a load error. `--final` adds `open_question_problems`."""
    oq_ids = {o["id"] for o in load_open_questions()}
    labels = _load_all_labels()

    for label in labels:
        oq = label.get("oq")
        if oq is None:
            continue
        if oq not in oq_ids:
            print(
                f"open-questions: load error: {label.get('id')} names oq={oq!r}, "
                "not in open_questions.toml"
            )
            return 1
        posture = label.get("posture")
        if posture not in ("gated_on", "both_variant"):
            print(
                f"open-questions: {label.get('id')} is bound to open question {oq!r} but "
                f"posture={posture!r} decides it (must be gated_on or both_variant)"
            )
            return 1

    if getattr(args, "final", False):
        from tests.proof import deferrals as deferrals_mod

        problems = open_question_problems(
            labels, load_open_questions(), deferrals_mod.load_deferrals()
        )
        for problem in problems:
            print(f"open-questions --final: {problem}")
        return 1 if problems else 0
    return 0


# RV-1 and RV-2's paired automated presence checks (reviews.py, C.4): the disclosure docs an RV
# review records, checked as tests. `kdoc --enforce` runs them; a missing node is a failed check.
RV_PRESENCE_NODES = {
    "RV-1": "tests/core/docs/test_cl_d1_disclosures.py",
    "RV-2": "tests/proof/b/test_stub_labels.py::test_docs_state_real_tool_unverified",
}


def _run_presence(node: str) -> tuple[int, str]:
    """One bounded pytest run of a presence node (the seam the self-test replaces)."""
    proc = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node])
    return proc.returncode, (proc.stdout + proc.stderr)[-300:]


def kdoc_problems(
    k_items: list[dict[str, Any]],
    *,
    cwd: Path | None = None,
    history: str | None = None,
    row_problems: Callable[[list[str]], list[str]] = lambda rows: [],  # noqa: ARG005
    presence: Callable[[str], tuple[int, str]] | None = None,
) -> list[str]:
    """`kdoc --enforce` (L.CZ.3, WR-PROOF-10): every K-item's landing merge has landed, every doc
    file `k_doc_map` names for it was edited in that landing commit, and its rows are closed
    (`row_problems`). `history` (`wr-ckpt/core..HEAD`) bounds the doc check to the landings inside
    that range: an earlier landing was K-doc-checked by `fence merge` before it landed (CM-3), and
    the landing-only read means no later edit could repair a miss, so this check never edits a doc.
    The RV-1 and RV-2 presence checks run once. A K-item with no landing merge that is marked
    conditional (K-16) is skipped."""
    from tests.proof import fence as fence_mod
    from tests.proof import kdoc as kdoc_mod
    from tests.proof import trailers as trailers_mod

    cwd = cwd or ROOT
    presence = presence or _run_presence
    problems: list[str] = []
    if history is not None and fence_mod._git(cwd, "rev-list", "-n1", history).returncode != 0:  # noqa: SLF001
        return [f"history range {history!r} is not readable in this checkout (fetch-depth 0, tags)"]
    for k in k_items:
        kid, merge = str(k["id"]), str(k.get("landing_merge") or "")
        if not merge:
            if "conditional" not in str(k.get("confirm", "")):
                problems.append(f"{kid}: no landing merge and not conditional")
            continue
        landing = trailers_mod.landing(merge, cwd=cwd)
        if landing is None:
            problems.append(f"{kid}: landing merge {merge} has not landed")
        elif history is None or trailers_mod.landing(merge, ref=history, cwd=cwd) is not None:
            missing = kdoc_mod.missing_docs(merge, landing, cwd=cwd, k_items=[k])
            if missing:
                problems.append(
                    f"{kid}: {merge} landed at {landing[:12]} without editing {missing}"
                )
        problems += [f"{kid}: {p}" for p in row_problems([str(r) for r in k.get("rows", [])])]
    for rv, node in RV_PRESENCE_NODES.items():
        rc, tail = presence(node)
        if rc != 0:
            problems.append(f"{rv} presence check {node} failed (exit {rc}): {tail.strip()[-160:]}")
    return problems


def cmd_kdoc(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta kdoc [--history <range>] [--enforce]`: the report mode is
    `kdoc.cmd_kdoc` (L.P0-0d.10); `--enforce` is `kdoc_problems` over the live checkout."""
    from tests.proof import kdoc as kdoc_mod
    from tests.proof import ledger as ledger_mod

    if not getattr(args, "enforce", False):
        return kdoc_mod.cmd_kdoc(args)
    try:
        world = live_world("ci")
    except ledger_mod.VacuousLedgerError as exc:
        print(f"error: {exc}")
        return 1
    rows = {str(r["id"]): r for r in tomllib.loads(ROW_OWNERS_PATH.read_text()).get("row", [])}

    def row_problems(ids: list[str]) -> list[str]:
        known = [rows[i] for i in ids if i in rows]
        found = [f"{i} is not a row of row_owners.toml" for i in ids if i not in rows]
        return found + audit_rows_problems(known, world.labels, world.clauses, world)

    problems = kdoc_problems(
        kdoc_mod.load_k_doc_map(), history=args.history, row_problems=row_problems
    )
    for problem in problems:
        print(f"kdoc --enforce: {problem}")
    if not problems:
        print("kdoc --enforce: every K-item landed with its docs and closed rows")
    return 1 if problems else 0


def mypy_error_sites() -> list[str]:
    """`file:line` for every current `mypy trestle` error."""
    proc = _run([sys.executable, "-m", "mypy", "trestle"])
    sites = []
    for line in proc.stdout.splitlines():
        match = re.match(r"^(\S+:\d+): error:", line)
        if match:
            sites.append(match.group(1))
    return sites


def cmd_mypy_ratchet(args: argparse.Namespace) -> int:
    """`mypy trestle` may carry at most `--max` errors (WR-PROOF-8:
    mypy-ratchet-le-1), and a known error may never move: every current
    error site must already be one of `baseline.json`'s `ev01.mypy_error_sites`
    (a fixed error count with a *different* location is still a ratchet
    violation, not a wash)."""
    sites = mypy_error_sites()
    baseline = json.loads(BASELINE_PATH.read_text())
    baseline_sites = set(baseline["ev01"].get("mypy_error_sites", []))

    if len(sites) > args.max:
        print(f"mypy-ratchet: {len(sites)} error(s) > max {args.max}: {sites}")
        return 1

    unknown = set(sites) - baseline_sites
    if unknown:
        print(f"mypy-ratchet: error site(s) not in the recorded baseline: {sorted(unknown)}")
        return 1

    return 0


def cmd_ckpt(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta ckpt <name> [--dry] [--preview] [--commit <sha>]`
    (L.P0-0d.4): evaluates `tests/proof/ckpt/<name>.py` only on the newest
    commit carrying `WR-Merge: <TRIGGER_MERGE>`; any other commit is a
    no-op exit 0."""
    from tests.proof import ckpt as ckpt_mod
    from tests.proof import fence as fence_mod
    from tests.proof import trailers as trailers_mod

    try:
        module = ckpt_mod.load_module(args.name)
    except ckpt_mod.UnknownCkptError:
        print(f"ckpt: unknown checkpoint {args.name!r}")
        return 2

    commit_ref = args.commit or "HEAD"
    commit_sha = fence_mod._git(ROOT, "rev-parse", commit_ref).stdout.strip()  # noqa: SLF001
    trigger_sha = trailers_mod.newest(module.TRIGGER_MERGE, ref=commit_ref, cwd=ROOT)
    # --dry lists pending reasons on any commit (L.P0-0d.4: at P0-0d there is no
    # J0 carrier yet) and --preview evaluates the PR head, which is never a
    # carrier; only a real evaluation is limited to the newest carrier.
    if not args.dry and not args.preview and (trigger_sha is None or trigger_sha != commit_sha):
        print(
            f"ckpt {args.name}: {commit_sha} is not the newest "
            f"{module.TRIGGER_MERGE} carrier; no-op"
        )
        return 0

    if (
        not args.dry
        and not args.preview
        and ckpt_mod.already_evaluated(module, commit_sha, cwd=ROOT)
    ):
        print(f"ckpt {args.name}: already evaluated at {commit_sha} (idempotent no-op)")
        return 0

    results = ckpt_mod.evaluate(module, commit_sha, preview=args.preview)
    pending = [r for r in results if not r.ok]

    if args.dry:
        for r in pending:
            print(f"pending:{r.id}")
        return 0

    if pending:
        for r in pending:
            print(f"ckpt {args.name}: {r.id}: {r.reason}")
        return 1

    digest = ckpt_mod.ledger_digest()
    print(f"ckpt {args.name}: pass; digest={digest}")
    return 0


MC31_CLASSES = ("forward_only", "drain", "transparent")


def register_final_problems(
    entries: list[dict[str, Any]],
    boundaries: list[dict[str, Any]],
    present: Callable[[dict[str, Any]], object] | None = None,
) -> list[str]:
    """`register --final` (L.CZ.5, CM-7): every entry's probe exits non-zero (the mechanism is
    gone), or the entry is `named-not-removed` with a citation and `serves = []` (T-5..T-7,
    TM-B4-1.x, TM-B2-2, TM-C4a/b: it stays, and serves no clause); nothing is scheduled for later
    (DM-77), and permanent test infrastructure is never registered. MC-31 records every rollback
    boundary's class, and none is still `pending:` once the merges have landed. SC-5 is met here,
    at J-ROOT."""
    from tests.proof import register as register_mod

    present = present or register_mod.active_phase
    problems: list[str] = []
    for entry in entries:
        eid, removed_by = entry["id"], entry["removed_by"]
        if entry.get("permanent") is not False:
            problems.append(f"{eid}: permanent infrastructure is not registered (CM-7)")
        if removed_by == "named-not-removed":
            if entry.get("serves") != []:
                problems.append(f"{eid}: named-not-removed but serves {entry.get('serves')!r}")
            if not str(entry.get("citation", "")).strip():
                problems.append(f"{eid}: named-not-removed without a citation")
        elif present(entry) is not None:
            problems.append(f"{eid} is present and its remover is {removed_by}")
    for boundary in boundaries:
        merge = boundary.get("merge")
        if boundary.get("class") not in MC31_CLASSES:
            problems.append(f"rollback {merge}: class {boundary.get('class')!r} is not recorded")
        if str(boundary.get("evidence", "")).startswith("pending:"):
            problems.append(f"rollback {merge}: evidence is still {boundary['evidence']!r}")
    return problems


def cmd_register(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta register[, --probe <id>, --final]` (L.P0-0d.1, L.CZ.5):
    exactly CM-7's register rule and probe command, implemented in `tests/proof/register.py`;
    `--final` is `register_final_problems` over the loaded register."""
    from tests.proof import register as register_mod

    if args.final:
        try:
            entries = register_mod.load_entries()
            boundaries = register_mod.load_rollback()
        except (register_mod.RegisterLoadError, FileNotFoundError) as exc:
            print(f"register --final: {exc}")
            return 1
        problems = register_final_problems(entries, boundaries)
        for problem in problems:
            print(f"register --final: {problem}")
        return 1 if problems else 0
    if args.probe:
        return register_mod.cmd_probe(args.probe)
    return register_mod.cmd_register()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.meta")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("baseline")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--json", action="store_true")
    enforce_parser = sub.add_parser("enforce")
    enforce_parser.add_argument("--print-mode", action="store_true", dest="print_mode")
    enforce_parser.add_argument("--scope", choices=ENFORCE_SCOPES, default=None)
    enforce_parser.add_argument("--commit", default=None)
    inventory_parser = sub.add_parser("inventory")
    inventory_parser.add_argument("--strict", action="store_true")
    inventory_parser.add_argument("--count-pending", action="store_true", dest="count_pending")
    inventory_parser.add_argument("--lane", default=None)
    ratchet_parser = sub.add_parser("mypy-ratchet")
    ratchet_parser.add_argument("--max", type=int, required=True)
    sub.add_parser("check-map")
    audit_parser = sub.add_parser("audit-rows")
    audit_parser.add_argument("--enforce", action="store_true")
    oq_parser = sub.add_parser("open-questions")
    oq_parser.add_argument("--final", action="store_true")
    register_parser = sub.add_parser("register")
    register_parser.add_argument("--probe", default=None)
    register_parser.add_argument("--final", action="store_true")
    kdoc_parser = sub.add_parser("kdoc")
    kdoc_parser.add_argument("--history", default=None)
    kdoc_parser.add_argument("--enforce", action="store_true")
    drain_parser = sub.add_parser("drain-check")
    drain_parser.add_argument("--home", default=None)
    ckpt_parser = sub.add_parser("ckpt")
    ckpt_parser.add_argument("name")
    ckpt_parser.add_argument("--dry", action="store_true")
    ckpt_parser.add_argument("--preview", action="store_true")
    ckpt_parser.add_argument("--commit", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "baseline":
        return cmd_baseline(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "enforce":
        return cmd_enforce(args)
    if args.command == "inventory":
        return cmd_inventory(args)
    if args.command == "mypy-ratchet":
        return cmd_mypy_ratchet(args)
    if args.command == "check-map":
        return cmd_check_map(args)
    if args.command == "audit-rows":
        return cmd_audit_rows(args)
    if args.command == "open-questions":
        return cmd_open_questions(args)
    if args.command == "register":
        return cmd_register(args)
    if args.command == "kdoc":
        return cmd_kdoc(args)
    if args.command == "ckpt":
        return cmd_ckpt(args)
    if args.command == "drain-check":
        from tests.proof import drain_check

        return drain_check.cmd_drain_check(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
