"""`github_policy`, called with `FakeContext`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from gh_wrappers import call, fake_gh, policy_file


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
