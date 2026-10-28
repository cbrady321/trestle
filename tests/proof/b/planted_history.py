"""Planted checkpoint histories for the record-fact and checkpoint-condition self-tests (L.RB-12.4).

Not a test module (never matches `test_*.py`, DM-80). `Repo` builds a throwaway git repository
shaped like a J-SLICE-B history: per-merge records, a first attempt (a role-1 commit, then its
role-2 records), a failure-exit re-run (a later role-1 commit that fixes a fossil and re-records the
review, then its role-2 records), the carrier (`WR-Merge: J-SLICE-B`, tagged `wr-ckpt/slice-b`), and
a later product change with its own per-merge record. `tests/proof/b/test_record_facts.py`
(L.RB-12.7) and `tests/proof/b/test_ckpt_slice_b.py` (L.RB-12.4) both plant through it.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tests.proof import fence as fence_mod
from tests.proof.b import record_facts as rf

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
FOSSIL = "tests/fixtures/fossils/slice-b/s1/data.txt"
REVIEW = "tests/proof/reviews/RV-1-slice-b.toml"


class Repo:
    """A throwaway repository shaped like the checkpoint history (all commits made by `git`)."""

    def __init__(self, path: Path, salt: str = "") -> None:
        self.path = path
        self.salt = salt
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "master")
        self.commit({"src.txt": "0"}, "base")

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, env=ENV, check=False
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    def commit(self, files: dict[str, str], message: str, allow_empty: bool = False) -> str:
        for name, text in files.items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text(text)
        self.git("add", "-A")
        subject, _, body = message.partition("\n")
        args = ["commit", "-q", "-m", f"{subject}{self.salt}" + (f"\n{body}" if body else "")]
        self.git(*args, *(["--allow-empty"] if allow_empty else []))
        return self.git("rev-parse", "HEAD")

    def product(self, text: str) -> str:
        return self.commit({"src.txt": text}, f"product {text}")

    def role1(self, tag: str, reviewed: str) -> str:
        """A role-1 commit (CM-5): a fossil and a re-recorded review carrying the reviewed sha."""
        return self.commit({FOSSIL: tag, REVIEW: f'sha = "{reviewed}"\n'}, f"role 1 {tag}")

    def records(
        self, sha: str, *, proc: dict[str, Any] | None, docker: dict[str, Any] | None
    ) -> str:
        """A records-only commit for `sha` (a record for a gate is omitted when its arg is None)."""
        files = {}
        if proc is not None:
            files[f"tests/proof/host/host-proc/{sha}.json"] = json.dumps(
                record("host-proc", sha, **proc)
            )
        if docker is not None:
            files[f"tests/proof/host/host-docker/{sha}.json"] = json.dumps(
                record("host-docker", sha, **docker)
            )
        return self.commit(files, f"records {sha[:7]}")

    def carrier(self) -> str:
        sha = self.commit({}, "carrier\n\nWR-Merge: J-SLICE-B", allow_empty=True)
        self.git("tag", "wr-ckpt/slice-b", sha)
        return sha


def record(gate: str, sha: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "gate": gate,
        "sha": sha,
        "mode": "run",
        "python": "3.12.8",
        "platform": "test",
        "results": [],
        "status": "PASSED",
    }
    if gate == "host-docker":
        base["diff"] = {"unattributed": [], "engine_state_changed": False}
    return {**base, **fields}


CLEAN: dict[str, Any] = {}
DIRTY_UNATTRIBUTED: dict[str, Any] = {
    "diff": {"unattributed": ["c1"], "engine_state_changed": False}
}
DIRTY_ENGINE: dict[str, Any] = {"diff": {"unattributed": [], "engine_state_changed": True}}


def shas(pair: rf.Pair | None) -> tuple[str, str | None] | None:
    if pair is None:
        return None
    return pair.host_proc["sha"], None if pair.host_docker is None else pair.host_docker["sha"]


# ---------------------------------------------------------------------------
# the resolver over planted histories
# ---------------------------------------------------------------------------


def history(
    tmp_path: Path, salt: str, *, older: dict[str, Any], newer: dict[str, Any]
) -> tuple[Repo, dict[str, str]]:
    """Base, a per-merge record, a first attempt (role 1 at R1, role-2 records at P1), a re-run
    (role 1 at R2 fixing the fossil and re-recording the review, records at P2), the carrier
    (tagged), then a product change with its own per-merge record."""
    repo = Repo(tmp_path / f"repo{salt}", salt)
    x = repo.product("1")
    per_merge = repo.records(x, proc=CLEAN, docker=CLEAN)
    r1 = repo.role1("first attempt", x)
    p1 = repo.records(r1, proc=CLEAN, docker=older)
    r2 = repo.role1("re-run fixes the fossil", r1)
    p2 = repo.records(r2, proc=CLEAN, docker=newer)
    k = repo.carrier()
    later = repo.product("2")
    per_merge2 = repo.records(later, proc=CLEAN, docker=CLEAN)
    return repo, {
        "x": x,
        "per_merge": per_merge,
        "r1": r1,
        "p1": p1,
        "r2": r2,
        "p2": p2,
        "k": k,
        "later": later,
        "per_merge2": per_merge2,
    }


def ancestry_only(record_: dict[str, Any], anchor: str, cwd: Path) -> tuple[bool, str | None]:
    """A lenient CM-6 (the sha is an ancestor of the anchor; a later product change is ignored): it
    makes both role-2 pairs admissible, which the real rule never does for a re-run."""
    return fence_mod.is_ancestor(cwd, str(record_["sha"]), anchor), None


def land(repo: Repo, head: str, onto: str) -> str:
    """The landing merge commit of a PR head (CM-2 R3: the branch is up to date, so the merge
    commit's tree is the head's tree and adds no record): `head` merged `--no-ff` onto `onto`, an
    ancestor of it, carrying `WR-Merge: J-SLICE-B`."""
    repo.git("branch", "-f", "pr-head", head)
    repo.git("checkout", "-q", "-b", f"landing{repo.salt}", onto)
    repo.git(
        "merge",
        "-q",
        "--no-ff",
        "-m",
        f"land J-SLICE-B{repo.salt}\n\nWR-Merge: J-SLICE-B",
        "pr-head",
    )
    return repo.git("rev-parse", "HEAD")
