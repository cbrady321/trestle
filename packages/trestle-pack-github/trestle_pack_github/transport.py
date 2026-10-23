"""Running `gh` and `git` through `ctx.exec`: finding them, the environment they get, and the one
answer for a missing tool or login (`Blocked`).

`GhTransport` is a seam (P-19): a company wrapper may hand the plugins its own transport, for
example one that records every call, as long as it runs commands through `ctx.exec`. The
default, `CtxGh`, adds nothing but the binary's path and a non-interactive environment, so each
argv a wrapper passes is the command that runs.

Identity: `gh` acts as whoever the operator logged in with `gh auth login` (as the user the
service runs as), or as `GH_TOKEN` when a wrapper passes one. The bundled wrappers declare no
credential and hold no token; a company wrapper that declares `credentials=["gh"]` passes
`env=ctx.credential_env("gh")` to `CtxGh`, by name, and the value never appears in an argument.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from trestle.plugin import Blocked, Context, ExecResult

GH_PATH_ENV = "TRESTLE_GH_PATH"
GIT_PATH_ENV = "TRESTLE_GIT_PATH"
DEFAULT_HOST = "github.com"
# Where Homebrew, the distribution packages and the official installer put the CLIs: a launchd
# or systemd service often runs with a PATH that has none of them.
WELL_KNOWN_GH = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh")
WELL_KNOWN_GIT = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")
CALL_TIMEOUT_S = 120.0
# Never wait on a prompt nobody will answer, never page, never colour.
QUIET_ENV = {
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_PAGER": "cat",
    "NO_COLOR": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
}
# What `gh` prints when it has no usable login for the host.
_AUTH_MARKERS = (
    "gh auth login",
    "not logged into",
    "authentication required",
    "bad credentials",
    "http 401",
    "requires authentication",
)


class GhError(Exception):
    """A `gh` or `git` command failed for a reason a person need not act on (a missing PR, a
    network error): the plugin reports it per target and goes on."""


def _runnable(path: str) -> bool:
    return Path(path).is_file() and os.access(path, os.X_OK)


def _locate(tool: str, variable: str, known: Sequence[str], environ: Mapping[str, str]) -> str:
    named = environ.get(variable)
    if named:
        if _runnable(named):
            return named
        raise Blocked(
            f"github.{tool}_missing",
            f"{variable}={named} is not an executable. Fix it under [plugins.env] in "
            f"$TRESTLE_HOME/config.toml (or remove it to use the {tool} on PATH), then resend.",
        )
    found = shutil.which(tool, path=environ.get("PATH"))
    if found:
        return found
    for candidate in known:
        if _runnable(candidate):
            return candidate
    install = "https://cli.github.com" if tool == "gh" else "https://git-scm.com"
    raise Blocked(
        f"github.{tool}_missing",
        f"No {tool} found on the service's PATH or in the usual places. Install it ({install}), "
        f"or set {variable} to its absolute path under [plugins.env] in "
        "$TRESTLE_HOME/config.toml; then resend.",
    )


def locate_gh(environ: Mapping[str, str] | None = None) -> str:
    """The gh CLI's absolute path: `TRESTLE_GH_PATH`, else PATH, else a well-known place."""
    return _locate("gh", GH_PATH_ENV, WELL_KNOWN_GH, os.environ if environ is None else environ)


def locate_git(environ: Mapping[str, str] | None = None) -> str:
    """The git CLI's absolute path: `TRESTLE_GIT_PATH`, else PATH, else a well-known place."""
    return _locate("git", GIT_PATH_ENV, WELL_KNOWN_GIT, os.environ if environ is None else environ)


def gh_host(environ: Mapping[str, str] | None = None) -> str:
    """The GitHub host the run talks to: `GH_HOST` (GitHub Enterprise), else github.com."""
    env = os.environ if environ is None else environ
    return env.get("GH_HOST") or DEFAULT_HOST


def needs_login(result: ExecResult) -> bool:
    text = f"{result.stderr}\n{result.stdout}".lower()
    return not result.ok and any(marker in text for marker in _AUTH_MARKERS)


def login_blocked(host: str) -> Blocked:
    return Blocked(
        "github.gh_auth",
        f"Log gh in to {host}: run `gh auth login --hostname {host}` as the user the Trestle "
        "service runs as, then resend.",
    )


class GhTransport(Protocol):
    """How a plugin runs `gh`: `run(args)` runs `gh <args>` and returns its result. A command
    that fails because gh has no login raises `Blocked("github.gh_auth")`."""

    host: str

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        timeout: float | None = None,
        output: str | None = "gh.log",
    ) -> ExecResult: ...


def _timeout(ctx: Context, timeout: float | None) -> float:
    budget = max(1.0, ctx.remaining() - 5.0)
    return min(timeout if timeout is not None else CALL_TIMEOUT_S, budget)


class CtxGh:
    """`gh` through `ctx.exec`, non-interactive, on `host`."""

    def __init__(
        self,
        ctx: Context,
        *,
        path: str | None = None,
        host: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.ctx = ctx
        self.path = path or locate_gh()
        self.host = host or gh_host()
        self.env: dict[str, str | None] = {**QUIET_ENV, "GH_HOST": self.host, **(env or {})}

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | str | None = None,
        timeout: float | None = None,
        output: str | None = "gh.log",
    ) -> ExecResult:
        result = self.ctx.exec(
            [self.path, *args],
            cwd=cwd,
            timeout=_timeout(self.ctx, timeout),
            env=self.env,
            output=output,
        )
        if needs_login(result):
            raise login_blocked(self.host)
        return result


class CtxGit:
    """`git` through `ctx.exec`, non-interactive, in one repository."""

    def __init__(self, ctx: Context, root: Path | str, *, path: str | None = None) -> None:
        self.ctx = ctx
        self.root = Path(root)
        self.path = path or locate_git()

    def run(
        self, args: Sequence[str], *, timeout: float | None = None, output: str | None = "git.log"
    ) -> ExecResult:
        return self.ctx.exec(
            [self.path, *args],
            cwd=self.root,
            timeout=_timeout(self.ctx, timeout),
            env=dict(QUIET_ENV),
            output=output,
        )


def require_login(gh: GhTransport) -> None:
    """`gh auth status --hostname <host>`: `Blocked("github.gh_auth")` unless gh has a login."""
    result = gh.run(["auth", "status", "--hostname", gh.host], output="gh-auth.log")
    if not result.ok:
        raise login_blocked(gh.host)


def failure(result: ExecResult, what: str) -> str:
    """One line for a failed command: what it was for, and the tail of what it said."""
    if result.timed_out:
        return f"{what}: timed out"
    if result.cancelled:
        return f"{what}: stopped"
    said = (result.stderr.strip() or result.stdout.strip()).splitlines()
    return f"{what}: exit {result.returncode}: {said[-1][:300] if said else 'no output'}"


def json_of(result: ExecResult, what: str) -> Any:
    """The JSON a successful command printed; `GhError` when it failed or printed none."""
    if not result.ok:
        raise GhError(failure(result, what))
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise GhError(f"{what}: gh printed no JSON") from None
