"""github_pr_open: push the current branch and open (or update) its pull request, behind guards.

The recipe, top to bottom (adapt it by copying this file and publishing it under a new name):

1. load the policy (`TRESTLE_GITHUB_POLICY`, else the defaults) and attach it;
2. `gh auth status --hostname <host>`: no login is `Blocked("github.gh_auth")`;
3. read, change nothing: `git rev-parse --show-toplevel`, `git symbolic-ref --quiet --short
   HEAD`, `gh repo view --json nameWithOwner,defaultBranchRef`, `gh pr list -R <repo> --head
   <branch> --state open --json ...`, with `update_existing` `gh api user --jq .login`, and with
   `paths` `git diff --cached --name-only`;
4. the guards (`branch_refusal`, `path_refusal`, `existing_pr_refusal`, `staged_outside`): any
   refusal answers `ok: false`, `action: refused`, before anything changed;
5. with `paths`: `git add -A -- <paths>` and `git commit -q -m <commit_message>`;
6. `git push --porcelain -u <remote> HEAD:refs/heads/<branch>` (never a force push);
7. `gh pr create -R <repo> --head <branch> --base <base> --title .. --body-file .. [--draft]
   [--label ..] [--reviewer ..]`, or for the PR already open, `gh pr edit <n> ...`;
8. answer what was done; a command that fails answers `action: failed` with its error.
"""

from __future__ import annotations

from pathlib import Path

from trestle.plugin import Context, trestle

from trestle_pack_github import (
    EXISTING_FIELDS,
    CtxGh,
    CtxGit,
    GhError,
    GhOpenBackend,
    PrSpec,
    Refusal,
    attach_policy,
    branch_refusal,
    created_url,
    existing_pr_refusal,
    failure,
    json_of,
    load_policy,
    path_refusal,
    pick_body,
    require_login,
    staged_outside,
)
from trestle_pack_github.results import OpenResult  # where it is defined: the schema reads it


