"""R3: `trestle.plugin.testing`: a plugin is unit tested with `FakeContext` and `call_plugin`,
with no server, run id or evidence; `exec` runs for real or is scripted; and the module stays out
of what a published plugin may import."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from trestle.child.validate import forbidden_import_error
from trestle.plugin import Context, ExecResult, trestle
from trestle.plugin.testing import FakeContext, call_plugin, exec_result
from trestle.plugin.testing import trestle_ctx as trestle_ctx  # the fixture, as a conftest gets it


@dataclass
class Report:
    state: str
    checked: int


@trestle
def check_pr(ctx: Context, pr: int, rounds: int = 2) -> Report:
    state = "unknown"
    for i in range(rounds):
        ctx.progress(f"round {i}", fraction=(i + 1) / rounds)
        result = ctx.exec(["gh", "pr", "view", str(pr)], timeout=30)
        if result.ok:
            state = result.stdout.strip()
        status = ctx.tmp / "status.md"
        status.write_text(f"round {i}: {state}\n", encoding="utf-8")
        ctx.attach(status, name="status.md")
        ctx.event("round", i=i, state=state)
    return Report(state=state, checked=rounds)


def test_a_plugin_runs_against_a_scripted_exec(tmp_path: Path) -> None:
    def gh(argv: list[str], **kwargs: object) -> ExecResult:
        assert kwargs["timeout"] == 30
        return exec_result(argv, stdout="MERGED\n")

    ctx = FakeContext(tmp_path, exec_handler=gh)
    answer = call_plugin(check_pr, {"pr": 7, "rounds": 3}, ctx=ctx)
    assert answer == {"state": "MERGED", "checked": 3}
    assert [c.argv for c in ctx.exec_calls] == [("gh", "pr", "view", "7")] * 3
    assert ctx.events == [("round", {"i": i, "state": "MERGED"}) for i in range(3)]
    assert ctx.progress_updates[-1] == ("round 2", 1.0)
    assert ctx.artifacts == {"status.md": b"round 2: MERGED\n"}
    chain = [a.supersedes for a in ctx.attachments]
    assert chain[0] is None and chain[1:] == [a.artifact_id for a in ctx.attachments[:-1]]


def test_the_fixture_and_a_real_command(trestle_ctx: FakeContext) -> None:
    result = trestle_ctx.exec([sys.executable, "-c", "print('real')"])
    assert result.ok and result.stdout == "real\n"
    assert result.artifact and b"real" in trestle_ctx.artifacts["exec.log"]
    assert trestle_ctx.logs[-1].startswith("exec ")
    assert 290 < trestle_ctx.remaining() <= 300


def test_cancel_and_deadline_are_settable(tmp_path: Path) -> None:
    ctx = FakeContext(tmp_path, deadline=timedelta(seconds=0))
    assert ctx.remaining() == 0.0
    assert ctx.exec([sys.executable, "-c", "print(1)"]).timed_out
    ctx = FakeContext(tmp_path)
    ctx.cancelled = True
    assert ctx.exec([sys.executable, "-c", "import time; time.sleep(30)"]).cancelled


def test_the_fake_refuses_what_the_runtime_refuses(tmp_path: Path) -> None:
    ctx = FakeContext(tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        ctx.event("log", x=1)
    with pytest.raises(NotImplementedError):
        ctx.exec(["gh"], credentials=["gh"])
    with pytest.raises(TypeError):
        ctx.exec("gh pr view")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="under work"):
        ctx.attach(tmp_path / "elsewhere.txt", name="x")
    with pytest.raises(TypeError, match="missing required arg"):
        call_plugin(check_pr, {}, ctx=ctx)
    with pytest.raises(TypeError, match="not marked"):
        call_plugin(lambda ctx: None, ctx=ctx)


def test_published_plugins_cannot_import_the_testing_module() -> None:
    assert forbidden_import_error("import trestle.plugin.testing\n") is not None
    assert forbidden_import_error("from trestle.plugin import testing\n") is not None
    assert forbidden_import_error("from trestle.plugin import ExecResult, trestle\n") is None
