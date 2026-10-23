"""The github pack's policy file: how a company tunes the pack with settings instead of a fork.

The operator names the file with one line in `$TRESTLE_HOME/config.toml`:

    [plugins.env]
    TRESTLE_GITHUB_POLICY = "/abs/path/github-policy.toml"

With no file named, every default below applies (P-21: safe and generic, no vendor names). A
file holds only the keys it changes:

    policy_version = 1

    [bots]                      # GitHub's own signal (a Bot author, a `[bot]` login) always counts
    logins = ["qodo-merge"]     # extra logins, compared case-insensitively
    patterns = ["^ci-.*"]       # extra regular expressions, matched against the whole login

    [triage]                    # what `github_pr_comments` suggests for each comment
    not_actionable = ["nit", "nitpick", "optional"]   # whole words, case-insensitive
    clarify_on_question = true  # a human's question is `clarify`
    code_change = ["bug", "error", "fix", "must", "should"]
    bot_comments = "triage"     # "triage" (the rules above), "code_change", or "ignore"

    [checks]                    # what `github_pr_status` counts
    required = "branch_protection"   # or "all": every reported check counts
    add = ["build"]             # named checks that count as required as well
    drop = ["codecov/patch"]    # named checks that never count
    no_checks = "report"        # "report": its own verdict, never passed; or "pass"
    grace_s = 90                # how long a waiting run lets checks appear before `no_checks`
    poll_s = 15                 # how often a waiting run asks again (at least 5)

    [open]                      # `github_pr_open`'s guards and defaults
    remote = "origin"
    branch_pattern = "(feat|fix|chore)/.+"   # the head branch must match; never the default one
    title_pattern = ""          # the title must match, when set
    body_templates = [".github/pull_request_template.md"]   # the first that exists is the body
    labels = []                 # always added
    reviewers = []              # always requested
    draft = false               # the default when the request does not say
    edit_any_pr = false         # true: `update_existing` may edit a PR someone else opened

Unknown tables and keys, a wrong type and a `policy_version` other than 1 are refused: the run
ends `Blocked("github.policy_invalid")`, naming the file and the key, because only the operator
can fix the file. Every plugin of the pack reports the file and its sha256 in its result
(`policy`) and attaches the effective policy as `github-policy.json`; the host also records the
file's digest in the run's provenance (`pack.toml [config] files`, P-20).

The GitHub host is not policy: `gh` reads `GH_HOST`, set under `[plugins.env]` for GitHub
Enterprise (P-21).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Literal

from trestle.plugin import Blocked, Context

POLICY_ENV = "TRESTLE_GITHUB_POLICY"
POLICY_VERSION = 1
POLICY_ARTIFACT = "github-policy.json"
MIN_POLL_S = 5.0


@dataclass(frozen=True)
class BotPolicy:
    logins: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class TriagePolicy:
    not_actionable: tuple[str, ...] = ("nit", "nitpick", "optional")
    clarify_on_question: bool = True
    code_change: tuple[str, ...] = ("bug", "error", "fix", "must", "should")
    bot_comments: Literal["triage", "code_change", "ignore"] = "triage"


@dataclass(frozen=True)
class ChecksPolicy:
    required: Literal["branch_protection", "all"] = "branch_protection"
    add: tuple[str, ...] = ()
    drop: tuple[str, ...] = ()
    no_checks: Literal["report", "pass"] = "report"
    grace_s: float = 90.0
    poll_s: float = 15.0


@dataclass(frozen=True)
class OpenPolicy:
    remote: str = "origin"
    branch_pattern: str = ""
    title_pattern: str = ""
    body_templates: tuple[str, ...] = (
        ".github/pull_request_template.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "docs/pull_request_template.md",
    )
    labels: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()
    draft: bool = False
    edit_any_pr: bool = False


@dataclass(frozen=True)
class GithubPolicy:
    """The effective policy: the defaults with the file's keys over them."""

    policy_version: int = POLICY_VERSION
    bots: BotPolicy = field(default_factory=BotPolicy)
    triage: TriagePolicy = field(default_factory=TriagePolicy)
    checks: ChecksPolicy = field(default_factory=ChecksPolicy)
    open: OpenPolicy = field(default_factory=OpenPolicy)


@dataclass(frozen=True)
class PolicyEcho:
    """Which policy a run used: `source` is the file's path, or "defaults" when none is named;
    `sha256` is the file's digest (None for the defaults)."""

    source: str
    sha256: str | None


@dataclass(frozen=True)
class LoadedPolicy:
    policy: GithubPolicy
    echo: PolicyEcho


class PolicyInvalid(ValueError):
    """The policy file is unreadable or breaks the format; the message names the key."""


