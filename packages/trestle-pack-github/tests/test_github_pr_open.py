"""`github_pr_open`, called with `FakeContext` over the fake gh CLI and the host's git, on a
checkout under `tmp_path` whose `origin` is a local bare repository: no test reaches GitHub."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gh_wrappers import (
    call_failed,
    checkout,
    context,
    fake_gh,
    git,
    load_wrapper,
    policy_file,
    pr,
)
from trestle.plugin import Blocked
from trestle.plugin.testing import FakeContext, call_plugin

# K-3/K-4: these plugins take and return typed records (skipped if they are declined).
pytestmark = pytest.mark.typed_records


def call(tmp_path: Path, name: str, args: dict[str, Any]) -> tuple[Any, FakeContext]:
    """Call the wrapper `name` with `FakeContext`: its answer as an agent reads it, and the
    context (commands run, artifacts attached)."""
    ctx = context(tmp_path)
    return call_plugin(getattr(load_wrapper(name), name), args, ctx=ctx), ctx


def _change(work: Path, name: str = "src/a.py", text: str = "x = 1\n") -> None:
    path = work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _remote_heads(origin: Path) -> set[str]:
    out = git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return set(out.splitlines())


def _open(tmp_path: Path, work: Path, **args: object) -> dict:
    answer, _ = call(tmp_path, "github_pr_open", {"workdir": str(work), "title": "Add x", **args})
    assert isinstance(answer, dict)
    return answer


def _refused(tmp_path: Path, work: Path, **args: object) -> Blocked:
    """Open where a guard must refuse: the `Blocked` raised (its code is the refusal's)."""
    with pytest.raises(Blocked) as raised:
        _open(tmp_path, work, **args)
    assert raised.value.resend == "will_not_succeed"
    return raised.value


def test_commits_only_the_paths_pushes_and_creates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_gh(tmp_path, monkeypatch, repo="octo/app", default_branch="main")
    policy_file(tmp_path, monkeypatch, '[open]\nlabels = ["bot-made"]\n')
    work, origin = checkout(tmp_path)
    _change(work)
    _change(work, "notes.txt", "scratch\n")  # not listed: stays uncommitted
    _change(work, ".github/pull_request_template.md", "## What\n")
    git(work, "add", ".github")
    git(work, "commit", "-qm", "template")
    answer = _open(tmp_path, work, paths=["src"], commit_message="Add x module", labels=["ready"])
    assert answer["ok"] is True, answer
    assert answer["action"] == "created" and answer["number"] == 1
    assert answer["url"] == "https://github.com/octo/app/pull/1"
    assert answer["committed"] == ["src/a.py"] and answer["pushed"] is True
    assert git(work, "log", "-1", "--format=%s") == "Add x module"
    assert "notes.txt" in git(work, "status", "--porcelain")
    assert "feat/x" in _remote_heads(origin)
    created = fake.state["prs"]["octo/app#1"]
    assert created["headRefName"] == "feat/x" and created["baseRefName"] == "main"
    assert created["body"] == "## What\n" and created["labels"] == ["bot-made", "ready"]
    assert any(c.startswith("pr create -R github.com/octo/app --head feat/x") for c in fake.calls)


@pytest.mark.parametrize(
    ("setup", "args", "code"),
    [
        ("main", {}, "github.default_branch"),
        ("detached", {}, "github.detached_head"),
        ("feat/x", {"base": "feat/x"}, "github.head_is_base"),
        ("feat/x", {"paths": ["src"]}, "github.commit_message_missing"),
        ("feat/x", {"commit_message": "m"}, "github.paths_missing"),
        ("feat/x", {"paths": ["../elsewhere"], "commit_message": "m"}, "github.path_outside_repo"),
        ("feat/x", {"title": " "}, "github.title_missing"),
    ],
)
def test_a_guard_refuses_before_anything_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setup: str, args: dict, code: str
) -> None:
    fake = fake_gh(tmp_path, monkeypatch, repo="octo/app")
    work, origin = checkout(tmp_path, "main" if setup == "detached" else setup)
    if setup == "detached":
        git(work, "switch", "-q", "--detach")
    _change(work)
    before = git(work, "rev-parse", "HEAD")
    refusal = _refused(tmp_path, work, **args)
    assert refusal.code == code and refusal.human_action
    assert git(work, "rev-parse", "HEAD") == before
    assert _remote_heads(origin) == {"main"}
    assert not any(c.startswith("pr create") for c in fake.calls)


