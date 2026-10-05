"""J0 condition module (CM-5; L.P0-0d.8). `TRIGGER_MERGE = "J0"`, `TAG =
None` (J0 is a check-run checkpoint, O-3). Named neither `test_*.py` nor
`*_test.py`: its live conditions never run in default collection
(DM-80); its default-collected selftest (`test_ckpt_j0.py`) plants data
and a fake command runner, never a live pytest-in-pytest run.

Exactly the plan's J0-1..J0-12 (plans/p0-court.md, the `**J0**` block):

  J0-1  product identity (P-ID) and `fence check --history 5fbdd2f..HEAD`
  J0-2  red-reason audit (`-m target --runxfail`)
  J0-3  `-m target -q`: every target XFAIL, no XPASS
  J0-4  `-m pin -q` green and `meta inventory --strict`
  J0-5  `pytest tests/proof/selftest tests/proof/drift -q` green
  J0-6  `meta report --json`: 18 cells, 65 clauses, 69 parts, all UNPROVEN
  J0-7  `differ d1 --strict` and `differ d2 --reader s0`
  J0-8  `meta baseline`
  J0-9  check-map, audit-rows, open-questions, register
  J0-10 host-proc record for the commit (CSC-5 default set, CM-6 role 2)
        and a host-docker preflight record at that sha
  J0-11 `RV-5-j0.toml` outcome pass; ledger shows `review:RV-5`
  J0-12 every CI job green: `ckpt` runs only after every other job

`merge_only` is unused: J0-1's history half applies only when the commit
is itself the `WR-Merge: J0` carrier (an unlanded PR head has no landed
history to check), so `--preview` on the PR head evaluates everything else.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from tests.proof import ckpt as ckpt_mod
from tests.proof import fence as fence_mod
from tests.proof import reviews as reviews_mod
from tests.proof import trailers as trailers_mod
from tests.proof.markers import TargetUnmet

TRIGGER_MERGE = "J0"
TAG = None

ROOT = Path(__file__).resolve().parents[3]
S0_BASE = "5fbdd2f"
PRODUCT_PATHS = [
    "trestle",
    "packages/trestle-packs/trestle_packs",
    "packages/trestle-packs/pyproject.toml",
    "examples",
    "docs",
    "scripts",
    ".cursor",
    "console",
]
REQUIRED_GAPS = 20
TOTAL_GAPS = 21
D2_STATES = 19
CLAUSES, CELLS, PARTS, STUB_CLAUSES = 65, 18, 69, 3
ADVERSARY_CLAUSES = {"B4.5", "B9.1"}
REVIEW_FILE = "RV-5-j0.toml"
PY = sys.executable


def _run(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """One bounded subprocess in the repository root; a timeout is a
    failed run (returncode 124), never an exception. The single seam the
    selftest replaces."""
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


def _tail(proc: subprocess.CompletedProcess) -> str:
    return (proc.stdout + proc.stderr)[-400:].strip()


def _meta_ok(*args: str) -> tuple[bool, str]:
    proc = _run([PY, "-m", "tests.proof.meta", *args])
    return proc.returncode == 0, f"meta {' '.join(args)}: exit {proc.returncode}: {_tail(proc)}"


def _audit(pytest_args: list[str]) -> tuple[dict | None, subprocess.CompletedProcess]:
    """One pytest run with `audit_plugin` recording each node's outcome."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "audit.json"
        env = dict(os.environ, TRESTLE_AUDIT_OUT=str(out))
        proc = _run(
            [PY, "-m", "pytest", *pytest_args, "-q", "-p", "tests.proof.audit_plugin"]
            + ["-p", "no:cacheprovider"],
            env=env,
        )
        data = json.loads(out.read_text()) if out.exists() else None
    return data, proc


def _gaps() -> list[dict]:
    from tests.proof import meta as meta_mod

    return meta_mod._load_inventories()[0]


