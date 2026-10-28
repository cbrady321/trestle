"""One-call chains, change 1: a plain plugin raises `Failed(code, detail, result=)` and its run's
answer reads `failed`, the code namespaced `plugin.<code>`; and every reader that orders, waits on
or lists runs by success judges the verdict (`answer.outcome`), not `state`, for `Failed` and
`Blocked` alike."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from trestle.common import codes
from trestle.common.types import RunView
from trestle.plugin import Failed
from trestle.plugin.testing import FakeContext, call_plugin
from trestle.server import answer as answer_mod
from trestle.server import chain
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import Kernel, create_kernel
from trestle.server.project import _did_not_pass
from trestle.server.recovery import find_run_dir

CHECKED_PLUGIN = """
from dataclasses import dataclass

from trestle.plugin import Blocked, Context, Failed, trestle


@dataclass
class Report:
    ok: bool
    steps: list[str]
    pad: str


@trestle(summary_fields=["ok", "steps"])
def checked(ctx: Context, mode: str = "pass", size: int = 0) -> Report:
    report = Report(mode == "pass", ["lint"], "x" * size)
    if mode == "pass":
        return report
    if mode == "blocked":
        raise Blocked("tool_missing", "Install ruff, then resend.")
    if mode == "no_result":
        raise Failed("lint_failed", "2 findings in /tmp/x.py")
    if mode == "unencodable":
        raise Failed("lint_failed", "2 findings", result={"ok": False, "bad": object()})
    raise Failed("lint_failed", "2 findings in app.py", result=report)
