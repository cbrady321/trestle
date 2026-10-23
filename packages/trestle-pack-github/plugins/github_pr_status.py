"""github_pr_status: the state and checks verdict of many pull requests in one run.

The recipe, top to bottom (adapt it by copying this file and publishing it under a new name):

1. load the policy (`TRESTLE_GITHUB_POLICY`, else the defaults) and attach it;
2. `gh auth status --hostname <host>`: no login is `Blocked("github.gh_auth")`;
3. per PR, `gh pr view <url> --json url,title,state,...`;
4. per PR still pending, `gh pr checks <url> --json name,bucket,state,link,workflow`, and with
   `[checks] required = "branch_protection"` also `gh pr checks <url> --required --json name`;
5. judge the checks that count (`judge_checks`: required ones, else all; policy `add`/`drop`);
6. with `wait`, repeat 4-5 every `[checks] poll_s` until no PR is pending, `timeout_s` passes,
   or the run is stopped or nearly out of time; checks get `[checks] grace_s` to appear before
   a PR with none is `no_checks`;
7. answer `ok` (every PR `passed`), the counts and one line per PR; attach the full report. When
   a PR's checks failed, there were none, or its status could not be read, raise
   `Failed("checks_not_passed")` carrying that report; when PRs are only still `pending` (the
   wait ran out), raise `Failed("checks_pending")`, so nothing ordered after this run starts on
   checks that have not finished.

Read-only: nothing is pushed, commented or changed on GitHub.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

from trestle.plugin import Context, Failed, trestle

from trestle_pack_github import (
    CHECK_FIELDS,
    VIEW_FIELDS,
    CtxGh,
    GhError,
    TargetError,
    apply_view,
    attach_policy,
    judge_checks,
    load_policy,
    parse_target,
    pause,
    read_checks,
    require_login,
    required_names,
)
from trestle_pack_github.results import PrStatus, StatusReport  # defined there: the schema reads it


@trestle(packages=["trestle_pack_github"], deadline=3600.0, summary_fields=["ok", "summary"])
def github_pr_status(
    ctx: Context,
    targets: list[str],
    wait: bool = False,
    timeout_s: float | None = None,
) -> StatusReport:
    """State, review decision and checks verdict for each pull request in `targets` (URLs, or
    `owner/repo#number`).

    Without `wait`, one read. With `wait`, polls until no PR is pending, for at most `timeout_s`
    (default: the run's own time). A PR's `verdict` is `passed`, `failed`, `pending`,
    `no_checks` or `error`; `ok` is true only when every PR passed. A PR that is `failed`,
    `no_checks` or `error` raises `Failed` (`checks_not_passed`) with the report as its result;
    PRs that are only still `pending` raise `Failed` (`checks_pending`).
    Example: {"targets": ["octo/app#12", "https://github.com/octo/app/pull/13"], "wait": true}
    """
    loaded = load_policy()
    policy = loaded.policy.checks
    attach_policy(ctx, loaded)
    gh = CtxGh(ctx)
    require_login(gh)

    prs: list[PrStatus] = []
    live: list[tuple[PrStatus, str]] = []
    for target in targets:
        status = PrStatus(target=target)
        prs.append(status)
        try:
            ref = parse_target(target, gh.host)
            status.url = ref.url
            apply_view(status, gh.run(["pr", "view", ref.url, "--json", VIEW_FIELDS]))
            live.append((status, ref.url))
        except (TargetError, GhError) as exc:
            status.error = str(exc)
            status.summary = f"{target}: error: {exc}"

    waited = 0.0
    budget = timeout_s if timeout_s is not None else float("inf")
    while True:
        grace_over = not wait or waited >= policy.grace_s
        for status, url in live:
            try:
                listed = gh.run(["pr", "checks", url, "--json", CHECK_FIELDS], output="checks.log")
                required = None
                if policy.required == "branch_protection":
                    required = gh.run(
                        ["pr", "checks", url, "--required", "--json", "name"], output="checks.log"
                    )
                judge_checks(
                    status, read_checks(listed), required_names(required), policy,
                    grace_over=grace_over,
                )  # fmt: skip
            except GhError as exc:
                status.verdict, status.error = "error", str(exc)
                status.summary = f"{status.target}: error: {exc}"
        live = [(s, url) for s, url in live if s.verdict == "pending"]
        if not wait or not live or waited + policy.poll_s > budget:
            break
        ctx.progress(f"{len(live)} of {len(prs)} PRs pending after {waited:.0f}s")
        if not pause(ctx, policy.poll_s):
            break
        waited += policy.poll_s

    counts: Counter[str] = Counter(status.verdict for status in prs)
    report = StatusReport(
        ok=bool(prs) and counts["passed"] == len(prs),
        summary=", ".join(f"{n} {verdict}" for verdict, n in sorted(counts.items())) or "no PRs",
        counts=dict(counts),
        prs=prs,
        waited_s=waited,
        policy=loaded.echo,
    )
    staged = ctx.artifact("github-pr-status.json")
    staged.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    ctx.attach(staged, name="github-pr-status.json")
    ctx.log(f"github_pr_status: {report.summary}")
    not_passed = [p for p in prs if p.verdict in ("failed", "no_checks", "error")]
    if not_passed:
        raise Failed(
            "checks_not_passed",
            f"checks did not pass for {len(not_passed)} of {len(prs)} PRs ({report.summary})",
            report,
        )
    pending = [p for p in prs if p.verdict == "pending"]
    if pending:
        raise Failed(
            "checks_pending",
            f"checks still pending for {len(pending)} of {len(prs)} PRs ({report.summary})",
            report,
        )
    return report