def _required(gaps: list[dict]) -> list[str]:
    return [g["id"] for g in gaps if g.get("target") == "required"]


class AuditReport:
    """One `-m target --runxfail` node's outcome, for J0-2's audit.
    `exc_type` is the raised exception's class name (e.g. `TargetUnmet`,
    `ImportError`); `gap`/`unmet_gap` compare the node's declared gap
    against the one a `TargetUnmet` actually names. `has_reason` is False
    for a skipped node carrying no `gated_on`/`na` reason."""

    def __init__(
        self,
        nodeid: str,
        gap: str,
        outcome: str,
        exc_type: str | None,
        unmet_gap: str | None,
        has_reason: bool = True,
    ):
        self.nodeid = nodeid
        self.gap = gap
        self.outcome = outcome
        self.exc_type = exc_type
        self.unmet_gap = unmet_gap
        self.has_reason = has_reason


def audit_report(reports: list[AuditReport]) -> tuple[bool, str]:
    """J0-2: accepts only a `TargetUnmet` naming the target's own gap
    (MC-P0-04). An XPASS, an ImportError-caused failure, a `TargetUnmet`
    naming the wrong gap or a skip with no reason each fail the audit."""
    bad = []
    for r in reports:
        if r.outcome == "xfailed":
            if r.exc_type != "TargetUnmet":
                bad.append(f"{r.nodeid}: failed by {r.exc_type}, not TargetUnmet")
            elif r.unmet_gap != r.gap:
                bad.append(f"{r.nodeid}: TargetUnmet names {r.unmet_gap!r}, expected {r.gap!r}")
        elif r.outcome == "xpassed":
            bad.append(f"{r.nodeid}: XPASS (target gap {r.gap!r} appears fixed; flip it)")
        elif r.outcome in ("skipped",):
            if not r.has_reason:
                bad.append(f"{r.nodeid}: skipped without a gated_on/na reason")
        elif r.outcome == "failed":
            bad.append(f"{r.nodeid}: failed outright (not even a strict xfail)")
    if bad:
        return False, "; ".join(bad)
    return True, ""


def reports_from_run(data: dict) -> list[AuditReport]:
    """Turn `audit_plugin`'s JSON (a `-m target --runxfail` run) into
    `AuditReport`s. Under `--runxfail` a target's red outcome is a plain
    failure carrying its exception: `failed` becomes the audit's
    `xfailed` (red for a reason), `passed` its `xpassed`. A target node
    that recorded no outcome at all is `failed` (it never ran to an end)."""
    outcomes = data.get("outcomes", {})
    reports = []
    for node in data.get("nodes", []):
        gap = node.get("gap")
        if gap is None:
            continue
        got = outcomes.get(node["nodeid"])
        if got is None:
            reports.append(AuditReport(node["nodeid"], gap, "failed", None, None))
            continue
        outcome = {"failed": "xfailed", "passed": "xpassed"}.get(got["outcome"], got["outcome"])
        reports.append(
            AuditReport(
                node["nodeid"],
                gap,
                outcome,
                got.get("exc_type"),
                got.get("unmet_gap"),
                bool(node.get("has_reason")),
            )
        )
    return reports


def audit_run(data: dict, required_gaps: list[str]) -> tuple[bool, str]:
    """J0-2 over one run's data: every node passes `audit_report`, and
    every required gap has >=1 node that failed with `TargetUnmet` naming
    it."""
    reports = reports_from_run(data)
    ok, reason = audit_report(reports)
    missing = [
        g
        for g in required_gaps
        if not any(
            r.gap == g and r.outcome == "xfailed" and r.exc_type == "TargetUnmet" for r in reports
        )
    ]
    if missing:
        ok = False
        reason = (reason + "; " if reason else "") + f"no TargetUnmet-red target for {missing}"
    return ok, reason


