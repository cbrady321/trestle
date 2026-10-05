"""Selftest for `tests/proof/trailers.py` (CM-1; L.P0-0d.7)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.proof import trailers as trailers_mod

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env={**_env(), **{}}
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _env() -> dict:
    import os

    return {**os.environ, **ENV}


def _commit(repo: Path, message: str) -> str:
    _git(repo, "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    return r


def test_one_trailer_per_merge_commit(repo):
    _commit(repo, "root")
    _commit(repo, "WR-Merge: P0-0a")
    commits = trailers_mod._commits("HEAD", cwd=repo)  # noqa: SLF001
    trailer_counts = [len(c.trailers()) for c in commits]
    assert trailer_counts == [0, 1]


def test_second_wr_merge_of_product_id_is_history_anomaly_not_load_error(repo):
    _commit(repo, "root")
    _commit(repo, "WR-Merge: CS-1")
    _commit(repo, "WR-Merge: CS-1")  # planted: a second product carrier
    found = trailers_mod.anomalies("HEAD", cwd=repo)
    assert any("second WR-Merge carrier of product id 'CS-1'" in f for f in found)

    _commit(repo, "two trailers\n\nWR-Merge: CS-2\nWR-Fix: CS-3")
    found2 = trailers_mod.anomalies("HEAD", cwd=repo)
    assert any("carries 2 trailers" in f for f in found2)


def test_landing_is_oldest_wr_merge_carrier_never_fix(repo):
    _commit(repo, "root")
    first = _commit(repo, "WR-Merge: CS-1")
    _commit(repo, "WR-Fix: CS-1")  # a later fix; never the landing
    assert trailers_mod.landing("CS-1", cwd=repo) == first
    assert trailers_mod.newest("CS-1", cwd=repo) == first  # product id: newest == oldest carrier


def test_checkpoint_id_newest_carrier(repo):
    _commit(repo, "root")
    first = _commit(repo, "WR-Merge: J0")
    second = _commit(repo, "a failed evaluation retry")
    third = _commit(repo, "WR-Merge: J0")
    assert first != third
    assert trailers_mod.landing("J0", cwd=repo) == first
    assert trailers_mod.newest("J0", cwd=repo) == third
    assert second  # keeps the chain honest; not itself a carrier
