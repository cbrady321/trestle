"""J-SINGLE (b): the recorder-based one-vertex audit plugin (L.SV-5.14; TM-B2-8
`admission-audit-hook`).

    pytest tests/single tests/proof/spine tests/proof/suites -q \
        -p tests.proof.ckpt.single_vertex_audit

The plugin records admissions at their source and never globs run directories (A1c2-1). At
`pytest_configure` it points `TRESTLE_ADMISSION_AUDIT` at one session file; every in-process
admission and every `trestle serve` subprocess the MC-12 host starts inherits it, so
`write_admitted_run` (the one post-refusal write path of both `Admission.admit` and the harness
`run_tree`, MC-B2-08) appends `{run_dir, vertex_count, plugin, pid, nodeid}` per admission. At
session finish the plugin fails the session when

* any recorded `vertex_count` is not 1 (A-1 admits one-vertex roots only), or
* the audit is vacuous, each guard keyed on the recorded `nodeid` so an admission made elsewhere
  in the session never satisfies it (A1c3-5): the file holds no `spine_leaf` admission from a pid
  other than the pytest process (the MCP-host path never reached the recorder); no `spine_leaf`
  admission whose nodeid path starts with `tests/proof/spine/` (the J-SV5 spine suites); or no
  admission of some MC-35 entry (`SLICE_A_WORKFLOWS`) whose nodeid path starts with
  `tests/proof/suites/` (the conformance suites).

The verdict is a pure function of the recorded lines (`verdict`), so the negatives in
`single_conditions.py` plant lines and sessions without the hook's machinery. This module matches
no default pytest pattern (DM-80); it is used only through `-p`, at the A-1 MJs from J-SV5 on and by
J-SINGLE (b), and never on a later head, where L.TR-1.1 removes the hook by design (DM-11).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

# `meta ckpt <name>` is run by the CI `ckpt` job for every module in tests/proof/ckpt: this one
# is a plugin, not a checkpoint, and names a trigger no commit carries, so that run is a no-op.
TRIGGER_MERGE = "not-a-checkpoint"
TAG = None

AUDIT_ENV = "TRESTLE_ADMISSION_AUDIT"
SPINE_FIXTURE = "spine_leaf"
SPINE_PREFIX = "tests/proof/spine/"
SUITES_PREFIX = "tests/proof/suites/"
REPORT_HEADER = "single vertex audit"

_STATE: dict[str, Any] = {}


def nodeid_path(nodeid: object) -> str:
    """The file path of a recorded `PYTEST_CURRENT_TEST` (`path::name (call)`), or `""`."""
    if not isinstance(nodeid, str):
        return ""
    return nodeid.split("::", 1)[0]


def read_lines(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """The recorded admissions and the lines that are not one (a torn or foreign line)."""
    lines: list[dict[str, Any]] = []
    bad: list[str] = []
    if not path.exists():
        return lines, bad
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            bad.append(raw)
            continue
        if not isinstance(row, dict) or not {"vertex_count", "plugin", "pid", "nodeid"} <= set(row):
            bad.append(raw)
            continue
        lines.append(row)
    return lines, bad


def verdict(
    lines: Iterable[dict[str, Any]],
    *,
    own_pid: int,
    entries: Iterable[str],
    bad_lines: Iterable[str] = (),
) -> list[str]:
    """Why the audit fails, or `[]`: the vertex counts, then the nodeid-keyed vacuity guards."""
    recorded = list(lines)
    problems = [f"unreadable audit line {line[:60]!r}" for line in bad_lines]
    if not recorded:
        problems.append("vacuous: no admission was recorded (the hook never fired)")
    for line in recorded:
        if line.get("vertex_count") != 1:
            problems.append(
                f"{line.get('plugin')}: vertex_count {line.get('vertex_count')} != 1 "
                f"({line.get('run_dir')}; {line.get('nodeid')})"
            )
    spine = [line for line in recorded if line.get("plugin") == SPINE_FIXTURE]
    if not any(line.get("pid") != own_pid for line in spine):
        problems.append(
            f"vacuous: no {SPINE_FIXTURE} admission from a process other than pytest's "
            "(the MCP-host path never reached the recorder)"
        )
    if not any(nodeid_path(line.get("nodeid")).startswith(SPINE_PREFIX) for line in spine):
        problems.append(f"vacuous: no {SPINE_FIXTURE} admission recorded under {SPINE_PREFIX}")
    for name in entries:
        if not any(
            line.get("plugin") == name and nodeid_path(line.get("nodeid")).startswith(SUITES_PREFIX)
            for line in recorded
        ):
            problems.append(f"vacuous: no {name} admission recorded under {SUITES_PREFIX}")
    return problems


def registry_entries() -> list[str]:
    """The MC-35 entries the conformance suites must admit."""
    from tests.proof.suites.workflows import SLICE_A_WORKFLOWS

    return sorted(SLICE_A_WORKFLOWS)


def pytest_configure(config: pytest.Config) -> None:
    directory = Path(tempfile.mkdtemp(prefix="trestle-vertex-audit-"))
    path = directory / "admissions.ndjson"
    path.touch()
    _STATE.update(directory=directory, path=path, previous=os.environ.get(AUDIT_ENV))
    os.environ[AUDIT_ENV] = str(path)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    path: Path | None = _STATE.get("path")
    if path is None:
        return
    lines, bad = read_lines(path)
    problems = verdict(lines, own_pid=os.getpid(), entries=registry_entries(), bad_lines=bad)
    _STATE["report"] = (len(lines), problems)
    if problems and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    previous = _STATE.get("previous")
    if previous is None:
        os.environ.pop(AUDIT_ENV, None)
    else:
        os.environ[AUDIT_ENV] = previous
    shutil.rmtree(_STATE["directory"], ignore_errors=True)
    _STATE.pop("path", None)


def pytest_terminal_summary(terminalreporter: Any) -> None:
    report = _STATE.get("report")
    if report is None:
        return
    count, problems = report
    terminalreporter.section(REPORT_HEADER)
    if problems:
        for problem in problems:
            terminalreporter.line(f"{REPORT_HEADER}: FAILED: {problem}", red=True)
    else:
        terminalreporter.line(f"{REPORT_HEADER}: ok ({count} admissions, every one vertex)")
