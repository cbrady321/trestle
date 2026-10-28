"""github_pr_comments: the open review comments of many pull requests, each with a next step.

The recipe, top to bottom (adapt it by copying this file and publishing it under a new name):

1. load the policy (`TRESTLE_GITHUB_POLICY`, else the defaults) and attach it;
2. `gh auth status --hostname <host>`: no login is `Blocked("github.gh_auth")`;
3. per PR, one `gh api graphql --hostname <host> -f query=<QUERY> -f owner=.. -f name=..
   -F number=..` for its review threads, comments and review bodies (`trestle_pack_github.
   comments.QUERY`);
4. drop resolved threads (unless `include_resolved`), mark bots (`[bots]`), and suggest a next
   step for each (`PolicyTriage`, from `[triage]`): `code_change`, `clarify`, `reply_only` or
   `not_actionable`;
5. answer the first `max_items` per PR (code changes first) and the counts; attach everything.
   When any PR's comments could not be read, raise `Failed("comments_unread")` carrying that
   report (the PRs that were read are in it).

Read-only: nothing is posted, resolved or changed on GitHub.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

from trestle.plugin import Context, Failed, trestle

from trestle_pack_github import (
    CtxGh,
    GhError,
    PolicyTriage,
    TargetError,
    attach_policy,
    graphql_args,
    json_of,
    load_policy,
    parse_target,
    read_comments,
    require_login,
    triage_items,
)
from trestle_pack_github.results import CommentsReport, PrComments  # defined there: the schema


@trestle(packages=["trestle_pack_github"], deadline=900.0, summary_fields=["ok", "summary"])
def github_pr_comments(
    ctx: Context,
    targets: list[str],
    include_resolved: bool = False,
    max_items: int = 30,
) -> CommentsReport:
    """Unresolved review threads, PR comments and review bodies for each pull request in
    `targets` (URLs, or `owner/repo#number`), each with a suggested `action`, the author and
    whether it is a bot. Each PR keeps its first `max_items` (code changes first); `omitted`
    counts the rest, which are in the attached `github-pr-comments.json`. A PR whose comments
    could not be read (bad target, unknown PR, gh error) raises `Failed` (`comments_unread`)
    with the report as its result, each unread PR carrying its `error`.
    Example: {"targets": ["octo/app#12"]}
    """
    loaded = load_policy()
    attach_policy(ctx, loaded)
    triage = PolicyTriage(loaded.policy.triage)
    gh = CtxGh(ctx)
    require_login(gh)

    prs: list[PrComments] = []
    everything: dict[str, list[dict[str, object]]] = {}
    for target in targets:
        try:
            ref = parse_target(target, gh.host)
            payload = json_of(gh.run(graphql_args(ref), output="graphql.log"), "gh api graphql")
            pr, found = read_comments(
                target, payload, loaded.policy.bots, include_resolved=include_resolved
            )
            triage_items(pr, found, triage, max_items=max(0, max_items))
            everything[target] = [asdict(item) for item, _body in found]
        except (TargetError, GhError) as exc:
            pr = PrComments(target=target, error=str(exc))
        prs.append(pr)

    counts: Counter[str] = Counter()
    for pr in prs:
        counts.update(pr.actions)
    errors = sum(1 for pr in prs if pr.error)
    counts["error"] = errors
    report = CommentsReport(
        ok=bool(prs) and errors == 0,
        summary=", ".join(f"{n} {action}" for action, n in sorted(counts.items()) if n)
        or "no open comments",
        counts=dict(counts),
        prs=prs,
        policy=loaded.echo,
    )
    staged = ctx.artifact("github-pr-comments.json")
    staged.write_text(json.dumps(everything, indent=2), encoding="utf-8")
    ctx.attach(staged, name="github-pr-comments.json")
    ctx.log(f"github_pr_comments: {report.summary}")
    if errors:
        raise Failed(
            "comments_unread",
            f"comments could not be read for {errors} of {len(prs)} PRs",
            report,
        )
    return report