def _run_red_reason_audit(_commit: str) -> tuple[bool, str]:
    """J0-2: `pytest -m target --runxfail` with `audit_plugin` recording
    each node's exception; the run is expected to exit non-zero (every
    target fails by design), so the verdict is the audit of the recorded
    per-node outcomes, never the exit status."""
    data, proc = _audit(["-m", "target", "--runxfail"])
    if data is None:
        return False, f"red-reason audit: the run recorded no per-node outcomes: {_tail(proc)}"
    return audit_run(data, _required(_gaps()))


def target_xfail_verdict(
    data: dict, required: list[str], returncode: int, total_required: int = REQUIRED_GAPS
) -> tuple[bool, str]:
    """J0-3: every target node XFAIL (never XPASS, skip, error or pass),
    >=1 per required gap, `total_required` gaps, and a green exit."""
    bad = []
    if len(required) != total_required:
        bad.append(f"{len(required)} required gaps, expected {total_required}")
    outcomes = data.get("outcomes", {})
    seen: set[str] = set()
    for node in data.get("nodes", []):
        gap = node.get("gap")
        if gap is None:
            continue
        got = outcomes.get(node["nodeid"], {}).get("outcome")
        if got == "xfailed":
            seen.add(gap)
        else:
            bad.append(f"{node['nodeid']}: {got or 'no outcome'}, expected xfailed")
    missing = [g for g in required if g not in seen]
    if missing:
        bad.append(f"no XFAIL target for {missing}")
    if returncode != 0:
        bad.append(f"pytest exit {returncode}")
    return (not bad), "; ".join(bad)


def _target_xfail(_commit: str) -> tuple[bool, str]:
    data, proc = _audit(["-m", "target"])
    if data is None:
        return False, f"no per-node outcomes: {_tail(proc)}"
    return target_xfail_verdict(data, _required(_gaps()), proc.returncode)


def pin_verdict(
    data: dict, gaps: list[dict], returncode: int, total: int = TOTAL_GAPS
) -> tuple[bool, str]:
    """J0-4: `-m pin` green, every one of the 21 gaps has >=1 pin node,
    no pending entry, and G-C4's target is exactly `none(DM-20)`."""
    bad = []
    if len(gaps) != total:
        bad.append(f"{len(gaps)} gaps, expected {total}")
    outcomes = data.get("outcomes", {})
    pinned: set[str] = set()
    for node in data.get("nodes", []):
        if node.get("pin_gap") is None:
            continue
        pinned.add(node["pin_gap"])
        if outcomes.get(node["nodeid"], {}).get("outcome") != "passed":
            bad.append(f"{node['nodeid']}: pin did not pass")
    missing = [g["id"] for g in gaps if g["id"] not in pinned]
    if missing:
        bad.append(f"no pin for {missing}")
    pending = [g["id"] for g in gaps if g.get("entry") == "pending"]
    if pending:
        bad.append(f"pending entries {pending}")
    gc4 = [g for g in gaps if g["id"] == "G-C4"]
    if not gc4 or gc4[0].get("target") != "none(DM-20)":
        bad.append("G-C4 target is not none(DM-20)")
    if returncode != 0:
        bad.append(f"pytest exit {returncode}")
    return (not bad), "; ".join(bad)


def _pin_check(_commit: str) -> tuple[bool, str]:
    data, proc = _audit(["-m", "pin"])
    if data is None:
        return False, f"no per-node outcomes: {_tail(proc)}"
    ok, reason = pin_verdict(data, _gaps(), proc.returncode)
    inv_ok, inv_reason = _meta_ok("inventory", "--strict")
    if not inv_ok:
        ok, reason = False, "; ".join(x for x in (reason, inv_reason) if x)
    return ok, reason


def _selftest_drift_check(_commit: str) -> tuple[bool, str]:
    proc = _run([PY, "-m", "pytest", "tests/proof/selftest", "tests/proof/drift", "-q"])
    return proc.returncode == 0, f"selftest+drift: exit {proc.returncode}: {_tail(proc)}"


