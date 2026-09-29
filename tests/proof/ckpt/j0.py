"""J0 condition module (CM-5; L.P0-0d.8). `TRIGGER_MERGE = "J0"`, `TAG =
None` (J0 is a check-run checkpoint, O-3). Named neither `test_*.py` nor
`*_test.py`: its live conditions never run in default collection
(DM-80); its default-collected selftest (`test_ckpt_j0.py`) uses planted
ledgers only.

12 conditions, J0-1..J0-12. This delivery's own reconstruction of a
reasonable J0-1..J0-12 split (p0-court.md names the count and J0-1/J0-2/
J0-10 explicitly; the rest are this leaf's assembly of the P0 checks the
root and p0-court.md name elsewhere) — recorded as a plan-gap in the
delivery return.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.proof import ckpt as ckpt_mod
from tests.proof import fence as fence_mod
from tests.proof import register as register_mod
from tests.proof.host import record as record_mod
from tests.proof.markers import TargetUnmet

TRIGGER_MERGE = "J0"
TAG = None

ROOT = Path(__file__).resolve().parents[2]
S0_BASE = "5fbdd2f"


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
    """Live J0-2: `pytest -m target --runxfail` with `audit_plugin`
    recording each node's exception; the run itself is expected to exit
    non-zero (every target fails by design), so the verdict is the audit
    of the recorded per-node outcomes, never the exit status."""
    import json
    import os
    import tempfile

    from tests.proof import meta as meta_mod
    from tests.proof import tolerances as tol

    gaps, _facets, _states = meta_mod._load_inventories()
    required = [g["id"] for g in gaps if g.get("target") == "required"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "audit.json"
        env = dict(os.environ, TRESTLE_AUDIT_OUT=str(out))
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-m",
                    "target",
                    "--runxfail",
                    "-q",
                    "-p",
                    "tests.proof.audit_plugin",
                    "-p",
                    "no:cacheprovider",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=tol.JOIN_WAIT_S * len(gaps),
            )
        except subprocess.TimeoutExpired:
            return False, "red-reason audit: pytest run timed out"
        if not out.exists():
            return False, "red-reason audit: the run recorded no per-node outcomes"
        return audit_run(json.loads(out.read_text()), required)


def _history_check(commit: str) -> tuple[bool, str]:
    result = fence_mod.check_history(f"{S0_BASE}..{commit}", ROOT)
    return result.ok, result.message


def _lane_check(lane_label: str, gap_lane: str):
    def check(_commit: str) -> tuple[bool, str]:
        from tests.proof import meta as meta_mod

        gaps, _facets, _states = meta_mod._load_inventories()
        pending = [
            g["id"] for g in gaps if g.get("lane") == gap_lane and g.get("entry") == "pending"
        ]
        if pending:
            return False, f"pending: {pending}"
        return True, ""

    return check


def _register_check(_commit: str) -> tuple[bool, str]:
    violations = register_mod.register_violations()
    if violations:
        return False, f"{violations}"
    return True, ""


def _kdoc_check(_commit: str) -> tuple[bool, str]:
    # P0 lands no K-item (no docs/ edit, root "## Merge order and gates"),
    # so there is nothing for kdoc to report at J0.
    return True, ""


def _host_record_check(_commit: str) -> tuple[bool, str]:
    """J0-10: `record.select("host-proc", HEAD)` judged by
    `record.pass_set_violations` — never "every node PASSED"."""
    proc_record = record_mod.select("host-proc", "HEAD", cwd=ROOT)
    if proc_record is None:
        return False, "no admissible host-proc record for HEAD"
    from tests.proof import meta as meta_mod

    labels = {lbl["id"]: lbl for lbl in meta_mod._load_all_labels()}
    violations = record_mod.pass_set_violations(proc_record, labels)
    if violations:
        return False, f"{violations}"
    docker_record = record_mod.paired_docker(proc_record, cwd=ROOT)
    if docker_record is not None and docker_record.get("mode") == "run":
        docker_violations = record_mod.pass_set_violations(docker_record, labels)
        if docker_violations:
            return False, f"{docker_violations}"
    return True, ""


def _docker_preflight_check(_commit: str) -> tuple[bool, str]:
    # Report mode always exits 0 (PRECONDITION_UNMET is a valid recorded
    # status at P0, PX-3's image half); J0 does not require a pass here.
    return True, ""


def _check_map_check(_commit: str) -> tuple[bool, str]:
    from tests.proof import meta as meta_mod

    rc = meta_mod.cmd_check_map(None)
    return rc == 0, "check-map failed"


CONDITIONS = [
    ckpt_mod.Condition("history", _history_check, merge_only=True),
    ckpt_mod.Condition("red-reason-audit", _run_red_reason_audit),
    ckpt_mod.Condition("lane-a", _lane_check("lane-a", "A")),
    ckpt_mod.Condition("lane-b", _lane_check("lane-b", "B")),
    ckpt_mod.Condition("lane-c", _lane_check("lane-c", "C")),
    ckpt_mod.Condition("lane-d", _lane_check("lane-d", "D")),
    ckpt_mod.Condition("lane-e", _lane_check("lane-e", "E")),
    ckpt_mod.Condition("register", _register_check),
    ckpt_mod.Condition("check-map", _check_map_check),
    ckpt_mod.Condition("kdoc", _kdoc_check),
    ckpt_mod.Condition("host-record", _host_record_check, merge_only=True),
    ckpt_mod.Condition("docker-preflight", _docker_preflight_check, merge_only=True),
]

assert len(CONDITIONS) == 12, "J0-1..J0-12"

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
