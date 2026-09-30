"""L.RB-12.7: B's checkpoint record resolver (`record_facts.py`) and its two record-fact nodes.

`test_record_facts_planted` builds planted histories in throwaway git repositories (records, role-1
commits, carriers, a tag) and checks the resolver against them, both with P0's `record.select` and
`record.paired_docker` monkeypatched to planted returns (the resolver has no selection of its own)
and unpatched. The two record-fact nodes read the real repository at HEAD: with no role-2 candidate
and no `wr-ckpt/slice-b` tag they skip (UNPROVEN, never a pass); at a J-SLICE-B PR head, where
`select` returns the newest role-2 run's record, they judge the selected host-docker record.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.proof.b import record_facts as rf
from tests.proof.host import record as record_mod

ROOT = Path(__file__).resolve().parents[3]
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


def _history(tmp_path: Path, salt: str, *, older: dict[str, Any], newer: dict[str, Any]):
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


def _resolve(repo: Repo, head: str) -> rf.Pair | None:
    return rf.resolve(head, repo.path)


def _ancestry_only(record_: dict[str, Any], anchor: str, cwd: Path) -> tuple[bool, str | None]:
    """A lenient CM-6 (the sha is an ancestor of the anchor; a later product change is ignored): it
    makes both role-2 pairs admissible, which the real rule never does for a re-run."""
    from tests.proof import fence as fence_mod

    return fence_mod.is_ancestor(cwd, str(record_["sha"]), anchor), None


def test_record_facts_planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lanes = rf.checkpoint_lanes()
    assert lanes["slice-b"] == [
        "tests/fixtures/fossils/slice-b/**",
        "tests/proof/reviews/*-slice-b.toml",
    ]

    # 1. the re-run supersedes the failed attempt, at the PR head, at the carrier, and through the
    #    tag anchor once the checkpoint is tagged, whatever order the record files sort in
    orders: set[bool] = set()
    for i in range(40):
        repo, h = _history(tmp_path, f"-{i}", older=DIRTY_UNATTRIBUTED, newer=CLEAN)
        first_sorts_first = h["r1"] < h["r2"]
        orders.add(first_sorts_first)
        # PR head of the re-run: only the re-run's role-2 records are admissible (CM-6)
        at_head = _resolve(repo, h["p2"])
        assert shas(at_head) == (h["r2"], h["r2"]) and at_head.anchor == h["p2"]
        assert rf.diff_clean_problems(at_head.host_docker) == []
        # the first attempt's records at its own head are the first attempt's
        assert shas(_resolve(repo, h["p1"])) == (h["r1"], h["r1"])
        # the carrier (tagged) resolves the same pair as the PR head (B6-3)
        assert shas(_resolve(repo, h["k"])) == (h["r2"], h["r2"])
        # a later non-checkpoint head: its own per-merge record never passes the candidate test, so
        # the tag anchor answers, and the pair is still the re-run's
        through_tag = _resolve(repo, h["per_merge2"])
        assert shas(through_tag) == (h["r2"], h["r2"]) and through_tag.anchor == h["k"]
        if len(orders) == 2:
            break
    assert orders == {True, False}, "both sort orders of the two role-2 records were planted"

    # 2. both role-2 pairs admissible (lenient CM-6): the newer by ancestry wins, whatever order the
    #    files sort in, at the head and through the tag anchor
    monkeypatch.setattr(record_mod, "is_admissible", _ancestry_only)
    seen: set[bool] = set()
    for i in range(40):
        repo, h = _history(tmp_path, f"-lenient-{i}", older=CLEAN, newer=DIRTY_ENGINE)
        seen.add(h["r1"] < h["r2"])
        assert shas(_resolve(repo, h["p2"])) == (h["r2"], h["r2"])
        assert shas(_resolve(repo, h["per_merge2"])) == (h["r2"], h["r2"])
        pair = _resolve(repo, h["p2"])
        assert (
            rf.engine_untouched_problems(pair.host_docker) != []
        )  # the newer pair is the dirty one
        if len(seen) == 2:
            break
    assert seen == {True, False}
    monkeypatch.undo()

    # 3. the assertions fail on a dirty selected record, and pass when only the superseded one is
    repo, h = _history(tmp_path, "-dirty-old", older=DIRTY_UNATTRIBUTED, newer=CLEAN)
    pair = _resolve(repo, h["p2"])
    assert rf.diff_clean_problems(pair.host_docker) == []
    assert rf.engine_untouched_problems(pair.host_docker) == []
    repo, h = _history(tmp_path, "-dirty-new", older=CLEAN, newer=DIRTY_UNATTRIBUTED)
    assert "unattributed" in rf.diff_clean_problems(_resolve(repo, h["p2"]).host_docker)[0]
    repo, h = _history(tmp_path, "-engine", older=CLEAN, newer=DIRTY_ENGINE)
    assert (
        "engine_state_changed"
        in rf.engine_untouched_problems(_resolve(repo, h["p2"]).host_docker)[0]
    )
    assert rf.diff_clean_problems(None) != [] and rf.engine_untouched_problems(None) != []

    # 4. a triage, per-merge or catch-up record at the head is never read: before the tag there is
    #    no anchor, and the pair is `None` (the nodes skip)
    repo = Repo(tmp_path / "per-merge-only")
    x = repo.product("1")
    repo.records(x, proc=CLEAN, docker=CLEAN)
    assert _resolve(repo, "HEAD") is None
    # ... and the same at a head after a role-1 commit that has no records yet
    repo.role1("no records yet", x)
    assert _resolve(repo, "HEAD") is None

    # 5. a `mode = preflight` record is never a role-2 record, and a preflight host-docker record
    #    pairs as none
    repo = Repo(tmp_path / "preflight")
    x = repo.product("1")
    r = repo.role1("attempt", x)
    repo.records(r, proc={"mode": "preflight"}, docker=CLEAN)
    assert _resolve(repo, "HEAD") is None  # the selected host-proc record is a preflight
    repo = Repo(tmp_path / "preflight-docker")
    x = repo.product("1")
    r = repo.role1("attempt", x)
    repo.records(r, proc=CLEAN, docker={"mode": "preflight", "status": "PRECONDITION_UNMET"})
    pair = _resolve(repo, "HEAD")
    assert pair is not None and pair.host_docker is None  # counts as none

    # 6. a host-docker record with no host-proc record at its sha is never paired
    repo = Repo(tmp_path / "docker-only")
    x = repo.product("1")
    r = repo.role1("attempt", x)
    repo.records(r, proc=None, docker=CLEAN)
    assert _resolve(repo, "HEAD") is None

    # 7. a record whose sha is not an ancestor of the anchor, or after a product change since it
    #    (CM-6), is inadmissible: the resolver returns nothing for it
    repo = Repo(tmp_path / "inadmissible")
    x = repo.product("1")
    r = repo.role1("attempt", x)
    repo.records(r, proc=CLEAN, docker=CLEAN)
    repo.product("2")
    assert _resolve(repo, "HEAD") is None  # a product path changed since the record's sha
    repo.git("checkout", "-q", "-b", "side", r)
    side = repo.role1("side attempt", x)
    repo.git("checkout", "-q", "master")
    assert _resolve(repo, "master") is None
    assert side != r

    # 8. a tag on a commit that is not the newest J-SLICE-B carrier is not the checkpoint's
    repo = Repo(tmp_path / "bad-tag")
    x = repo.product("1")
    r = repo.role1("attempt", x)
    repo.records(r, proc=CLEAN, docker=CLEAN)
    repo.git("tag", "wr-ckpt/slice-b", r)
    later = repo.product("2")
    repo.records(later, proc=CLEAN, docker=CLEAN)
    assert rf.tag_commit(repo.path) is None
    assert _resolve(repo, "HEAD") is None


def test_resolver_returns_what_the_patched_p0_functions_return_for_its_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolver has no selection of its own: with `record.select` and `record.paired_docker`
    patched to planted returns, it returns exactly those, for the anchor it passed."""
    repo = Repo(tmp_path / "patched")
    x = repo.product("1")
    r = repo.role1("attempt", x)
    per_merge = repo.product("2")
    calls: list[tuple[str, str]] = []
    planted_proc = record("host-proc", r)
    planted_docker = record("host-docker", r, **DIRTY_UNATTRIBUTED)
    other_proc = record("host-proc", per_merge)  # a per-merge record: not a role-1 commit's

    def select(gate: str, anchor: str, cwd: Path | None = None) -> dict[str, Any] | None:
        calls.append((gate, anchor))
        return {"HEAD": planted_proc, "later": other_proc}.get(anchor)

    def paired(proc: dict[str, Any], cwd: Path | None = None) -> dict[str, Any] | None:
        calls.append(("pair", proc["sha"]))
        return planted_docker if proc is planted_proc else None

    monkeypatch.setattr(record_mod, "select", select)
    monkeypatch.setattr(record_mod, "paired_docker", paired)
    # a checkpoint record at the head: returned as-is, anchored at the head that was passed
    pair = rf.resolve("HEAD", repo.path)
    assert (
        pair is not None and pair.host_proc is planted_proc and pair.host_docker is planted_docker
    )
    assert pair.anchor == "HEAD"
    assert calls == [("host-proc", "HEAD"), ("pair", r)]
    # a non-checkpoint record at the head and no tag: nothing
    calls.clear()
    assert rf.resolve("later", repo.path) is None
    assert calls == [("host-proc", "later")]
    # the same head once the checkpoint is tagged: the tag commit is the anchor it passes on
    tagged = repo.carrier()
    calls.clear()

    def select_tag(gate: str, anchor: str, cwd: Path | None = None) -> dict[str, Any] | None:
        calls.append((gate, anchor))
        return other_proc if anchor == "later" else planted_proc  # the tag commit selects the pair

    monkeypatch.setattr(record_mod, "select", select_tag)
    pair = rf.resolve("later", repo.path)
    assert pair is not None and pair.anchor == tagged and pair.host_proc is planted_proc
    assert calls == [("host-proc", "later"), ("host-proc", tagged), ("pair", r)]


