"""L.RB-4.4: an exit status, a report and artifacts that disagree are a contract violation, and a
by-hand rerun of the recorded invocation is identical (B3-C14, WR-VERIFY-5, WR-EVID-7, WR-EVID-10).

The adversary is a small script the tests write: it exits, reports and leaves artifacts as told,
so a task can claim a success its own evidence denies. The real `CommandPort` runs it (a real
child, stdin closed, environment built from empty); `classify` decides from exit status, report and
artifacts, never from console text. The stub is the adversary under test, so the two matrix parts
here carry no STUB label (DM-29); the by-hand clause is stub-proven
(`WR-EVID-10:by-hand-identical`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat
from trestle.workflow.ports import (
    BoundCommand,
    ExecutionClass,
    ExecutionResult,
    InRunGroup,
    Resolved,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import Lineage, NodePath, StopCause

from trestle_packs.process.command import CommandPort, scrubbed_path
from trestle_packs.toolchain.classify import (
    TEXT_MAX,
    TRUNCATION_MARKER,
    Reported,
    bound_excerpt,
    classify,
    read_report,
    reproduction,
)

ADVERSARY = """\
import json, os, sys

mode, out = sys.argv[1], sys.argv[2]
report = os.path.join(out, "report.json")
artifact = os.path.join(out, "artifact.bin")


def write(outcome, make_artifact=True):
    with open(report, "w") as handle:
        json.dump({"outcome": outcome}, handle)
    if make_artifact:
        with open(artifact, "wb") as handle:
            handle.write(b"artifact")


if mode == "exit0-report-fail":
    write("failed")
    print("everything is fine, honest")
    sys.exit(0)
if mode == "exit1-report-pass":
    write("passed")
    print("FAILED loudly")
    sys.exit(1)
if mode == "artifact-missing":
    write("passed", make_artifact=False)
    sys.exit(0)
if mode == "exit0-no-report":
    sys.exit(0)
if mode == "agree-pass":
    write("passed")
    print("ok")
    sys.exit(0)
if mode == "agree-fail":
    write("failed")
    print("boom")
    sys.exit(1)
if mode == "crash-no-report":
    sys.exit(3)
if mode == "chatty":
    write("passed")
    for i in range(400):
        print("console line", i)
    sys.exit(0)
