"""Selftest for the K-doc landing rule (CM-0 G-COMPAT; L.P0-0d.10)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.proof import kdoc as kdoc_mod

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _sh(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _sh(r, "init", "-q", "-b", "master")
    (r / "code.txt").write_text("x")
    _sh(r, "add", "code.txt")
    _sh(r, "commit", "-q", "-m", "root")
    return r


K_ITEMS = [
    {"id": "K-1", "landing_merge": "CK-1", "docs": ["docs/a.md"]},
]


def test_planted_k_diff_without_doc_reported(tmp_path):
    repo = _repo(tmp_path)
    (repo / "code.txt").write_text("y")
    _sh(repo, "add", "code.txt")
    _sh(repo, "commit", "-q", "-m", "WR-Merge: CK-1")
    landing_sha = _sh(repo, "rev-parse", "HEAD")
    missing = kdoc_mod.missing_docs("CK-1", landing_sha, cwd=repo, k_items=K_ITEMS)
    assert missing == ["docs/a.md"]


def test_kdoc_reads_landing_commit_not_fix(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("doc")
    (repo / "code.txt").write_text("y")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "WR-Merge: CK-1")
    landing_sha = _sh(repo, "rev-parse", "HEAD")

    # a later WR-Fix touches code only; missing_docs is never asked about it
    (repo / "code.txt").write_text("z")
    _sh(repo, "add", "code.txt")
    _sh(repo, "commit", "-q", "-m", "WR-Fix: CK-1")

    missing_at_landing = kdoc_mod.missing_docs("CK-1", landing_sha, cwd=repo, k_items=K_ITEMS)
    assert missing_at_landing == []

    from tests.proof import trailers as trailers_mod

    assert trailers_mod.landing("CK-1", cwd=repo) == landing_sha


def test_missing_docs_empty_when_landing_edits_every_mapped_doc(tmp_path):
    repo = _repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("doc")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "WR-Merge: CK-1")
    sha = _sh(repo, "rev-parse", "HEAD")
    assert kdoc_mod.missing_docs("CK-1", sha, cwd=repo, k_items=K_ITEMS) == []