def test_the_candidate_test_is_a_role1_commit_or_nothing(tmp_path: Path) -> None:
    """A role-1 commit's first-parent diff lies within the checkpoint lane's globs and includes its
    review record; a fossil-only commit, a review plus a product change, and the base are not."""
    lanes = rf.checkpoint_lanes()
    repo = Repo(tmp_path / "role1")
    x = repo.product("1")
    good = repo.role1("attempt", x)
    fossil_only = repo.commit({FOSSIL: "no review"}, "fossil only")
    mixed = repo.commit({FOSSIL: "mixed", REVIEW: 'sha = "m"\n', "src.txt": "9"}, "mixed")
    base = repo.git("rev-list", "--max-parents=0", "HEAD")
    assert rf.is_role1_commit(good, repo.path, lanes)
    assert not rf.is_role1_commit(fossil_only, repo.path, lanes)
    assert not rf.is_role1_commit(mixed, repo.path, lanes)
    assert not rf.is_role1_commit(base, repo.path, lanes)  # no parent
    assert rf.is_checkpoint_record(record("host-proc", good), repo.path, lanes)
    assert not rf.is_checkpoint_record(
        record("host-proc", good, mode="preflight"), repo.path, lanes
    )
    assert not rf.is_checkpoint_record(record("host-proc", x), repo.path, lanes)


