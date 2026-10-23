"""The answers the github plugins return. Each is bounded: a top-level `ok`, counts, one line per
pull request or failure, and the policy that was in force. The full data is attached."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from trestle_pack_github.policy import GithubPolicy, PolicyEcho


@dataclass
class PolicyReport:
    """`github_policy`'s answer: the effective policy and where it came from."""

    source: str
    sha256: str | None
    policy: GithubPolicy
    host: str


@dataclass
class CheckLine:
    name: str
    bucket: str
    link: str = ""


@dataclass
class PrStatus:
    """One pull request: its state, and the verdict on the checks that count.

    `verdict`: `passed`; `failed` (a counted check failed or was cancelled, or a check the policy
    adds never reported); `pending`; `no_checks` (nothing counted was reported: never passed
    unless the policy says `no_checks = "pass"`); `error` (the PR could not be read: `error`)."""

    target: str
    verdict: Literal["passed", "failed", "pending", "no_checks", "error"] = "error"
    url: str = ""
    title: str = ""
    state: str = ""
    draft: bool = False
    head: str = ""
    head_sha: str = ""
    review_decision: str = ""
    mergeable: str = ""
    counted: Literal["required", "all"] = "required"
    buckets: dict[str, int] = field(default_factory=dict)
    failing: list[CheckLine] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    summary: str = ""
    error: str | None = None


@dataclass
class StatusReport:
    """`github_pr_status`'s answer: `ok` only when every PR's verdict is `passed`."""

    ok: bool
    summary: str
    counts: dict[str, int]
    prs: list[PrStatus]
    waited_s: float
    policy: PolicyEcho


@dataclass
class CommentItem:
    """One review thread, PR comment or review body, with the suggested next step."""

    kind: Literal["thread", "comment", "review"]
    author: str
    is_bot: bool
    action: Literal["code_change", "clarify", "reply_only", "not_actionable"]
    excerpt: str
    url: str = ""
    path: str = ""
    line: int | None = None
    replies: int = 0


@dataclass
class PrComments:
    target: str
    url: str = ""
    items: list[CommentItem] = field(default_factory=list)
    actions: dict[str, int] = field(default_factory=dict)
    resolved_skipped: int = 0
    omitted: int = 0
    truncated: bool = False
    error: str | None = None


@dataclass
class CommentsReport:
    """`github_pr_comments`'s answer: `ok` when every PR was read."""

    ok: bool
    summary: str
    counts: dict[str, int]
    prs: list[PrComments]
    policy: PolicyEcho


@dataclass
class Refusal:
    """A policy guard refused the request before anything changed. The agent can fix it by
    changing the request; sending the same request again will not succeed."""

    code: str
    message: str
    resend: Literal["will_not_succeed"] = "will_not_succeed"


@dataclass
class OpenResult:
    """`github_pr_open`'s answer.

    `action`: `created` or `updated` (ok); `refused` (a guard said no and nothing was changed:
    `refusal`); `failed` (a command failed: `error`, and `pushed` says whether the push
    happened)."""

    ok: bool
    action: Literal["created", "updated", "refused", "failed"]
    summary: str
    branch: str = ""
    base: str = ""
    url: str = ""
    number: int | None = None
    commit: str = ""
    committed: list[str] = field(default_factory=list)
    pushed: bool = False
    refusal: Refusal | None = None
    error: str | None = None
    policy: PolicyEcho | None = None