def matrix_verdict(report: dict, clauses: list[dict], labels: list[dict]) -> tuple[bool, str]:
    """J0-6: the matrix map is 18 cells / 65 clauses / 69 parts, every
    clause renders UNPROVEN in the ledger (a clause is PROVEN only when
    some `<clause>[:label]` ledger key is), 3 clauses need a STUB label,
    and exactly B4.5 and B9.1 are adversary clauses carrying no label."""
    bad = []
    cells = {c["cell"] for c in clauses}
    parts = sum(len(c["parts"]) if "parts" in c else 1 for c in clauses)
    for what, got, want in (
        ("cells", len(cells), CELLS),
        ("clauses", len(clauses), CLAUSES),
        ("parts", parts, PARTS),
    ):
        if got != want:
            bad.append(f"{got} {what}, expected {want}")
    statuses: dict[str, list[str]] = {}
    for key, entry in report.items():
        if not key.startswith("review:"):
            statuses.setdefault(key.split(":")[0], []).append(entry["status"])
    proven = sorted(c["id"] for c in clauses if "PROVEN" in statuses.get(c["id"], []))
    if proven:
        bad.append(f"clauses not UNPROVEN: {proven}")
    stubs = sum(1 for c in clauses if c.get("stub_label_required"))
    if stubs != STUB_CLAUSES:
        bad.append(f"{stubs} STUB-label clauses, expected {STUB_CLAUSES}")
    adversary = {c["id"] for c in clauses if c.get("adversary_stub")}
    if adversary != ADVERSARY_CLAUSES:
        bad.append(f"adversary clauses {sorted(adversary)}, expected {sorted(ADVERSARY_CLAUSES)}")
    labelled = sorted(
        str(lbl["composes"]).split(":")[0]
        for lbl in labels
        if str(lbl.get("composes", "")).split(":")[0] in ADVERSARY_CLAUSES
    )
    if labelled:
        bad.append(f"adversary clauses carrying a label: {labelled}")
    return (not bad), "; ".join(bad)


def _matrix_check(_commit: str) -> tuple[bool, str]:
    from tests.proof import ledger as ledger_mod
    from tests.proof import meta as meta_mod

    try:
        report = ledger_mod.render()
    except ledger_mod.VacuousLedgerError as exc:
        return False, f"meta report: {exc}"
    clauses = tomllib.loads(meta_mod.MATRIX_MAP_PATH.read_text()).get("clause", [])
    return matrix_verdict(report, clauses, meta_mod._load_all_labels())


def d2_states_verdict(states: dict, exceptions: list[dict]) -> tuple[bool, str]:
    """J0-7's d2 half beyond the exit status: 19 states really checked
    and no CM-9 exception excusing any reader-`s0` divergence."""
    checked = [
        sid
        for sid, (_band, entry) in states.items()
        if entry.get("producer") not in (None, "pending") and not entry.get("absent")
    ]
    bad = []
    if len(checked) != D2_STATES:
        bad.append(f"d2 checks {len(checked)} states, expected {D2_STATES}")
    used = [e.get("id") for e in exceptions if e.get("reader") == "s0"]
    if used:
        bad.append(f"d2 exceptions for reader s0: {used}")
    return (not bad), "; ".join(bad)


def _differ_check(_commit: str) -> tuple[bool, str]:
    from tests.proof import differ as differ_mod
    from tests.proof import fossils as fossils_mod

    bad = []
    d1 = _run([PY, "-m", "tests.proof.differ", "d1", "--strict"])
    if d1.returncode != 0:
        bad.append(f"differ d1 --strict: exit {d1.returncode}: {_tail(d1)}")
    d2 = _run([PY, "-m", "tests.proof.differ", "d2", "--reader", "s0"])
    if d2.returncode != 0:
        bad.append(f"differ d2 --reader s0: exit {d2.returncode}: {_tail(d2)}")
    path = differ_mod.D2_EXCEPTIONS_PATH
    exceptions = tomllib.loads(path.read_text()).get("exception", []) if path.exists() else []
    ok, reason = d2_states_verdict(fossils_mod.load_states(), exceptions)
    if not ok:
        bad.append(reason)
    return (not bad), "; ".join(bad)