def test_no_selection_pairing_or_pass_set_is_implemented_here() -> None:
    """B implements none of P0's three record functions (CM-6): it calls them."""
    pattern = re.compile(r"def (select|paired_docker|pass_set_violations)[(]")
    for path in (
        ROOT / "tests/proof/b/record_facts.py",
        ROOT / "tests/proof/ckpt/slice_b.py",
    ):
        assert not pattern.search(path.read_text()), path


# ---------------------------------------------------------------------------
# the two record-fact nodes (read the real repository at HEAD)
# ---------------------------------------------------------------------------


def _selected_docker() -> dict[str, Any]:
    pair = rf.resolve("HEAD", ROOT)
    if pair is None or pair.host_docker is None:
        pytest.skip("no checkpoint role-2 record at HEAD and no wr-ckpt/slice-b tag: UNPROVEN")
    return pair.host_docker


@pytest.mark.proves("WR-PROOF-6", "WR-PROOF-6:diff-clean-checkpoint", "B", "B", "LOGIC", "CI")
def test_checkpoint_record_diff_clean() -> None:
    """The selected host-docker record's `diff.unattributed` is empty; skips with no candidate."""
    assert rf.diff_clean_problems(_selected_docker()) == []


@pytest.mark.proves("WR-CON-3", "WR-CON-3:engine-untouched-checkpoint", "B", "B", "LOGIC", "CI")
def test_checkpoint_record_engine_untouched() -> None:
    """The selected host-docker record's `diff.engine_state_changed` is false."""
    assert rf.engine_untouched_problems(_selected_docker()) == []
