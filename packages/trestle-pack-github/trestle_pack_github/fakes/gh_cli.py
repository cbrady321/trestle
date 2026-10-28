"""A fake `gh` CLI for testing the github pack's wrappers without GitHub.

It answers the commands those wrappers run, over a JSON state file:

    {"login": "octo",                     # null: `gh auth status` fails, as with no login
     "default_branch": "main",            # for `gh repo view` in a checkout
     "repo": "octo/app",                  # the repository a checkout belongs to
     "prs": {"octo/app#12": {
         "title": "...", "state": "OPEN", "isDraft": false, "headRefName": "feat/x",
         "headRefOid": "abc", "reviewDecision": "", "mergeable": "MERGEABLE",
         "author": "octo", "body": "...", "labels": [], "reviewers": [],
         "checks": [[{"name": "build", "bucket": "pending"}],     # poll 1
                    [{"name": "build", "bucket": "pass"}]],       # poll 2, and after
         "required": ["build"],           # what `--required` lists (absent: none required)
         "comments": {...}                # the `pullRequest` object the GraphQL query returns
     }},
     "calls": ["pr view https://github.com/octo/app/pull/12 --json ...", ...]}

`checks` is a list of polls: each `gh pr checks` call without `--required` answers the next one,
and the last repeats. Exit codes follow gh: 8 while a listed check is pending, 1 when one failed,
and "no checks reported" (exit 1) when there are none. `pr create` adds a PR, and `pr edit`
changes one. `fail_auth_on` makes the named subcommand (`"pr view"`) answer as with an expired
login.

`write_fake_gh(folder, state)` writes `folder/gh`, an executable that runs this module over the
state file; point the wrappers at it with `TRESTLE_GH_PATH`.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

STATE_ENV = "TRESTLE_FAKE_GH_STATE"
_URL = re.compile(r"https://[^/]+/([^/]+/[^/]+)/pull/([0-9]+)")


def read_state(path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {"login": "octo", "prs": {}, "calls": []}
    if path.exists():
        state.update(json.loads(path.read_text(encoding="utf-8")))
    return state


def _flags(args: list[str], with_value: set[str]) -> tuple[list[str], dict[str, list[str]]]:
    positional: list[str] = []
    flags: dict[str, list[str]] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in with_value and i + 1 < len(args):
            flags.setdefault(arg, []).append(args[i + 1])
            i += 2
            continue
        if arg.startswith("-"):
            flags.setdefault(arg, []).append("")
        else:
            positional.append(arg)
        i += 1
    return positional, flags


class FakeGh:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    @property
    def state(self) -> dict[str, Any]:
        return read_state(self.state_path)

    @property
    def calls(self) -> list[str]:
        return list(self.state.get("calls", []))

    def set(self, **values: Any) -> None:
        state = self.state
        state.update(values)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        state = self.state
        args = argv[1:]
        state.setdefault("calls", []).append(" ".join(args))
        try:
            return self._answer(state, args)
        finally:
            self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _key(self, state: dict[str, Any], target: str) -> str | None:
        match = _URL.fullmatch(target)
        if match:
            return f"{match.group(1)}#{match.group(2)}"
        if target.isdigit():
            return f"{state.get('repo')}#{target}"
        for key, pr in state["prs"].items():  # a branch name
            if pr.get("headRefName") == target:
                return str(key)
        return None

    def _answer(self, state: dict[str, Any], args: list[str]) -> tuple[int, str, str]:
        command = " ".join(args[:2])
        if command in state.get("fail_auth_on", []):
            return 1, "", "HTTP 401: Bad credentials\nTry authenticating with:  gh auth login\n"
        if command == "auth status":
            if not state.get("login"):
                return (
                    1,
                    "",
                    "You are not logged into any GitHub hosts. To log in, run: gh auth login\n",
                )
            return 0, f"github.com\n  ✓ Logged in to github.com account {state['login']}\n", ""
        if command == "api user":
            return 0, f"{state['login']}\n", ""
        if command == "api graphql":
            return self._graphql(state, args[2:])
        if command == "repo view":
            name = state.get("repo", "octo/app")
            body = {
                "nameWithOwner": name,
                "defaultBranchRef": {"name": state.get("default_branch", "main")},
            }
            return 0, json.dumps(body), ""
        if args[:1] == ["pr"]:
            return self._pr(state, args[1], args[2:])
        return 1, "", f"fake gh: unsupported command {command!r}\n"

    def _graphql(self, state: dict[str, Any], args: list[str]) -> tuple[int, str, str]:
        _, flags = _flags(args, {"-f", "-F", "--hostname"})
        fields = dict(item.split("=", 1) for item in flags.get("-f", []) + flags.get("-F", []))
        key = f"{fields.get('owner')}/{fields.get('name')}#{fields.get('number')}"
        pr = state["prs"].get(key)
        if pr is None:
            number = fields.get("number")
            error = {"message": f"Could not resolve to a PullRequest with the number of {number}."}
            return (
                0,
                json.dumps({"data": {"repository": {"pullRequest": None}}, "errors": [error]}),
                "",
            )
        comments = dict(pr.get("comments") or {})
        comments.setdefault("url", f"https://github.com/{key.replace('#', '/pull/')}")
        return 0, json.dumps({"data": {"repository": {"pullRequest": comments}}}), ""

    def _pr(self, state: dict[str, Any], sub: str, args: list[str]) -> tuple[int, str, str]:
        with_value = {"--json", "--head", "--base", "--title", "--body-file", "--label", "--jq",
                      "--reviewer", "--state", "--add-label", "--add-reviewer", "-R"}  # fmt: skip
        positional, flags = _flags(args, with_value)
        if sub == "create":
            return self._create(state, flags)
        if sub == "list":
            head = flags.get("--head", [""])[0]
            found = [
                {
                    "number": int(k.split("#")[1]),
                    "url": _url(k),
                    "author": {"login": pr.get("author", "")},
                    "isDraft": pr.get("isDraft", False),
                    "headRefName": pr.get("headRefName"),
                }  # fmt: skip
                for k, pr in state["prs"].items()
                if pr.get("headRefName") == head and pr.get("state", "OPEN") == "OPEN"
            ]
            return 0, json.dumps(found), ""
        key = self._key(state, positional[0]) if positional else None
        pr = state["prs"].get(key) if key else None
        if pr is None:
            return 1, "", f"GraphQL: Could not resolve to a PullRequest ({positional[:1]})\n"
        if sub == "view":
            view = {"url": _url(str(key)), "number": int(str(key).split("#")[1]), **pr}
            view.pop("checks", None)
            view.pop("comments", None)
            return 0, json.dumps(view), ""
        if sub == "edit":
            if "--title" in flags:
                pr["title"] = flags["--title"][0]
            if "--body-file" in flags:
                pr["body"] = Path(flags["--body-file"][0]).read_text(encoding="utf-8")
            pr.setdefault("labels", []).extend(flags.get("--add-label", []))
            pr.setdefault("reviewers", []).extend(flags.get("--add-reviewer", []))
            return 0, _url(str(key)) + "\n", ""
        if sub == "checks":
            return self._checks(pr, "--required" in flags)
        return 1, "", f"fake gh: unsupported pr subcommand {sub!r}\n"

    def _checks(self, pr: dict[str, Any], required: bool) -> tuple[int, str, str]:
        polls = pr.get("checks") or [[]]
        if required:
            current = polls[0]
            names = set(pr.get("required", []))
            listed = [c for c in current if c["name"] in names]
            if not listed:
                return (
                    1,
                    "",
                    f"no required checks reported on the '{pr.get('headRefName')}' branch\n",
                )
        else:
            listed = polls[0]
            if len(polls) > 1:
                pr["checks"] = polls[1:]
            if not listed:
                return 1, "", f"no checks reported on the '{pr.get('headRefName')}' branch\n"
        buckets = {c.get("bucket") for c in listed}
        code = 1 if buckets & {"fail", "cancel"} else 8 if "pending" in buckets else 0
        return code, json.dumps(listed), ""

    def _create(self, state: dict[str, Any], flags: dict[str, list[str]]) -> tuple[int, str, str]:
        repo = state.get("repo", "octo/app")
        number = 1 + max([int(k.split("#")[1]) for k in state["prs"]] or [0])
        key = f"{repo}#{number}"
        state["prs"][key] = {
            "title": flags.get("--title", [""])[0],
            "body": Path(flags["--body-file"][0]).read_text(encoding="utf-8")
            if "--body-file" in flags
            else "",
            "state": "OPEN",
            "isDraft": "--draft" in flags,
            "headRefName": flags.get("--head", [""])[0],
            "baseRefName": flags.get("--base", [""])[0],
            "author": state.get("login"),
            "labels": flags.get("--label", []),
            "reviewers": flags.get("--reviewer", []),
        }
        return 0, f"Creating pull request\n{_url(key)}\n", ""


def _url(key: str) -> str:
    repo, number = key.split("#")
    return f"https://github.com/{repo}/pull/{number}"


def write_fake_gh(folder: Path, state_path: Path) -> Path:
    """Write `folder/gh`: an executable that runs this fake over `state_path`."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "gh"
    script.write_text(  # the file itself, not the package: the fake imports nothing of the pack
        "#!/bin/sh\n"
        f"{STATE_ENV}='{state_path}' "
        f"exec '{sys.executable}' -I '{Path(__file__).resolve()}' \"$@\"\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def main() -> int:
    code, out, err = FakeGh(Path(os.environ[STATE_ENV])).run(sys.argv[:])
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
