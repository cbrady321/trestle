"""`github_pr_status`, called with `FakeContext` over the fake gh CLI.

Copy this file beside an adapted wrapper to test it the same way: the fake answers `gh pr view`
and `gh pr checks` with gh's exit codes and records every command it was given."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from gh_wrappers import context, fake_gh, load_wrapper, policy_file, pr
from trestle.plugin import Blocked
from trestle.plugin.testing import FakeContext, call_plugin


def call(tmp_path: Path, name: str, args: dict[str, Any]) -> tuple[Any, FakeContext]:
    """Call the wrapper `name` with `FakeContext`: its answer as an agent reads it, and the
    context (commands run, artifacts attached)."""
    ctx = context(tmp_path)
    return call_plugin(getattr(load_wrapper(name), name), args, ctx=ctx), ctx


PASS = {"name": "build", "bucket": "pass", "link": "https://ci/1"}
LINT_FAIL = {"name": "lint", "bucket": "fail", "link": "https://ci/2"}
PENDING = {"name": "build", "bucket": "pending"}


def test_required_checks_decide_and_an_optional_failure_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_gh(tmp_path, monkeypatch, prs={
        "octo/app#12": pr(checks=[[PASS, LINT_FAIL]], required=["build"]),
    })  # fmt: skip
    answer, ctx = call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    assert answer["ok"] is True, answer
    status = answer["prs"][0]
    assert status["verdict"] == "passed" and status["counted"] == "required"
    assert status["url"] == "https://github.com/octo/app/pull/12"
    assert status["review_decision"] == "REVIEW_REQUIRED"
    assert answer["policy"] == {"source": "defaults", "sha256": None}
    assert "auth status --hostname github.com" in fake.calls
    assert any(c.endswith("--required --json name") for c in fake.calls)
    assert "github-pr-status.json" in ctx.artifacts and "github-policy.json" in ctx.artifacts


def test_with_no_required_checks_every_check_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr(checks=[[PASS, LINT_FAIL]])})
    answer, _ = call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    status = answer["prs"][0]
    assert answer["ok"] is False and status["verdict"] == "failed" and status["counted"] == "all"
    assert status["failing"] == [{"name": "lint", "bucket": "fail", "link": "https://ci/2"}]
    assert "failing lint" in status["summary"]


def test_no_checks_is_its_own_verdict_unless_the_policy_passes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr(checks=[[]])})
    answer, _ = call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    assert answer["ok"] is False and answer["counts"] == {"no_checks": 1}
    policy_file(tmp_path, monkeypatch, '[checks]\nno_checks = "pass"\n')
    answer, _ = call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    assert answer["ok"] is True and answer["policy"]["sha256"]


def test_policy_add_and_drop_change_what_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, prs={
        "octo/app#12": pr(checks=[[PASS, LINT_FAIL]], required=["build", "lint"]),
    })  # fmt: skip
    policy_file(tmp_path, monkeypatch, '[checks]\ndrop = ["lint"]\nadd = ["e2e"]\n')
    answer, _ = call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    status = answer["prs"][0]
    assert status["verdict"] == "failed" and status["missing"] == ["e2e"]  # never reported


def test_wait_polls_until_the_checks_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_gh(tmp_path, monkeypatch, prs={
        "octo/app#12": pr(checks=[[PENDING], [PENDING], [PASS]], required=["build"]),
    })  # fmt: skip
    answer, ctx = call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"], "wait": True})
    assert answer["ok"] is True and answer["waited_s"] == 30.0  # two polls of 15 s
    assert sum(1 for c in fake.calls if c.endswith("--json name,bucket,state,link,workflow")) == 3
    assert ctx.progress_updates


def test_wait_gives_checks_their_grace_before_no_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr(checks=[[], [], [PASS]])})
    policy_file(tmp_path, monkeypatch, "[checks]\ngrace_s = 60\npoll_s = 15\n")
    answer, _ = call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"], "wait": True})
    assert answer["ok"] is True and answer["waited_s"] == 30.0


def test_wait_stops_at_timeout_with_the_pr_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr(checks=[[PENDING]])})
    args = {"targets": ["octo/app#12"], "wait": True, "timeout_s": 40}
    answer, _ = call(tmp_path, "github_pr_status", args)
    assert answer["ok"] is False and answer["counts"] == {"pending": 1}
    assert answer["waited_s"] == 30.0


def test_bad_and_missing_targets_are_lines_not_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr(checks=[[PASS]])})
    targets = ["octo/app#12", "octo/app#99", "not a pr", "https://gitlab.com/a/b/pull/1"]
    answer, ctx = call(tmp_path, "github_pr_status", {"targets": targets})
    assert answer["counts"] == {"passed": 1, "error": 3} and answer["ok"] is False
    errors = [p["error"] for p in answer["prs"][1:]]
    assert "gh pr view: exit 1" in errors[0]
    assert "not a pull request" in errors[1] and "GH_HOST" in errors[2]
    report = json.loads(ctx.artifacts["github-pr-status.json"])
    assert len(report["prs"]) == 4


def test_no_login_is_blocked_with_one_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, login=None)
    with pytest.raises(Blocked) as raised:
        call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    assert raised.value.code == "github.gh_auth"
    assert "gh auth login --hostname github.com" in raised.value.human_action


def test_an_expired_login_mid_run_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr()}, fail_auth_on=["pr view"])
    with pytest.raises(Blocked) as raised:
        call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    assert raised.value.code == "github.gh_auth"


def test_a_missing_gh_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(tmp_path, monkeypatch)
    monkeypatch.setenv("TRESTLE_GH_PATH", str(tmp_path / "nowhere" / "gh"))
    with pytest.raises(Blocked) as raised:
        call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    assert raised.value.code == "github.gh_missing"
    monkeypatch.delenv("TRESTLE_GH_PATH")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr("trestle_pack_github.transport.WELL_KNOWN_GH", ())
    with pytest.raises(Blocked) as raised:
        call(tmp_path, "github_pr_status", {"targets": ["octo/app#12"]})
    assert "https://cli.github.com" in raised.value.human_action


def test_enterprise_host_comes_from_gh_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr(checks=[[PASS]])})
    monkeypatch.setenv("GH_HOST", "ghe.example.com")
    answer, ctx = call(tmp_path, "github_pr_status", {
        "targets": ["https://ghe.example.com/octo/app/pull/12"],
    })  # fmt: skip
    assert "auth status --hostname ghe.example.com" in fake.calls
    assert answer["prs"][0]["url"] == "https://github.com/octo/app/pull/12"  # what gh answered
    assert all(call.kwargs["env"]["GH_HOST"] == "ghe.example.com" for call in ctx.exec_calls)
