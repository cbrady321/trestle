"""CM-6: HOST record admissibility, selection and the role-2 pass set
(MC-27). Built once here; every reader (the ledger renderer, `fence
check`, `live_records.py`, the ckpt modules) calls these functions and
implements none of them.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from tests.proof import fence as fence_mod

ROOT = Path(__file__).resolve().parents[2]
HOST_DIR = ROOT / "tests" / "proof" / "host"

REQUIRED_KEYS = {"schema", "gate", "sha", "mode", "python", "platform", "results", "status"}
OPTIONAL_KEYS = {
    "engine",
    "images",
    "inventory_before",
    "inventory_after",
    "diff",
    "residue",
}
ALL_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS
VALID_MODES = {"run", "preflight"}
VALID_STATUSES = {"PASSED", "FAILED", "PRECONDITION_UNMET"}

_SHA_RE = re.compile(r"^[0-9a-f]{6,40}$")


class RecordSchemaError(ValueError):
    pass


def validate_schema(record: dict) -> None:
    extra = set(record) - ALL_KEYS
    missing = REQUIRED_KEYS - set(record)
    if extra or missing:
        raise RecordSchemaError(f"bad record schema (extra={extra}, missing={missing})")
    if record["mode"] not in VALID_MODES:
        raise RecordSchemaError(f"mode must be one of {VALID_MODES}")
    if record["status"] not in VALID_STATUSES:
        raise RecordSchemaError(f"status must be one of {VALID_STATUSES}")


def _record_dir(gate: str, cwd: Path | None = None) -> Path:
    base = (cwd or ROOT) / "tests" / "proof" / "host"
    return base / gate


def _record_glob() -> list[str]:
    return ["tests/proof/host/host-*/*.json"]


class MissingShaError(RuntimeError):
    """Raised in a shallow repository when a record's sha is absent
    (CM-6): a record-reading job without `fetch-depth: 0` fails visibly."""


def is_admissible(record: dict, anchor: str, cwd: Path) -> tuple[bool, str | None]:
    """`(admissible, warning)`. Admissible iff the record's sha is an
    ancestor of `anchor` and the diff since touches record files only.
    A record whose sha is not an ancestor is inadmissible (no error). A
    record whose sha is absent from the repository: skipped with a
    warning in a full checkout, or raises `MissingShaError` in a shallow
    one."""
    sha = record["sha"]
    check = fence_mod._git(cwd, "cat-file", "-e", f"{sha}^{{commit}}")  # noqa: SLF001
    if check.returncode != 0:
        if fence_mod.is_shallow(cwd):
            raise MissingShaError(f"sha {sha} is missing from this shallow checkout")
        return False, fence_mod.missing_sha_warning(f"{_record_dir(record['gate'], cwd)}", sha)
    if not fence_mod.is_ancestor(cwd, sha, anchor):
        return False, None
    diff = fence_mod.diff_paths(cwd, sha, anchor)
    for path in diff:
        if not fence_mod.glob_match(path, _record_glob()):
            return False, None
    return True, None


def _load_records(gate: str, cwd: Path | None = None) -> list[tuple[Path, dict]]:
    d = _record_dir(gate, cwd)
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        out.append((path, data))
    return out


def select(gate: str, anchor: str, cwd: Path | None = None) -> dict | None:
    """Among `gate`'s records admissible for `anchor`, the one whose sha
    is newest by ancestry. The anchor is always an explicit argument
    (CM-6)."""
    cwd = cwd or ROOT
    candidates: list[dict] = []
    for _path, record in _load_records(gate, cwd):
        if record.get("gate") != gate:
            continue
        ok, warning = is_admissible(record, anchor, cwd)
        if warning:
            print(warning)
        if ok:
            candidates.append(record)
    if not candidates:
        return None
    newest = candidates[0]
    for candidate in candidates[1:]:
        if fence_mod.is_ancestor(cwd, newest["sha"], candidate["sha"]):
            newest = candidate
    return newest


# G-E2 (WR-PROOF-2:pack-docker-live): the live compose node, PASSED in a host-docker run record
# admissible for the head. One reading, two readers: the G-E2 target (tests/pins/e_packs) and the
# fence's landing rule L1 (`fence.host_docker_at_landing`).
LIVE_COMPOSE_NODE = (
    "packages/trestle-packs/tests/test_docker_integration.py::test_stack_runner_live_compose"
)
NO_HOST_DOCKER_RECORD = "no host-docker record admissible for HEAD"


def host_docker_problem(anchor: str, cwd: Path | None = None) -> str | None:
    """Why the host-docker evidence for `anchor` does not hold (None when it does): no admissible
    record, a record that is not a passing run, or the live compose node not PASSED in it."""
    record = select("host-docker", anchor, cwd=cwd)
    if record is None:
        return NO_HOST_DOCKER_RECORD
    if not (record["mode"] == "run" and record["status"] == "PASSED"):
        return (
            f"host-docker record for HEAD is {record['mode']}/{record['status']}, not a passing run"
        )
    passed = [
        r
        for r in record["results"]
        if r.get("nodeid") == LIVE_COMPOSE_NODE and r.get("outcome") == "PASSED"
    ]
    if not passed:
        return "the live compose node is not PASSED in the record"
    return None


def host_docker_pending(problem: str | None, event: str | None = None) -> bool:
    """Owner decision (2026-10-02, FINAL-REPORT section 3 item 4): on a pull_request run, a head
    with no host-docker record YET is pending, not failing, so the jobs that need the aggregate
    `test` (proof-ledger) and the CK drills run before the host session. Only that one problem is
    pending: a record that exists and fails is a failure on every run. The record is still
    required where it is enforced: at landing (fence rule L1) and at a checkpoint (CM-6).
    `event` is GitHub's `GITHUB_EVENT_NAME` (read from the environment when omitted)."""
    event = os.environ.get("GITHUB_EVENT_NAME", "") if event is None else event
    return problem == NO_HOST_DOCKER_RECORD and event == "pull_request"


def paired_docker(proc_record: dict, cwd: Path | None = None) -> dict | None:
    """A host-docker record pairs with a host-proc record only at the
    same sha (one role-2 run wrote both)."""
    cwd = cwd or ROOT
    for _path, record in _load_records("host-docker", cwd):
        if record.get("sha") == proc_record.get("sha"):
            return record
    return None


def pass_set_violations(record: dict, labels: dict[str, dict] | None = None) -> list[str]:
    """CM-6's role-2 pass set: no FAILED/ERROR/XPASS; SKIPPED only for a
    node registered `gated_on`, `both_variant` or `na(...)`."""
    labels = labels or {}
    violations = []
    for result in record.get("results", []):
        outcome = result.get("outcome")
        nodeid = result.get("nodeid")
        if outcome in ("FAILED", "ERROR", "XPASS"):
            violations.append(f"{nodeid}: {outcome}")
        elif outcome == "SKIPPED":
            node_labels = result.get("labels", [])
            allowed = any(
                labels.get(lbl, {}).get("posture") in ("gated_on", "both_variant")
                or labels.get(lbl, {}).get("posture") == "na"  # C.9 spelling (reason field)
                or str(labels.get(lbl, {}).get("posture", "")).startswith("na(")
                for lbl in node_labels
            )
            if not allowed:
                violations.append(f"{nodeid}: SKIPPED without a gated_on/both_variant/na label")
    return violations
