"""Reading `gh pr view` and `gh pr checks`, and the verdict on the checks that count (P-21).

Which checks count: with `[checks] required = "branch_protection"` (the default), the ones
`gh pr checks --required` lists; when branch protection requires none, every reported check
counts, so an unprotected branch is never passed by default. With `required = "all"`, every
reported check. Then `[checks] add` joins, and `[checks] drop` leaves.

`gh pr checks` buckets each check `pass`, `fail`, `pending`, `skipping` or `cancel`; a skipped
check counts as passed (as GitHub's branch protection does), a cancelled one as failed.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from trestle.plugin import Context, ExecResult

from trestle_pack_github.policy import ChecksPolicy
from trestle_pack_github.results import CheckLine, PrStatus
from trestle_pack_github.transport import GhError, failure, json_of

VIEW_FIELDS = "url,title,state,isDraft,headRefName,headRefOid,reviewDecision,mergeable"
CHECK_FIELDS = "name,bucket,state,link,workflow"
_NONE_REPORTED = ("no checks reported", "no required checks reported")
_FAILED = {"fail", "cancel"}
MAX_LISTED = 20


def apply_view(status: PrStatus, result: ExecResult) -> None:
    """Fill the PR's state from `gh pr view --json VIEW_FIELDS`; `GhError` when it failed."""
    view = json_of(result, "gh pr view")
    status.url = str(view.get("url", status.url))
    status.title = str(view.get("title", ""))
    status.state = str(view.get("state", ""))
    status.draft = bool(view.get("isDraft", False))
    status.head = str(view.get("headRefName", ""))
    status.head_sha = str(view.get("headRefOid", ""))
    status.review_decision = str(view.get("reviewDecision") or "")
    status.mergeable = str(view.get("mergeable") or "")


def read_checks(result: ExecResult) -> list[dict[str, Any]]:
    """The checks `gh pr checks --json ...` listed. Its exit code says pending (8) or failed (1)
    even when it printed the list, so the list is read first; "no checks reported" is an empty
    list; anything else is a `GhError`."""
    try:
        listed = json.loads(result.stdout) if result.stdout.strip() else None
    except ValueError:
        listed = None
    if isinstance(listed, list):
        return [item for item in listed if isinstance(item, dict)]
    said = f"{result.stderr}\n{result.stdout}".lower()
    if not result.timed_out and any(marker in said for marker in _NONE_REPORTED):
        return []
    raise GhError(failure(result, "gh pr checks"))


def required_names(result: ExecResult | None) -> set[str] | None:
    """The names `gh pr checks --required --json name` listed; None when not asked."""
    if result is None:
        return None
    return {str(item.get("name", "")) for item in read_checks(result)}


def judge_checks(
    status: PrStatus,
    reported: list[dict[str, Any]],
    required: set[str] | None,
    policy: ChecksPolicy,
    *,
    grace_over: bool,
) -> None:
    """Set `status.verdict` and its check lines. `required` is None for `required = "all"`;
    `grace_over` says checks have had their time to appear (always, for a one-shot read)."""
    wanted = set(required or ()) | set(policy.add)
    status.counted = "required" if required else "all"
    counted = [
        check
        for check in reported
        if (not required or str(check.get("name")) in wanted)
        and str(check.get("name")) not in policy.drop
    ]
    names = {str(check.get("name")) for check in reported}
    status.missing = sorted(set(policy.add) - names - set(policy.drop))
    status.buckets = dict(Counter(str(check.get("bucket", "")) for check in counted))
    failing = [check for check in counted if check.get("bucket") in _FAILED]
    status.failing = [
        CheckLine(str(c.get("name")), str(c.get("bucket")), str(c.get("link") or ""))
        for c in failing[:MAX_LISTED]
    ]
    status.pending = sorted({str(c.get("name")) for c in counted if c.get("bucket") == "pending"})
    if failing or (status.missing and grace_over):
        status.verdict = "failed"
    elif status.pending or status.missing:
        status.verdict = "pending"
    elif not counted:
        if not grace_over:
            status.verdict = "pending"
        else:
            status.verdict = "passed" if policy.no_checks == "pass" else "no_checks"
    else:
        status.verdict = "passed"
    status.summary = _line(status, len(counted))


def _line(status: PrStatus, counted: int) -> str:
    parts = [f"{status.target}: {status.verdict}", f"{counted} {status.counted} checks"]
    if status.failing:
        parts.append("failing " + ", ".join(c.name for c in status.failing))
    if status.missing:
        parts.append("never reported " + ", ".join(status.missing))
    if status.pending:
        parts.append(f"{len(status.pending)} pending")
    return "; ".join(parts)


def pause(ctx: Context, seconds: float) -> bool:
    """Sleep `seconds` in short steps; False (at once) when the run is stopped, or when the run
    has too little time left to poll again and still answer."""
    if ctx.cancelled or ctx.remaining() < seconds + 10.0:
        return False
    left = seconds
    while left > 0:
        if ctx.cancelled:
            return False
        _sleep(min(left, 1.0))
        left -= 1.0
    return not ctx.cancelled


def _sleep(seconds: float) -> None:  # the tests replace it
    time.sleep(seconds)