_SECTIONS: dict[str, type] = {
    "bots": BotPolicy,
    "triage": TriagePolicy,
    "checks": ChecksPolicy,
    "open": OpenPolicy,
}
_CHOICES: dict[tuple[str, str], tuple[str, ...]] = {
    ("triage", "bot_comments"): ("triage", "code_change", "ignore"),
    ("checks", "required"): ("branch_protection", "all"),
    ("checks", "no_checks"): ("report", "pass"),
}
_REGEXES = {("bots", "patterns"), ("open", "branch_pattern"), ("open", "title_pattern")}


def _value(section: str, key: str, default: object, raw: object) -> object:
    where = f"[{section}] {key}"
    if isinstance(default, bool):
        if not isinstance(raw, bool):
            raise PolicyInvalid(f"{where} must be true or false")
        return raw
    if isinstance(default, float):
        if isinstance(raw, bool) or not isinstance(raw, int | float) or raw < 0:
            raise PolicyInvalid(f"{where} must be a number of seconds, 0 or more")
        if key == "poll_s" and raw < MIN_POLL_S:
            raise PolicyInvalid(f"{where} must be at least {MIN_POLL_S:g}")
        return float(raw)
    if isinstance(default, tuple):
        if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
            raise PolicyInvalid(f"{where} must be a list of non-empty strings")
        if (section, key) in _REGEXES:
            for item in raw:
                _compile(where, item)
        return tuple(raw)
    if not isinstance(raw, str):
        raise PolicyInvalid(f"{where} must be a string")
    choices = _CHOICES.get((section, key))
    if choices is not None and raw not in choices:
        raise PolicyInvalid(f"{where} must be one of {', '.join(map(repr, choices))}")
    if (section, key) in _REGEXES and raw:
        _compile(where, raw)
    return raw


def _compile(where: str, pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise PolicyInvalid(f"{where}: {pattern!r} is not a regular expression ({exc})") from None


def parse_policy(raw: Mapping[str, Any]) -> GithubPolicy:
    """The policy a parsed TOML document describes; `PolicyInvalid` names the first bad key."""
    version = raw.get("policy_version")
    if version != POLICY_VERSION:
        raise PolicyInvalid(f"policy_version must be {POLICY_VERSION} (got {version!r})")
    policy = GithubPolicy()
    for name, table in raw.items():
        if name == "policy_version":
            continue
        kind = _SECTIONS.get(name)
        if kind is None or not isinstance(table, dict):
            known = ", ".join(f"[{s}]" for s in _SECTIONS)
            raise PolicyInvalid(f"unknown key {name!r}: the tables are {known}")
        current = getattr(policy, name)
        defaults = {f.name: getattr(current, f.name) for f in fields(kind)}
        changes: dict[str, object] = {}
        for key, value in table.items():
            if key not in defaults:
                raise PolicyInvalid(
                    f"[{name}] unknown key {key!r}: the keys are {', '.join(defaults)}"
                )
            changes[key] = _value(name, key, defaults[key], value)
        policy = replace(policy, **{name: replace(current, **changes)})
    return policy


def load_policy(environ: Mapping[str, str] | None = None) -> LoadedPolicy:
    """The policy file `TRESTLE_GITHUB_POLICY` names, else the defaults. A file that is missing,
    unreadable or invalid raises `Blocked("github.policy_invalid")`: the operator fixes it."""
    env = os.environ if environ is None else environ
    named = env.get(POLICY_ENV, "")
    if not named:
        return LoadedPolicy(GithubPolicy(), PolicyEcho(source="defaults", sha256=None))
    path = Path(named).expanduser()
    try:
        data = path.read_bytes()
        policy = parse_policy(tomllib.loads(data.decode("utf-8")))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, PolicyInvalid) as exc:
        why = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        raise Blocked(
            "github.policy_invalid",
            f"Fix the github policy file {path} ({POLICY_ENV} under [plugins.env] in "
            f"$TRESTLE_HOME/config.toml): {why}. Then resend.",
        ) from None
    digest = hashlib.sha256(data).hexdigest()
    return LoadedPolicy(policy, PolicyEcho(source=str(path), sha256=digest))


def effective_policy(loaded: LoadedPolicy) -> dict[str, Any]:
    """The effective policy as JSON-ready data, with where it came from."""
    return {"source": loaded.echo.source, "sha256": loaded.echo.sha256, **asdict(loaded.policy)}


def attach_policy(ctx: Context, loaded: LoadedPolicy) -> str:
    """Attach the effective policy as `github-policy.json`; its artifact id."""
    staged = ctx.artifact(POLICY_ARTIFACT)
    staged.write_text(json.dumps(effective_policy(loaded), indent=2), encoding="utf-8")
    return ctx.attach(staged, name=POLICY_ARTIFACT)