@trestle(
    packages=["trestle_pack_github"],
    deadline=900.0,
    summary_fields=["ok", "action", "summary", "url"],
)
def github_pr_open(
    ctx: Context,
    workdir: str,
    title: str,
    body: str | None = None,
    base: str | None = None,
    paths: list[str] | None = None,
    commit_message: str | None = None,
    draft: bool | None = None,
    labels: list[str] | None = None,
    reviewers: list[str] | None = None,
    update_existing: bool = False,
) -> OpenResult:
    """Push the branch checked out in `workdir` and open its pull request into `base` (default:
    the repository's default branch).

    `paths` (relative to the repository root) are committed first with `commit_message`; nothing
    else is. `body` defaults to the repository's PR template. `draft`, `labels` and `reviewers`
    add to the policy's. An open PR for the branch is pushed to and edited only with
    `update_existing: true`, and only if the acting gh login opened it. A guard's refusal is
    `ok: false`, `action: "refused"`, with `refusal.code` and what to change.
    Example: {"workdir": "/abs/repo", "title": "Fix the parser", "paths": ["src/parse.py"],
    "commit_message": "Fix the parser on empty input"}
    """
    loaded = load_policy()
    policy = loaded.policy.open
    attach_policy(ctx, loaded)
    gh = CtxGh(ctx)
    require_login(gh)
    answer = OpenResult(ok=False, action="refused", summary="", policy=loaded.echo)

    def refused(refusal: Refusal) -> OpenResult:
        answer.refusal, answer.summary = refusal, f"refused ({refusal.code}): {refusal.message}"
        return answer

    def failed(error: str) -> OpenResult:
        answer.action, answer.error, answer.summary = "failed", error, f"failed: {error}"
        return answer

    git = CtxGit(ctx, workdir)
    top = git.run(["rev-parse", "--show-toplevel"])
    if not top.ok:
        return refused(Refusal("github.not_a_checkout", f"{workdir} is not a git checkout"))
    root = Path(top.stdout.strip())
    git = CtxGit(ctx, root, path=git.path)
    head = git.run(["symbolic-ref", "--quiet", "--short", "HEAD"])
    branch = head.stdout.strip() if head.ok else None
    try:
        repo = json_of(
            gh.run(["repo", "view", "--json", "nameWithOwner,defaultBranchRef"], cwd=root),
            "gh repo view",
        )
        slug = f"{gh.host}/{repo['nameWithOwner']}"
        default = str(repo["defaultBranchRef"]["name"])
    except (GhError, KeyError, TypeError) as exc:
        return failed(str(exc))
    answer.branch, answer.base = branch or "", base or default
    refusal = branch_refusal(
        branch, default=default, base=answer.base, title=title, policy=policy
    ) or path_refusal(paths, commit_message)
    if refusal is not None or branch is None:
        return refused(refusal or Refusal("github.detached_head", "HEAD is detached"))

    try:
        listed = json_of(gh.run(["pr", "list", "-R", slug, "--head", branch, "--state", "open",
                                 "--json", EXISTING_FIELDS], cwd=root), "gh pr list")  # fmt: skip
        login = None
        if listed and update_existing:
            me = gh.run(["api", "user", "--hostname", gh.host, "--jq", ".login"])
            login = me.stdout.strip() if me.ok else None
    except GhError as exc:
        return failed(str(exc))
    refusal, existing = existing_pr_refusal(
        listed, update_existing=update_existing, login=login, policy=policy
    )
    if refusal is not None:
        return refused(refusal)
    if paths:
        staged = git.run(["diff", "--cached", "--name-only"]).stdout.splitlines()
        outside = staged_outside(staged, paths)
        if outside:
            return refused(Refusal(
                "github.index_not_clean",
                f"already staged and not in `paths`: {', '.join(outside[:10])}. Unstage them "
                "(`git restore --staged <file>`) or list them in `paths`, then resend.",
            ))  # fmt: skip

    # everything above only read; from here on the checkout and GitHub change
    if paths:
        added = git.run(["add", "-A", "--", *paths])
        if not added.ok:
            return failed(failure(added, "git add"))
        answer.committed = git.run(["diff", "--cached", "--name-only"]).stdout.splitlines()
        if answer.committed:
            done = git.run(["commit", "-q", "-m", commit_message or ""])
            if not done.ok:
                return failed(failure(done, "git commit"))
    answer.commit = git.run(["rev-parse", "HEAD"]).stdout.strip()

    backend = GhOpenBackend(gh, git)
    pushed = backend.push(policy.remote, branch)
    if not pushed.ok:
        return failed(failure(pushed, "git push"))
    answer.pushed = True

    body_file = None
    if existing is None or body is not None:
        body_file = ctx.tmp / "pr-body.md"
        body_file.write_text(pick_body(root, body, policy.body_templates), encoding="utf-8")
    spec = PrSpec(
        repo=slug, head=branch, base=answer.base, title=title, body_file=body_file,
        draft=policy.draft if draft is None else draft,
        labels=tuple(dict.fromkeys([*policy.labels, *(labels or [])])),
        reviewers=tuple(dict.fromkeys([*policy.reviewers, *(reviewers or [])])),
    )  # fmt: skip
    if existing is not None:
        done = backend.edit(int(existing["number"]), spec)
        answer.action, answer.url = "updated", str(existing["url"])
    else:
        done = backend.create(spec)
        answer.action, answer.url = "created", created_url(done)
    if not done.ok:
        return failed(failure(done, f"gh pr {'edit' if existing else 'create'}"))
    number = answer.url.rstrip("/").rsplit("/", 1)[-1]
    answer.number = int(number) if number.isdigit() else None
    answer.ok = True
    answer.summary = f"{answer.action} {answer.url}: {branch} into {answer.base}"
    ctx.log(f"github_pr_open: {answer.summary}")
    return answer
