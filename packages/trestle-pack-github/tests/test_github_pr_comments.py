"""`github_pr_comments`, called with `FakeContext` over the fake gh CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from gh_wrappers import call, fake_gh, policy_file, pr


def _who(login: str, bot: bool = False) -> dict[str, str]:
    return {"__typename": "Bot" if bot else "User", "login": login}


def _thread(author: dict[str, str], body: str, *, resolved: bool = False) -> dict[str, object]:
    return {
        "isResolved": resolved, "path": "app.py", "line": 7,
        "comments": {"totalCount": 2, "nodes": [{"author": author, "body": body, "url": "u"}]},
    }  # fmt: skip


COMMENTS = {
    "reviewThreads": {
        "pageInfo": {"hasNextPage": False},
        "nodes": [
            _thread(_who("ana"), "This should handle None, it is a bug."),
            _thread(_who("ben"), "Why is this a list?"),
            _thread(_who("cy"), "nit: trailing space"),
            _thread(_who("dee"), "fix this", resolved=True),
            _thread(_who("github-actions", bot=True), "Coverage dropped by 2%"),
        ],
    },
    "comments": {
        "pageInfo": {"hasPreviousPage": False},
        "nodes": [{"author": _who("acme-review"), "body": "Consider renaming", "url": "c"}],
    },
    "reviews": {
        "nodes": [
            {"author": _who("ana"), "body": "", "state": "APPROVED"},
            {"author": _who("eve"), "body": "Looks good", "state": "COMMENTED"},
        ]
    },  # fmt: skip
}


def test_open_comments_are_triaged_by_the_default_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr(comments=COMMENTS)})
    answer, ctx = call(tmp_path, "github_pr_comments", {"targets": ["octo/app#12"]})
    assert answer["ok"] is True, answer
    found = answer["prs"][0]
    actions = {item["author"]: item["action"] for item in found["items"]}
    assert actions == {
        "ana": "code_change",
        "ben": "clarify",
        "cy": "not_actionable",
        "github-actions": "reply_only",
        "acme-review": "reply_only",
        "eve": "reply_only",
    }
    assert found["resolved_skipped"] == 1 and found["items"][0]["action"] == "code_change"
    bots = {item["author"] for item in found["items"] if item["is_bot"]}
    assert bots == {"github-actions"}
    assert found["items"][0]["path"] == "app.py" and found["items"][0]["replies"] == 1
    assert answer["counts"]["code_change"] == 1
    assert any(c.startswith("api graphql --hostname github.com -f query=") for c in fake.calls)
    full = json.loads(ctx.artifacts["github-pr-comments.json"])
    assert len(full["octo/app#12"]) == 6


def test_policy_names_company_bots_and_their_treatment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_gh(tmp_path, monkeypatch, prs={"octo/app#12": pr(comments=COMMENTS)})
    policy_file(tmp_path, monkeypatch, '[bots]\npatterns = ["acme-.*"]\n'
                '[triage]\nbot_comments = "code_change"\n')  # fmt: skip
    answer, _ = call(tmp_path, "github_pr_comments", {"targets": ["octo/app#12"], "max_items": 2})
    found = answer["prs"][0]
    assert [item["author"] for item in found["items"]] == ["ana", "github-actions"]
    assert found["omitted"] == 4 and found["actions"]["code_change"] == 3


def test_an_unknown_pr_is_an_error_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(tmp_path, monkeypatch, prs={})
    answer, _ = call(tmp_path, "github_pr_comments", {"targets": ["octo/app#5"]})
    assert answer["ok"] is False and answer["counts"]["error"] == 1
    assert "Could not resolve" in answer["prs"][0]["error"]
