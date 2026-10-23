"""`github_policy`, called with `FakeContext`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from gh_wrappers import context, fake_gh, load_wrapper, policy_file
from trestle.plugin.testing import FakeContext, call_plugin


def call(tmp_path: Path, name: str, args: dict[str, Any]) -> tuple[Any, FakeContext]:
    """Call the wrapper `name` with `FakeContext`: its answer as an agent reads it, and the
    context (commands run, artifacts attached)."""
    ctx = context(tmp_path)
    return call_plugin(getattr(load_wrapper(name), name), args, ctx=ctx), ctx


def test_answers_the_effective_policy_and_attaches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_gh(tmp_path, monkeypatch)
    path = policy_file(tmp_path, monkeypatch, '[checks]\nadd = ["build"]\n')
    monkeypatch.setenv("GH_HOST", "github.example.com")
    answer, ctx = call(tmp_path, "github_policy", {})
    assert answer["source"] == str(path) and len(answer["sha256"]) == 64
    assert answer["host"] == "github.example.com"
    assert answer["policy"]["checks"]["add"] == ["build"]
    attached = json.loads(ctx.artifacts["github-policy.json"])
    assert attached["checks"]["add"] == ["build"] and attached["sha256"] == answer["sha256"]
    assert fake.calls == []  # nothing ran, nothing reached GitHub