"""

REPORT, ARTIFACT = "report.json", "artifact.bin"


class NeverCancel:
    requested = False

    def cause(self) -> StopCause | None:
        return None

    def wait(self, timeout: timedelta) -> bool:
        return False


TICKET = AttemptTicket(
    lineage=Lineage("r_toolchain_0001", NodePath(("task",))),
    effect="task",
    facet=EffectFacetClass.EVENT,
    attempt=1,
    repeat=Repeat.SAFE,
    lifetime=Lifetime.RUN,
    release=InRunGroup(),
    remedy=None,
)


def command(tmp_path: Path, mode: str) -> tuple[BoundCommand, Path]:
    script = tmp_path / "adversary.py"
    script.write_text(f"#!{sys.executable}\n{ADVERSARY}", encoding="utf-8")
    script.chmod(0o755)
    out = tmp_path / f"out-{mode}"
    out.mkdir()
    bound = BoundCommand(
        task="adversary",
        argv=(str(script), mode, str(out)),
        environment={},
        resolved=Resolved(str(script), "1", "p", "a"),
        reports_tests=False,
    )
    return bound, out


def run(bound: BoundCommand) -> ExecutionResult:
    until = datetime.now(UTC) + timedelta(seconds=60)
    _, result = CommandPort().run(bound, TICKET, NeverCancel(), until)
    assert result is not None
    return result


def decide(result: ExecutionResult, out: Path, report_expected: bool = True) -> ExecutionResult:
    missing = () if (out / ARTIFACT).exists() else (ARTIFACT,)
    return classify(
        result,
        reported=read_report(out / REPORT),
        report_expected=report_expected,
        missing_artifacts=missing,
    )


def klass(result: ExecutionResult) -> ExecutionClass:
    return ExecutionClass(getattr(result.classification, "value", result.classification))


@pytest.mark.proves("WR-VERIFY-5", "B4.5", "B", "B", "STUB", "CI")
@pytest.mark.proves("WR-VERIFY-5", "B9.1", "B", "B", "STUB", "CI")
@pytest.mark.parametrize("mode", ["exit0-report-fail", "exit1-report-pass", "artifact-missing"])
def test_disagreement_is_contract_violation(mode: str, tmp_path: Path) -> None:
    bound, out = command(tmp_path, mode)
    raw = run(bound)
    result = decide(raw, out)
    assert klass(result) is ExecutionClass.CONTRACT_VIOLATION
    assert result.code is None  # never an invented code (B3-E1)
    assert result.recorded.passed is False  # never passed: the node ends FAILED through J-6
    assert result.exit_status == raw.exit_status  # the status is the tool's own, kept


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("agree-pass", ExecutionClass.PASSED),
        ("agree-fail", ExecutionClass.FAILED),
        ("crash-no-report", ExecutionClass.FAILED),
        ("exit0-no-report", ExecutionClass.CONTRACT_VIOLATION),  # success with no report it owes
    ],
)
def test_agreement_is_passed_or_failed_and_a_missing_report_on_success_is_a_violation(
    mode: str, expected: ExecutionClass, tmp_path: Path
) -> None:
    bound, out = command(tmp_path, mode)
    result = decide(run(bound), out)
    assert klass(result) is expected
    assert result.code is None


def test_console_text_never_decides(tmp_path: Path) -> None:
    # "honest" console over a failed report, "FAILED loudly" over a passed one: the console is
    # evidence and is not read
    for mode, said in (("exit0-report-fail", "honest"), ("exit1-report-pass", "FAILED")):
        bound, out = command(tmp_path, mode)
        raw = run(bound)
        assert said in raw.excerpt
        assert klass(decide(raw, out)) is ExecutionClass.CONTRACT_VIOLATION


def test_an_interrupted_result_is_left_as_the_port_recorded_it(tmp_path: Path) -> None:
    bound, out = command(tmp_path, "agree-pass")
    raw = run(bound)
    interrupted = ExecutionResult(
        raw.exit_status, ExecutionClass.INTERRUPTED, None, (), "execution.cancelled", raw.excerpt
    )
    assert decide(interrupted, out) is interrupted


def test_a_test_selector_without_counts_is_a_violation_and_post_start_provisioning_is_one() -> None:
    plain = ExecutionResult(0, ExecutionClass.PASSED, None, (), None, "")
    assert klass(classify(plain, counts_required=True)) is ExecutionClass.CONTRACT_VIOLATION
    assert klass(classify(plain)) is ExecutionClass.PASSED
    provisioned = classify(plain, self_provisioned=True)
    assert klass(provisioned) is ExecutionClass.CONTRACT_VIOLATION
    assert provisioned.code == "execution.toolchain_missing"  # B3-E5: the only code this sets


def test_excerpt_bounded_by_text_max(tmp_path: Path) -> None:
    bound, out = command(tmp_path, "chatty")
    result = decide(run(bound), out)
    assert klass(result) is ExecutionClass.PASSED
    assert len(result.excerpt.encode()) <= TEXT_MAX
    long = "line\n" * 500 + "the newest line"
    cut = bound_excerpt(long)
    assert len(cut.encode()) <= TEXT_MAX
    assert cut.startswith(TRUNCATION_MARKER) and cut.endswith("the newest line")
    assert bound_excerpt("short") == "short"  # a text that fits is untouched
    # a multi-byte tail is cut on a character boundary, never raised
    wide = bound_excerpt("é" * 2000)
    assert len(wide.encode()) <= TEXT_MAX and wide.startswith(TRUNCATION_MARKER)
    assert len(bound_excerpt("x" * (TEXT_MAX + 1)).encode()) <= TEXT_MAX
    exact = "y" * TEXT_MAX
    assert bound_excerpt(exact) == exact


@pytest.mark.proves("WR-EVID-10", "WR-EVID-10:by-hand-identical", "B", "B", "STUB", "CI")
@pytest.mark.parametrize("mode", ["exit0-report-fail", "exit1-report-pass", "agree-pass"])
def test_by_hand_invocation_identical(mode: str, tmp_path: Path) -> None:
    bound, out = command(tmp_path, mode)
    port_result = decide(run(bound), out)
    port_report = json.loads((out / REPORT).read_text())
    (out / REPORT).unlink()
    (out / ARTIFACT).unlink(missing_ok=True)
    # the recorded argv and environment (the port adds only its scrubbed PATH), rerun by hand
    environment = {**bound.environment, "PATH": scrubbed_path(bound.resolved.executable)}
    by_hand = subprocess.run(  # noqa: S603 - the recorded absolute argv is the point
        reproduction(bound.argv, environment),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    hand_result = ExecutionResult(
        by_hand.returncode,
        ExecutionClass.PASSED if by_hand.returncode == 0 else ExecutionClass.FAILED,
        None,
        (),
        None,
        by_hand.stdout,
    )
    hand_result = decide(hand_result, out)
    assert by_hand.returncode == port_result.exit_status
    assert klass(hand_result) is klass(port_result)
    assert json.loads((out / REPORT).read_text()) == port_report
    assert (out / ARTIFACT).exists()


def test_reproduction_is_env_i_argv_exactly_as_recorded() -> None:
    line = reproduction(("/abs/tool", "--flag", "a b"), {"Z": "1", "A": "two words"})
    assert line == ("/usr/bin/env", "-i", "A=two words", "Z=1", "/abs/tool", "--flag", "a b")


def test_read_report_reads_json_and_junit_and_nothing_else(tmp_path: Path) -> None:
    def write(name: str, text: str) -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    assert read_report(write("a.json", '{"outcome": "passed"}')) is Reported.PASSED
    assert read_report(write("b.json", '{"outcome": "failed"}')) is Reported.FAILED
    assert read_report(write("c.json", '{"outcome": "maybe"}')) is None
    assert read_report(write("d.json", "[1, 2]")) is None
    ok = '<testsuite tests="2" failures="0" errors="0"/>'
    bad = '<testsuites><testsuite tests="2" failures="0" errors="1"/></testsuites>'
    assert read_report(write("e.xml", ok)) is Reported.PASSED
    assert read_report(write("f.xml", bad)) is Reported.FAILED
    assert read_report(write("g.xml", "<broken")) is None
    assert read_report(write("h.xml", "<other/>")) is None
    assert read_report(tmp_path / "missing.json") is None
