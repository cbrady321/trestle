"""R4a: a plain plugin raises `Blocked(code, human_action, resend)` and its run's answer reads
`blocked` with that human action, the code namespaced `plugin.<code>`.

Proven against a stub `aws` that reports an expired login: no real AWS, no credentials."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from trestle.common.types import RunView
from trestle.plugin import Blocked
from trestle.server.main import create_kernel

AWS_PLUGIN = """
from trestle.plugin import Blocked, Context, trestle


@trestle
def aws_preflight(ctx: Context, profile: str = "dev") -> dict[str, str]:
    who = ctx.exec(["aws", "sts", "get-caller-identity", "--profile", profile], timeout=30)
    if not who.ok:
        if "expired" in who.stderr or "sso login" in who.stderr:
            raise Blocked(
                "aws_login",
                f"Run `aws sso login --profile {profile}`, then send the same request again.",
            )
        raise RuntimeError(who.stderr)
    return {"identity": who.stdout.strip()}
"""

STUB_AWS = """#!/bin/sh
echo "Error when retrieving token from sso: Token has expired and refresh failed" >&2
exit 255
"""


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "aws_preflight.py").write_text(AWS_PLUGIN, encoding="utf-8")
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    aws = stub_dir / "aws"
    aws.write_text(STUB_AWS, encoding="utf-8")
    aws.chmod(aws.stat().st_mode | stat.S_IXUSR)
    # the operator's PATH for plugin runs puts the stub first (G1)
    (home / "config.toml").write_text(
        f'[plugins.env]\nPATH = "{stub_dir}:/usr/bin:/bin"\n', encoding="utf-8"
    )
    return home, plugins


def test_a_plugin_that_finds_aws_logged_out_ends_blocked(tmp_path: Path) -> None:
    home, plugins = _home(tmp_path)
    kernel = create_kernel(home=home, plugin_dirs=[plugins], skip_recovery=True)
    view = kernel.control.run(plugin="aws_preflight", args={"profile": "dev"}, wait_ms=20_000)
    assert isinstance(view, RunView), view
    assert view.state == "succeeded"  # the plugin ended normally; the verdict is the answer's
    answer = view.answer
    assert answer is not None
    assert answer["outcome"] == "blocked"
    primary = answer["primary"]
    assert primary["node_class"] == "blocked"
    assert primary["code"] == "plugin.aws_login"
    assert primary["human_action"] == (
        "Run `aws sso login --profile dev`, then send the same request again."
    )
    assert primary["resend"] == "succeeds_after_action"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("Bad Code", "x"), "lower-case"),
        (("execution.x!", "x"), "lower-case"),
        (("aws_login", "  "), "human action"),
        (("aws_login", "x", "maybe"), "resend"),
    ],
)
def test_blocked_refuses_a_malformed_record(args: tuple[str, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Blocked(*args)
