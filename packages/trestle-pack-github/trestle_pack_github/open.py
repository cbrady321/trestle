"""Opening a pull request: the push guards (P-21) and the `OpenBackend` seam (P-19).

Every guard runs before anything changes, so a refusal leaves the checkout and GitHub as they
were. A refusal is a `Refusal` in the answer (`ok: false`, `resend: will_not_succeed`): the
agent fixes the request, never a person (P-21). The guards:

- the head is a branch, never a detached HEAD, the default branch or the base;
- the branch matches `[open] branch_pattern`, and the title `[open] title_pattern`, when set;
- only the listed `paths` are committed, with the request's `commit_message` (there is no
  built-in message); a path outside the repository, or anything already staged outside the
  paths, is refused;
- an open PR for the branch is edited only with `update_existing: true`, only when the acting
  login opened it (unless `[open] edit_any_pr`), and never when it comes from a fork.

`OpenBackend` is what changes the world: `push`, `create` and `edit`. `GhOpenBackend` runs one
visible command for each; a company wrapper may pass its own (for example one that pushes to a
mirror first), declaring its library in `packages=` so digests and grants cover it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from trestle.plugin import ExecResult

from trestle_pack_github.policy import OpenPolicy
from trestle_pack_github.results import Refusal
from trestle_pack_github.transport import CtxGit, GhTransport

EXISTING_FIELDS = "number,url,author,isCrossRepository,headRefName"


@dataclass(frozen=True)
class PrSpec:
    """What a created or edited PR says: `repo` is `HOST/OWNER/REPO`, `body_file` the body
    (None when editing leaves the body as it is)."""

    repo: str
    head: str
    base: str
    title: str
    body_file: Path | None
    draft: bool = False
    labels: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()


class OpenBackend(Protocol):
    def push(self, remote: str, branch: str) -> ExecResult: ...
    def create(self, spec: PrSpec) -> ExecResult: ...
    def edit(self, number: int, spec: PrSpec) -> ExecResult: ...


class GhOpenBackend:
    """`git push` and `gh pr create` / `gh pr edit`, one command each. Never a force push."""

    def __init__(self, gh: GhTransport, git: CtxGit) -> None:
        self.gh = gh
        self.git = git

    def push(self, remote: str, branch: str) -> ExecResult:
        return self.git.run(
            ["push", "--porcelain", "-u", remote, f"HEAD:refs/heads/{branch}"],
            output="git-push.log",
        )

    def create(self, spec: PrSpec) -> ExecResult:
        body = str(spec.body_file or "/dev/null")
        args = ["pr", "create", "-R", spec.repo, "--head", spec.head, "--base", spec.base,
                "--title", spec.title, "--body-file", body]  # fmt: skip
        args += ["--draft"] if spec.draft else []
        for label in spec.labels:
            args += ["--label", label]
        for reviewer in spec.reviewers:
            args += ["--reviewer", reviewer]
        return self.gh.run(args, cwd=self.git.root, output="gh-pr-create.log")

    def edit(self, number: int, spec: PrSpec) -> ExecResult:
        args = ["pr", "edit", str(number), "-R", spec.repo, "--title", spec.title]
        args += ["--body-file", str(spec.body_file)] if spec.body_file is not None else []
        for label in spec.labels:
            args += ["--add-label", label]
        for reviewer in spec.reviewers:
            args += ["--add-reviewer", reviewer]
        return self.gh.run(args, cwd=self.git.root, output="gh-pr-edit.log")


def branch_refusal(
    branch: str | None, *, default: str, base: str, title: str, policy: OpenPolicy
) -> Refusal | None:
    """The head branch and title guards; None when the request may go on."""
    if branch is None:
        return Refusal(
            "github.detached_head",
            "HEAD is detached: switch to a branch for this change (`git switch -c <name>`), "
            "then send the request again.",
        )
    if branch == default:
        return Refusal(
            "github.default_branch",
            f"{branch!r} is the repository's default branch, which this plugin never pushes. "
            "Commit the change on its own branch (`git switch -c <name>`), then resend.",
        )
    if branch == base:
        return Refusal("github.head_is_base", f"the head branch {branch!r} is also the base")
    if policy.branch_pattern and not re.fullmatch(policy.branch_pattern, branch):
        return Refusal(
            "github.branch_pattern",
            f"branch {branch!r} does not match the policy's branch_pattern "
            f"{policy.branch_pattern!r}: rename it (`git branch -m <name>`), then resend.",
        )
    if not title.strip():
        return Refusal("github.title_missing", "give the pull request a title")
    if policy.title_pattern and not re.fullmatch(policy.title_pattern, title):
        return Refusal(
            "github.title_pattern",
            f"the title does not match the policy's title_pattern {policy.title_pattern!r}",
        )
    return None


def path_refusal(paths: list[str] | None, commit_message: str | None) -> Refusal | None:
    """The commit guards: listed paths inside the repository, and a message exactly when there
    is something to commit."""
    if paths and not (commit_message or "").strip():
        return Refusal(
            "github.commit_message_missing",
            "`paths` are committed with `commit_message`, which has no default: give one.",
        )
    if commit_message and not paths:
        return Refusal(
            "github.paths_missing",
            "`commit_message` needs the `paths` to commit; without paths nothing is committed "
            "and the branch is pushed as it is.",
        )
    for path in paths or []:
        pure = PurePosixPath(path)
        if not path.strip() or pure.is_absolute() or ".." in pure.parts:
            return Refusal(
                "github.path_outside_repo",
                f"path {path!r} must be relative to the repository root and stay inside it",
            )
    return None


def _covered(name: str, paths: list[str]) -> bool:
    for path in paths:
        stem = path.rstrip("/")
        if stem in {"", "."} or name == stem or name.startswith(stem + "/"):
            return True
    return False


def staged_outside(staged: list[str], paths: list[str]) -> list[str]:
    """Files already staged (`git diff --cached --name-only`) that the paths do not cover."""
    return [name for name in staged if name and not _covered(name, paths)]


def existing_pr_refusal(
    existing: list[dict[str, Any]],
    *,
    update_existing: bool,
    login: str | None,
    policy: OpenPolicy,
) -> tuple[Refusal | None, dict[str, Any] | None]:
    """The guard on an open PR for the branch: the refusal, else the PR to edit (or None)."""
    if not existing:
        return None, None
    pr = existing[0]
    url = str(pr.get("url", ""))
    if not update_existing:
        return Refusal(
            "github.pr_exists",
            f"{url} is already open for this branch. Resend with update_existing: true to push "
            "to it and edit it, or use another branch.",
        ), None
    if pr.get("isCrossRepository"):
        return Refusal("github.pr_from_fork", f"{url} comes from a fork; it is not edited"), None
    author = str((pr.get("author") or {}).get("login") or "")
    if not policy.edit_any_pr and author.lower() != (login or "").lower():
        return Refusal(
            "github.pr_not_yours",
            f"{url} was opened by {author or 'someone else'}, not {login}; this plugin edits "
            "only its own PRs (the operator can allow others with [open] edit_any_pr).",
        ), None
    return None, pr


def pick_body(root: Path, body: str | None, templates: tuple[str, ...]) -> str:
    """The request's body, else the first template in the repository, else nothing."""
    if body is not None:
        return body
    for name in templates:
        path = root / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def created_url(result: ExecResult) -> str:
    """The PR URL `gh pr create` printed last."""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1] if lines and lines[-1].startswith("https://") else ""