def test_policy_patterns_refuse_a_branch_or_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, repo="octo/app")
    work, _ = checkout(tmp_path, "wip")
    policy_file(tmp_path, monkeypatch, '[open]\nbranch_pattern = "(feat|fix)/.+"\n')
    refusal = _refused(tmp_path, work)
    assert refusal.code == "github.branch_pattern"
    assert "git branch -m" in refusal.human_action
    git(work, "branch", "-m", "fix/y")
    policy_file(tmp_path, monkeypatch, '[open]\ntitle_pattern = "[A-Z]+-[0-9]+: .+"\n')
    assert _refused(tmp_path, work).code == "github.title_pattern"


def test_something_staged_outside_the_paths_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, repo="octo/app")
    work, _ = checkout(tmp_path)
    _change(work)
    _change(work, "secret.env", "TOKEN=x\n")
    git(work, "add", "secret.env")
    refusal = _refused(tmp_path, work, paths=["src/a.py"], commit_message="m")
    assert refusal.code == "github.index_not_clean"
    assert "secret.env" in refusal.human_action
    assert git(work, "diff", "--cached", "--name-only") == "secret.env"  # untouched


def test_an_open_pr_is_edited_only_when_asked_and_only_if_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = pr(author="octo", body="kept")
    fake = fake_gh(tmp_path, monkeypatch, repo="octo/app", prs={"octo/app#7": mine})
    work, origin = checkout(tmp_path)
    assert _refused(tmp_path, work).code == "github.pr_exists"
    assert _remote_heads(origin) == {"main"}

    answer = _open(tmp_path, work, update_existing=True, title="Add x, v2")
    assert answer["ok"] is True and answer["action"] == "updated", answer
    assert answer["url"] == "https://github.com/octo/app/pull/7" and answer["number"] == 7
    assert "feat/x" in _remote_heads(origin)
    edited = fake.state["prs"]["octo/app#7"]
    assert edited["title"] == "Add x, v2" and edited["body"] == "kept"  # no body: left as it was
    assert "api user --hostname github.com --jq .login" in fake.calls


def test_someone_elses_pr_is_not_edited_unless_the_policy_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, repo="octo/app", prs={"octo/app#7": pr(author="ana")})
    work, origin = checkout(tmp_path)
    refusal = _refused(tmp_path, work, update_existing=True)
    assert refusal.code == "github.pr_not_yours" and "ana" in refusal.human_action
    assert _remote_heads(origin) == {"main"}
    policy_file(tmp_path, monkeypatch, "[open]\nedit_any_pr = true\n")
    assert _open(tmp_path, work, update_existing=True)["action"] == "updated"


def test_a_failed_push_raises_failed_carrying_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_gh(tmp_path, monkeypatch, repo="octo/app")
    policy_file(tmp_path, monkeypatch, '[open]\nremote = "nowhere"\n')
    work, _ = checkout(tmp_path)
    failed, answer, _ = call_failed(
        tmp_path, "github_pr_open", {"workdir": str(work), "title": "Add x"}
    )
    assert failed.code == "open_failed" and failed.detail.startswith("git push: exit")
    assert answer["ok"] is False and answer["action"] == "failed" and answer["pushed"] is False
    assert answer["error"] == failed.detail
    assert not any(c.startswith("pr create") for c in fake.calls)


def test_not_a_checkout_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(tmp_path, monkeypatch)
    (tmp_path / "plain").mkdir()
    refusal = _refused(tmp_path, tmp_path / "plain")
    assert refusal.code == "github.not_a_checkout"


def test_no_login_is_blocked_before_any_git_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, login=None)
    work, _ = checkout(tmp_path)
    with pytest.raises(Blocked) as raised:
        _open(tmp_path, work)
    assert raised.value.code == "github.gh_auth"
