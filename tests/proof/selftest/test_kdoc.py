"""Selftest for the K-doc landing rule (CM-0 G-COMPAT; L.P0-0d.10)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.proof import kdoc as kdoc_mod
from tests.proof import meta as meta_mod

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
    r.mkdir(parents=True)
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


# --- L.CZ.3: `kdoc --enforce --history wr-ckpt/core..HEAD` ----------------------------------

ITEMS = [
    {"id": "K-1", "landing_merge": "CK-1", "docs": ["docs/a.md"], "rows": ["WR-R-1"]},
    {"id": "K-2", "landing_merge": "CK-2", "docs": ["docs/b.md"], "rows": ["WR-R-2"]},
    {"id": "K-16", "landing_merge": "", "docs": ["docs/a.md"], "confirm": "yes (conditional)"},
]
PRESENT = lambda node: (0, "")  # noqa: E731


def _land(repo: Path, merge_id: str, *files: str) -> str:
    for name in files:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{merge_id} {name}")
    (repo / "code.txt").write_text(merge_id)
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", f"WR-Merge: {merge_id}")
    return _sh(repo, "rev-parse", "HEAD")


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:slice-row-doc-syncs", "core", "CZ", "LOGIC", "CI")
def test_planted_k_row_without_doc_fails(tmp_path, capsys, monkeypatch):
    repo = _repo(tmp_path)
    _land(repo, "CK-1", "docs/a.md")
    _land(repo, "CK-2")  # the planted K diff: its landing does not edit docs/b.md
    found = meta_mod.kdoc_problems(ITEMS, cwd=repo, presence=PRESENT)
    assert found == [
        f"K-2: CK-2 landed at {_sh(repo, 'rev-parse', 'HEAD')[:12]} without editing ['docs/b.md']"
    ]

    # a landing that edits its doc passes, and the conditional K-16 (no landing merge) is skipped
    ok = _repo(tmp_path / "ok")
    _land(ok, "CK-1", "docs/a.md")
    _land(ok, "CK-2", "docs/b.md")
    assert meta_mod.kdoc_problems(ITEMS, cwd=ok, presence=PRESENT) == []

    # a K-item whose landing merge has not landed, and one with no merge that is not conditional
    early = _repo(tmp_path / "early")
    _land(early, "CK-1", "docs/a.md")
    found = meta_mod.kdoc_problems(ITEMS, cwd=early, presence=PRESENT)
    assert found == ["K-2: landing merge CK-2 has not landed"]
    bare = [{"id": "K-99", "landing_merge": "", "docs": [], "rows": []}]
    assert meta_mod.kdoc_problems(bare, cwd=ok, presence=PRESENT) == [
        "K-99: no landing merge and not conditional"
    ]

    # the row closure and the RV-1 / RV-2 presence checks are part of the verdict
    rows_open = lambda rows: [f"{r} has no passing test" for r in rows]  # noqa: E731
    found = meta_mod.kdoc_problems(ITEMS, cwd=ok, row_problems=rows_open, presence=PRESENT)
    assert found == ["K-1: WR-R-1 has no passing test", "K-2: WR-R-2 has no passing test"]
    failing = lambda node: (1, "not found") if "RV-2" in node or "stub_labels" in node else (0, "")  # noqa: E731
    found = meta_mod.kdoc_problems(ITEMS, cwd=ok, presence=failing)
    assert len(found) == 1 and found[0].startswith("RV-2 presence check")

    # the command exits 1 on the planted diff and 0 on the clean history
    rows_toml = tmp_path / "row_owners.toml"
    rows_toml.write_text('[[row]]\nid = "WR-R-1"\n[[row]]\nid = "WR-R-2"\n')
    monkeypatch.setattr(meta_mod, "ROW_OWNERS_PATH", rows_toml)
    labels = [
        {"id": f"{row}:x", "row": row, "tier": "LOGIC", "venue": "CI", "posture": "claim"}
        for row in ("WR-R-1", "WR-R-2")
    ]
    proven = {f"{row}:x": {"status": "PROVEN"} for row in ("WR-R-1", "WR-R-2")}
    world = meta_mod.EnforceWorld("ci", report=proven, labels=labels)
    monkeypatch.setattr(meta_mod, "live_world", lambda scope, commit="HEAD": world)
    monkeypatch.setattr(meta_mod, "_run_presence", PRESENT)
    monkeypatch.setattr(kdoc_mod, "load_k_doc_map", lambda: ITEMS)
    for repo_, want in ((repo, 1), (ok, 0)):
        monkeypatch.setattr(meta_mod, "ROOT", repo_)
        args = meta_mod.build_parser().parse_args(["kdoc", "--enforce"])
        assert meta_mod.cmd_kdoc(args) == want
    capsys.readouterr()


def test_history_bounds_the_doc_check_to_landings_in_range(tmp_path):
    repo = _repo(tmp_path)
    _land(repo, "CK-1")  # lands without docs/a.md, before the range starts
    boundary = _sh(repo, "rev-parse", "HEAD")
    _land(repo, "CK-2", "docs/b.md")
    # inside the range only CK-2 landed: K-1's earlier landing was K-doc-checked by `fence merge`
    assert (
        meta_mod.kdoc_problems(ITEMS, cwd=repo, history=f"{boundary}..HEAD", presence=PRESENT) == []
    )
    # with the whole history in range K-1's missing doc is a miss
    found = meta_mod.kdoc_problems(ITEMS, cwd=repo, history="HEAD", presence=PRESENT)
    assert len(found) == 1 and found[0].startswith("K-1: CK-1 landed at")
    # an unreadable range is refused, never read as "nothing landed in it"
    refused = meta_mod.kdoc_problems(
        ITEMS, cwd=repo, history="wr-ckpt/nope..HEAD", presence=PRESENT
    )
    assert len(refused) == 1 and "not readable" in refused[0]
