"""Shared by the wrapper tests: load a wrapper from the pack's `plugins/`, and a `FakeContext`
whose `gh` is the fake CLI (`trestle_pack_github.fakes.gh_cli`) over a JSON state file.

Commands run for real through `FakeContext.exec`: the fake gh is an executable script, and git is
the host's own, on a repository under `tmp_path` whose `origin` is a local bare repository. No
test reaches GitHub."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from trestle.plugin.testing import FakeContext

from trestle_pack_github.fakes.gh_cli import FakeGh, write_fake_gh

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"


def load_wrapper(name: str) -> ModuleType:
    """The wrapper module `plugins/<name>.py`, freshly loaded."""
    spec = importlib.util.spec_from_file_location(f"wrapper_{name}", PLUGINS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass annotations resolve through sys.modules
    spec.loader.exec_module(module)
    return module


def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **state: Any) -> FakeGh:
    """A fake gh the wrappers find through `TRESTLE_GH_PATH`; polls never really sleep."""
    fake = FakeGh(tmp_path / "gh-state.json")
    fake.set(**state)
    monkeypatch.setenv("TRESTLE_GH_PATH", str(write_fake_gh(tmp_path / "bin", fake.state_path)))
    monkeypatch.delenv("GH_HOST", raising=False)
    monkeypatch.delenv("TRESTLE_GITHUB_POLICY", raising=False)
    monkeypatch.setattr("trestle_pack_github.checks._sleep", lambda _s: None)
    return fake


def policy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    path = tmp_path / "github-policy.toml"
    path.write_text("policy_version = 1\n" + body, encoding="utf-8")
    monkeypatch.setenv("TRESTLE_GITHUB_POLICY", str(path))
    return path


def context(tmp_path: Path) -> FakeContext:
    return FakeContext(tmp_path / "ctx")


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *argv],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def checkout(tmp_path: Path, branch: str = "feat/x") -> tuple[Path, Path]:
    """A repository on `branch` (one commit on `main` before it), and its bare `origin`."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.name", "t")
    git(work, "config", "user.email", "t@example.com")
    (work / "README.md").write_text("hello\n", encoding="utf-8")
    git(work, "add", ".")
    git(work, "commit", "-qm", "one")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    if branch != "main":
        git(work, "switch", "-q", "-c", branch)
    return work, origin


def pr(**fields: Any) -> dict[str, Any]:
    base = {
        "title": "Add x", "state": "OPEN", "isDraft": False, "headRefName": "feat/x",
        "headRefOid": "abc123", "reviewDecision": "REVIEW_REQUIRED", "mergeable": "MERGEABLE",
        "author": "octo",
    }  # fmt: skip
    base.update(fields)
    return base