"""

# K-3/K-4: the plugin under test returns a typed record (skipped if they are declined).
pytestmark = pytest.mark.typed_records

RUN_WAIT_MS = 20_000
WAIT_S = 10.0


def _kernel(tmp_path: Path) -> Kernel:
    home = tmp_path / "home"
    home.mkdir()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "checked.py").write_text(CHECKED_PLUGIN, encoding="utf-8")
    return create_kernel(home=home, plugin_dirs=[plugins], skip_recovery=True)


def _run(kernel: Kernel, mode: str, **args: Any) -> RunView:
    view = kernel.control.run(
        "checked", {"mode": mode, **args}, wait_ms=RUN_WAIT_MS, completion="terminal"
    )
    assert isinstance(view, RunView), view
    return view


def _evidence(kernel: Kernel, run_id: str) -> Path:
    run_dir = find_run_dir(kernel.home, run_id)
    assert run_dir is not None
    return run_dir / "evidence"


def test_failed_with_a_result_answers_failed_with_the_result(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    view = _run(kernel, "fail")
    assert view.state == "succeeded"
    assert view.answer is not None and view.answer["outcome"] == "failed"
    primary = view.answer["primary"]
    assert primary["node_class"] == "failed" and primary["code"] == "plugin.lint_failed"
    assert primary["human_action"] is None
    # one verdict per run: outcome.class agrees with the answer, with its code
    assert view.outcome is not None
    assert view.outcome["class"] == "failed" and view.outcome["code"] == "plugin.lint_failed"
    # the result is recorded as a return would be, summary_fields applied
    assert view.summary is not None
    assert view.summary["ok"] is False and view.summary["steps"] == ["lint"]
    record = json.loads((_evidence(kernel, view.run_id) / "failed.json").read_text())
    assert record == {"code": "plugin.lint_failed", "detail": "2 findings in app.py"}


def test_failed_without_a_result_has_none(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    view = _run(kernel, "no_result")
    assert view.answer is not None and view.answer["outcome"] == "failed"
    assert view.summary is None and view.result_bytes is None
    record = json.loads((_evidence(kernel, view.run_id) / "failed.json").read_text())
    assert record["code"] == "plugin.lint_failed" and record["detail"].startswith("2 findings")


def test_a_result_that_cannot_be_encoded_keeps_the_verdict(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    view = _run(kernel, "unencodable")
    assert view.state == "succeeded"
    assert view.answer is not None and view.answer["outcome"] == "failed"
    assert view.summary is None
    assert view.limits_exceeded is not None
    assert any(
        m.get("stream") == "result" and m.get("limit") == "unencodable"
        for m in view.limits_exceeded
    ), view.limits_exceeded


def test_a_result_too_large_keeps_the_verdict(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("TRESTLE_TEST_LIMITS", "1")  # a 512 KiB result cap
    kernel = _kernel(tmp_path)
    view = _run(kernel, "fail", size=600 * 1024)
    assert view.answer is not None and view.answer["outcome"] == "failed"
    assert view.truncated is True
    assert (_evidence(kernel, view.run_id) / "result.state").read_text() == "too_large"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("Bad Code", "x"), "lower-case"),
        (("", "x"), "lower-case"),
        (("lint failed", "x"), "lower-case"),
        (("lint_failed", "  "), "detail"),
    ],
)
def test_failed_refuses_a_malformed_record(args: tuple[str, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Failed(*args)


def test_call_plugin_lets_failed_propagate_with_its_result(tmp_path: Path) -> None:
    namespace: dict[str, Any] = {}
    exec(compile(CHECKED_PLUGIN, "checked.py", "exec"), namespace)  # noqa: S102 - the test's own plugin
    with pytest.raises(Failed) as raised:
        call_plugin(namespace["checked"], {"mode": "fail"}, ctx=FakeContext(root=tmp_path))
    assert raised.value.code == "lint_failed"
    assert raised.value.detail == "2 findings in app.py"
    result = raised.value.result
    assert (result.ok, result.steps, result.pad) == (False, ["lint"], "")  # type: ignore[attr-defined]


def test_failed_json_is_not_read_for_an_interrupted_run(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    view = _run(kernel, "fail")
    run_dir = find_run_dir(kernel.home, view.run_id)
    assert run_dir is not None
    ledger = RunLedger.open(ledger_path(run_dir))
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text())
    restarted = answer_mod.answer_for_run(run_dir, ledger.records, "interrupted", spec)
    assert restarted.outcome.value == "execution_error"


# ---- B3: every reader judges the verdict


def _send_after(kernel: Kernel, earlier: str, **after: Any) -> RunView:
    view = kernel.control.run(
        "checked", {"mode": "pass"}, wait_ms=0, after={"run": earlier, **after}
    )
    assert isinstance(view, RunView), view
    return view


def _await_terminal(kernel: Kernel, run_id: str) -> RunView:
    deadline = time.monotonic() + WAIT_S
    while True:
        view = kernel.control.project.status(run_id)
        assert isinstance(view, RunView), view
        if view.state not in ("held", "queued", "running") or time.monotonic() > deadline:
            return view
        time.sleep(0.02)


@pytest.mark.parametrize(("mode", "verdict"), [("fail", "failed"), ("blocked", "blocked")])
def test_after_succeeded_is_unmet_behind_a_run_that_did_not_pass(
    tmp_path: Path, mode: str, verdict: str
) -> None:
    kernel = _kernel(tmp_path)
    earlier = _run(kernel, mode)
    assert earlier.state == "succeeded"
    held = _await_terminal(kernel, _send_after(kernel, earlier.run_id).run_id)
    assert held.state == "cancelled", held
    assert held.error is not None and held.error["code"] == codes.EXECUTION_AFTER_UNMET
    message = held.error["message"]
    assert chain.REASON_EARLIER_NOT_PASSED in message and f"answer {verdict}" in message
    # `when: "ended"` still releases on any ending
    ended = _await_terminal(kernel, _send_after(kernel, earlier.run_id, when="ended").run_id)
    assert ended.state == "succeeded"


def test_after_succeeded_releases_behind_a_passing_run(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    earlier = _run(kernel, "pass")
    assert _await_terminal(kernel, _send_after(kernel, earlier.run_id).run_id).state == "succeeded"


def test_a_run_without_a_persisted_answer_is_judged_by_its_state(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    earlier = _run(kernel, "fail")
    (_evidence(kernel, earlier.run_id) / "answer.json").unlink()  # a run from before B4
    assert _await_terminal(kernel, _send_after(kernel, earlier.run_id).run_id).state == "succeeded"


def test_first_failure_wakes_on_a_failed_verdict(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    failed = _run(kernel, "fail")
    passed = _run(kernel, "pass")
    views = kernel.control.await_runs(
        [passed.run_id, failed.run_id], mode="first_failure", timeout_ms=1_000
    )
    assert isinstance(views, list)
    assert any(v.answer and v.answer["outcome"] == "failed" for v in views)
    assert _did_not_pass(failed) and not _did_not_pass(passed)


def test_recent_failures_lists_failed_and_blocked_verdicts(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    failed = _run(kernel, "fail")
    blocked = _run(kernel, "blocked")
    passed = _run(kernel, "pass")
    page = kernel.control.query("recent_failures", {})
    assert isinstance(page, dict), page
    rows = {row["run_id"]: row for row in page["items"]}
    assert rows[failed.run_id]["outcome"] == "failed"
    assert rows[failed.run_id]["state"] == "succeeded"
    assert rows[blocked.run_id]["outcome"] == "blocked"
    assert passed.run_id not in rows
