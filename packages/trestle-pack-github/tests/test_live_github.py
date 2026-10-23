"""Live, read-only checks against real GitHub with the host's `gh`, gated like the docker node:
skipped unless `TRESTLE_GITHUB_LIVE_PR` names a pull request (its URL), so no test points at a
real repository without a flag. They only read: nothing is pushed, posted or opened."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from gh_wrappers import context, load_wrapper
from trestle.plugin.testing import FakeContext, call_plugin


def call(tmp_path: Path, name: str, args: dict[str, Any]) -> tuple[Any, FakeContext]:
    """Call the wrapper `name` with `FakeContext`: its answer as an agent reads it, and the
    context (commands run, artifacts attached)."""
    ctx = context(tmp_path)
    return call_plugin(getattr(load_wrapper(name), name), args, ctx=ctx), ctx


LIVE_PR = os.environ.get("TRESTLE_GITHUB_LIVE_PR", "")

pytestmark = pytest.mark.skipif(
    not LIVE_PR or shutil.which("gh") is None,
    reason="live github check: set TRESTLE_GITHUB_LIVE_PR to a PR URL, with gh logged in",
)


@pytest.fixture(autouse=True)
def _real_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRESTLE_GH_PATH", raising=False)
    monkeypatch.delenv("TRESTLE_GITHUB_POLICY", raising=False)


def test_live_status_reads_a_verdict(tmp_path: Path) -> None:
    answer, _ = call(tmp_path, "github_pr_status", {"targets": [LIVE_PR]})
    status = answer["prs"][0]
    assert status["error"] is None, status
    assert status["verdict"] in {"passed", "failed", "pending", "no_checks"}
    assert status["state"] in {"OPEN", "CLOSED", "MERGED"}


def test_live_comments_read(tmp_path: Path) -> None:
    answer, _ = call(tmp_path, "github_pr_comments", {"targets": [LIVE_PR]})
    assert answer["ok"] is True, answer
