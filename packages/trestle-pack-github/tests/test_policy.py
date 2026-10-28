"""The policy file format: defaults, keys over defaults, and refusals that name the key."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from trestle.plugin import Blocked

from trestle_pack_github.policy import (
    POLICY_ENV,
    GithubPolicy,
    PolicyInvalid,
    effective_policy,
    load_policy,
    parse_policy,
)


def _parse(text: str) -> GithubPolicy:
    return parse_policy(tomllib.loads("policy_version = 1\n" + text))


def test_no_file_named_is_the_generic_defaults() -> None:
    loaded = load_policy({})
    assert loaded.echo.source == "defaults" and loaded.echo.sha256 is None
    policy = loaded.policy
    assert policy.bots.logins == () and policy.bots.patterns == ()  # no vendor names (P-21)
    assert policy.checks.required == "branch_protection"
    assert policy.checks.no_checks == "report"  # never counted as passed by default
    assert policy.open.edit_any_pr is False and policy.open.branch_pattern == ""


def test_a_file_changes_only_its_keys() -> None:
    policy = _parse('[bots]\nlogins = ["review-bot"]\n[checks]\nno_checks = "pass"\ngrace_s = 30\n')
    assert policy.bots.logins == ("review-bot",)
    assert policy.checks.no_checks == "pass" and policy.checks.grace_s == 30.0
    assert policy.checks.poll_s == 15.0 and policy.triage == GithubPolicy().triage


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("[bot]\nlogins = []\n", "unknown key 'bot'"),
        ("[checks]\nrequire = 'all'\n", "[checks] unknown key 'require'"),
        ("[checks]\nrequired = 'some'\n", "[checks] required must be one of"),
        ("[checks]\npoll_s = 1\n", "[checks] poll_s must be at least 5"),
        ("[open]\ndraft = 'yes'\n", "[open] draft must be true or false"),
        ("[open]\nbranch_pattern = '('\n", "[open] branch_pattern: '(' is not a regular"),
        ("[bots]\npatterns = ['[']\n", "[bots] patterns: '[' is not a regular"),
        ("[open]\nlabels = 'x'\n", "[open] labels must be a list"),
    ],
)
def test_a_bad_key_is_refused_by_name(text: str, names: str) -> None:
    with pytest.raises(PolicyInvalid, match=names.replace("[", r"\[").replace("(", r"\(")):
        _parse(text)


def test_the_version_is_required() -> None:
    with pytest.raises(PolicyInvalid, match="policy_version must be 1"):
        parse_policy({"checks": {}})


def test_a_named_file_reports_its_digest(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text("policy_version = 1\n[open]\nremote = 'upstream'\n", encoding="utf-8")
    loaded = load_policy({POLICY_ENV: str(path)})
    assert loaded.policy.open.remote == "upstream"
    assert loaded.echo.source == str(path) and len(loaded.echo.sha256 or "") == 64
    assert effective_policy(loaded)["open"]["remote"] == "upstream"


@pytest.mark.parametrize("body", [None, "policy_version = 2\n", "not toml ["])
def test_a_missing_or_invalid_file_is_blocked_for_the_operator(
    tmp_path: Path, body: str | None
) -> None:
    path = tmp_path / "p.toml"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    with pytest.raises(Blocked) as raised:
        load_policy({POLICY_ENV: str(path)})
    assert raised.value.code == "github.policy_invalid"
    assert str(path) in raised.value.human_action
    assert raised.value.resend == "succeeds_after_action"


def test_the_readme_example_policy_parses() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    block = readme.split("```toml\n# github-policy.toml", 1)[1].split("```", 1)[0]
    parse_policy(tomllib.loads(block))
