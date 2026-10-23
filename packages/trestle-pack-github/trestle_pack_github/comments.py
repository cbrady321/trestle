"""Review comments: one GraphQL query per PR (`gh api graphql`), bot detection, and triage.

`CommentTriage` is a seam (P-19): a company wrapper passes its own classifier to
`triage_items`. The default, `PolicyTriage`, applies `[triage]` from the policy, in this order:

1. a whole word from `not_actionable` (`nit`, `nitpick`, `optional`) -> `not_actionable`;
2. a bot's comment with `bot_comments = "ignore"` -> `not_actionable`, with `"code_change"` ->
   `code_change`;
3. a question from a human, with `clarify_on_question` -> `clarify`;
4. a whole word from `code_change` (`bug`, `error`, `fix`, `must`, `should`) -> `code_change`;
5. else `reply_only`.

A bot is GitHub's own signal (the author is a `Bot`, or the login ends in `[bot]`), or a login
or pattern from `[bots]`. No vendor names are built in (P-21).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal, Protocol

from trestle_pack_github.policy import BotPolicy, TriagePolicy
from trestle_pack_github.results import CommentItem, PrComments
from trestle_pack_github.targets import PrRef
from trestle_pack_github.transport import GhError

Action = Literal["code_change", "clarify", "reply_only", "not_actionable"]
EXCERPT_CHARS = 300

QUERY = """\
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      url
      reviewThreads(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          isResolved
          path
          line
          comments(first: 50) {
            totalCount
            nodes { author { __typename login } body url }
          }
        }
      }
      comments(last: 100) {
        pageInfo { hasPreviousPage }
        nodes { author { __typename login } body url }
      }
      reviews(last: 50) {
        nodes { author { __typename login } body url state }
      }
    }
  }
}"""


def graphql_args(ref: PrRef) -> list[str]:
    """`gh` arguments for the comments query of one PR."""
    return [
        "api", "graphql", "--hostname", ref.host,
        "-f", f"query={QUERY}",
        "-f", f"owner={ref.owner}", "-f", f"name={ref.repo}", "-F", f"number={ref.number}",
    ]  # fmt: skip


def is_bot(author: dict[str, Any] | None, bots: BotPolicy) -> bool:
    if not author:
        return False
    login = str(author.get("login") or "")
    if author.get("__typename") == "Bot" or login.lower().endswith("[bot]"):
        return True
    if login.lower() in {name.lower() for name in bots.logins}:
        return True
    return any(re.fullmatch(pattern, login) for pattern in bots.patterns)


class CommentTriage(Protocol):
    """Suggests the next step for one comment."""

    def classify(self, item: CommentItem, body: str) -> Action: ...


def _words(words: tuple[str, ...]) -> re.Pattern[str] | None:
    if not words:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


class PolicyTriage:
    """The `[triage]` rules of the policy."""

    def __init__(self, policy: TriagePolicy) -> None:
        self.policy = policy
        self._skip = _words(policy.not_actionable)
        self._change = _words(policy.code_change)

    def classify(self, item: CommentItem, body: str) -> Action:
        if self._skip is not None and self._skip.search(body):
            return "not_actionable"
        if item.is_bot and self.policy.bot_comments == "ignore":
            return "not_actionable"
        if item.is_bot and self.policy.bot_comments == "code_change":
            return "code_change"
        if not item.is_bot and self.policy.clarify_on_question and "?" in body:
            return "clarify"
        if self._change is not None and self._change.search(body):
            return "code_change"
        return "reply_only"


def _excerpt(body: str) -> str:
    text = " ".join(body.split())
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1] + "…"


def read_comments(
    target: str, payload: Any, bots: BotPolicy, *, include_resolved: bool
) -> tuple[PrComments, list[tuple[CommentItem, str]]]:
    """The PR's comments from the GraphQL answer, each with its full body, before triage."""
    pr = (((payload or {}).get("data") or {}).get("repository") or {}).get("pullRequest")
    if not isinstance(pr, dict):
        errors = (payload or {}).get("errors") if isinstance(payload, dict) else None
        why = errors[0].get("message") if isinstance(errors, list) and errors else "no such PR"
        raise GhError(f"gh api graphql: {why}")
    out = PrComments(target=target, url=str(pr.get("url") or ""))
    found: list[tuple[CommentItem, str]] = []
    threads = pr.get("reviewThreads") or {}
    for thread in threads.get("nodes") or []:
        if thread.get("isResolved") and not include_resolved:
            out.resolved_skipped += 1
            continue
        posts = (thread.get("comments") or {}).get("nodes") or []
        if not posts:
            continue
        first = posts[0]
        item = CommentItem(
            kind="thread",
            author=str((first.get("author") or {}).get("login") or "ghost"),
            is_bot=is_bot(first.get("author"), bots),
            action="reply_only",
            excerpt="",
            url=str(first.get("url") or ""),
            path=str(thread.get("path") or ""),
            line=thread.get("line") if isinstance(thread.get("line"), int) else None,
            replies=max(0, int((thread.get("comments") or {}).get("totalCount") or 1) - 1),
        )
        found.append((item, str(first.get("body") or "")))
    for kind, key in (("comment", "comments"), ("review", "reviews")):
        for node in (pr.get(key) or {}).get("nodes") or []:
            body = str(node.get("body") or "")
            if not body.strip():
                continue  # a review with no body is only its state
            item = CommentItem(
                kind=kind,  # type: ignore[arg-type]
                author=str((node.get("author") or {}).get("login") or "ghost"),
                is_bot=is_bot(node.get("author"), bots),
                action="reply_only",
                excerpt="",
                url=str(node.get("url") or ""),
            )
            found.append((item, body))
    out.truncated = bool(threads.get("pageInfo", {}).get("hasNextPage")) or bool(
        (pr.get("comments") or {}).get("pageInfo", {}).get("hasPreviousPage")
    )
    return out, found


def triage_items(
    out: PrComments,
    found: list[tuple[CommentItem, str]],
    triage: CommentTriage,
    *,
    max_items: int,
) -> None:
    """Classify each comment, keep the first `max_items` (code changes first), count the rest."""
    for item, body in found:
        item.action = triage.classify(item, body)
        item.excerpt = _excerpt(body)
    order = {"code_change": 0, "clarify": 1, "reply_only": 2, "not_actionable": 3}
    ranked = sorted((item for item, _ in found), key=lambda item: order[item.action])
    out.actions = dict(Counter(item.action for item in ranked))
    out.items = ranked[:max_items]
    out.omitted = max(0, len(ranked) - max_items)