def _baseline_check(_commit: str) -> tuple[bool, str]:
    return _meta_ok("baseline")


def _maps_check(_commit: str) -> tuple[bool, str]:
    bad = []
    for sub in (["check-map"], ["audit-rows"], ["open-questions"], ["register"]):
        ok, reason = _meta_ok(*sub)
        if not ok:
            bad.append(reason)
    return (not bad), "; ".join(bad)


def _identity_check(commit: str) -> tuple[bool, str]:
    """J0-1: P-ID, and (on the `WR-Merge: J0` carrier) the history rule."""
    proc = fence_mod._git(  # noqa: SLF001
        ROOT, "diff", "--quiet", S0_BASE, commit, "--", *PRODUCT_PATHS
    )
    if proc.returncode != 0:
        return False, f"P-ID: product paths differ from {S0_BASE}"
    full = fence_mod._git(ROOT, "rev-parse", commit).stdout.strip()  # noqa: SLF001
    if trailers_mod.newest(TRIGGER_MERGE, ref=commit, cwd=ROOT) != full:
        return True, ""  # not the carrier: no landed J0 history to check yet
    result = fence_mod.check_history(f"{S0_BASE}..{commit}", ROOT)
    return result.ok, result.message


def default_set_nodes() -> list[dict]:
    """The nodes of a `TRESTLE_HOST_GATE=proc` collect-only run (host_only
    nodes not deselected), for CSC-5's default set."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "collect.json"
        env = dict(os.environ, TRESTLE_HOST_GATE="proc", TRESTLE_AUDIT_OUT=str(out))
        proc = _run(
            [PY, "-m", "pytest", "-q", "-p", "tests.proof.audit_plugin", "--collect-only"]
            + ["-p", "no:cacheprovider"],
            env=env,
        )
        if not out.exists():
            raise RuntimeError(f"collect-only produced no audit document: {_tail(proc)}")
        return json.loads(out.read_text())["nodes"]


def host_record_verdict(
    proc_record: dict | None,
    default_nodeids: list[str],
    labels: dict[str, dict],
    docker_record: dict | None,
) -> tuple[bool, str]:
    """J0-10 over already-selected records (CM-6): the host-proc record
    covers every CSC-5 default-set node, meets the role-2 pass set, and a
    host-docker record exists at the same sha with any status (a
    PRECONDITION_UNMET one is fine, PX-3)."""
    from tests.proof.host import record as record_mod

    if proc_record is None:
        return False, "no admissible host-proc record for the commit"
    bad = []
    if proc_record.get("status") != "PASSED":
        bad.append(f"host-proc status {proc_record.get('status')}")
    ran = {r.get("nodeid") for r in proc_record.get("results", [])}
    uncovered = [n for n in default_nodeids if n not in ran]
    if uncovered:
        bad.append(f"{len(uncovered)} default-set node(s) not in the record: {uncovered[:5]}")
    bad.extend(record_mod.pass_set_violations(proc_record, labels))
    if docker_record is None:
        bad.append("no host-docker preflight record at the host-proc sha")
    return (not bad), "; ".join(bad)


def _host_record_check(commit: str) -> tuple[bool, str]:
    from tests.proof import meta as meta_mod
    from tests.proof.host import proc_gate
    from tests.proof.host import record as record_mod

    proc_record = record_mod.select("host-proc", commit, cwd=ROOT)
    if proc_record is None:
        return False, "no admissible host-proc record for the commit"
    all_labels = meta_mod._load_all_labels()
    default = proc_gate.default_set(default_set_nodes(), all_labels)
    docker = record_mod.paired_docker(proc_record, cwd=ROOT)
    return host_record_verdict(proc_record, default, {lbl["id"]: lbl for lbl in all_labels}, docker)


def review_verdict(
    review_dir: Path,
    ancestor_of,
    render=None,
) -> tuple[bool, str]:
    """J0-11: `RV-5-j0.toml` is valid, outcome pass, its `sha` is an
    ancestor of the evaluated commit (`ancestor_of(sha) -> bool`), and the
    ledger shows `review:RV-5`."""
    from tests.proof import ledger as ledger_mod

    path = review_dir / REVIEW_FILE
    if not path.exists():
        return False, f"{REVIEW_FILE} is absent"
    try:
        record = reviews_mod.load(path)
    except reviews_mod.ReviewSchemaError as exc:
        return False, str(exc)
    if record["id"] != "RV-5":
        return False, f"{REVIEW_FILE} carries id {record['id']!r}, expected RV-5"
    if record["outcome"] != "pass":
        return False, f"RV-5 outcome is {record['outcome']!r}, expected pass"
    if not ancestor_of(record["sha"]):
        return False, f"RV-5 reviewed sha {record['sha']} is not an ancestor of the commit"
    try:
        report = (render or (lambda: ledger_mod.render(reviews_dir=review_dir)))()
    except ledger_mod.VacuousLedgerError as exc:
        return False, f"ledger: {exc}"
    if "review:RV-5" not in report:
        return False, "the ledger does not show review:RV-5"
    return True, ""


def _review_check(commit: str) -> tuple[bool, str]:
    def ancestor(sha: str) -> bool:
        return fence_mod.is_ancestor(ROOT, sha, commit)

    return review_verdict(reviews_mod.REVIEWS_DIR, ancestor)


def ci_needs_verdict(jobs: dict) -> tuple[bool, str]:
    """J0-12 by construction: `ckpt` runs only once every other job of
    ci.yml has succeeded, i.e. its `needs:` closure contains them all
    (CM-4/CM-5)."""
    if "ckpt" not in jobs:
        return False, "ci.yml has no ckpt job"

    def closure(job_id: str, seen: set[str]) -> set[str]:
        needs = jobs.get(job_id, {}).get("needs", [])
        for dep in [needs] if isinstance(needs, str) else needs:
            if dep not in seen:
                seen.add(dep)
                closure(dep, seen)
        return seen

    missing = sorted(set(jobs) - {"ckpt"} - closure("ckpt", set()))
    if missing:
        return False, f"ckpt does not need {missing}"
    return True, ""


def _ci_check(_commit: str) -> tuple[bool, str]:
    import yaml

    jobs = yaml.safe_load(fence_mod.CI_YML_PATH.read_text()).get("jobs", {})
    return ci_needs_verdict(jobs)


CONDITIONS = [
    ckpt_mod.Condition("J0-1", _identity_check),
    ckpt_mod.Condition("J0-2", _run_red_reason_audit),
    ckpt_mod.Condition("J0-3", _target_xfail),
    ckpt_mod.Condition("J0-4", _pin_check),
    ckpt_mod.Condition("J0-5", _selftest_drift_check),
    ckpt_mod.Condition("J0-6", _matrix_check),
    ckpt_mod.Condition("J0-7", _differ_check),
    ckpt_mod.Condition("J0-8", _baseline_check),
    ckpt_mod.Condition("J0-9", _maps_check),
    ckpt_mod.Condition("J0-10", _host_record_check),
    ckpt_mod.Condition("J0-11", _review_check),
    ckpt_mod.Condition("J0-12", _ci_check),
]

assert [c.id for c in CONDITIONS] == [f"J0-{n}" for n in range(1, 13)]

__all__ = [
    "TRIGGER_MERGE",
    "TAG",
    "CONDITIONS",
    "AuditReport",
    "audit_report",
    "audit_run",
    "reports_from_run",
    "TargetUnmet",
]
