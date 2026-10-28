"""B's one resolver of checkpoint host records (L.RB-12.7; CM-6, DM-83 (4)/(5)).

Helper, never a `test_*.py` name (DM-80). It is used by `test_record_facts.py`'s two record-fact
nodes and by `tests/proof/ckpt/slice_b.py`'s condition (c) (L.RB-12.4). It implements **no
selection, no pairing and no pass set**: it calls P0's `record.select` and `record.paired_docker`
(`tests/proof/host/record.py`, L.P0-0d.3) through the module, so a test that patches them is
answered by the patched functions, and it reads commits through P0's `WR-Merge` trailer reader
(`trailers.newest`, CM-1; L.P0-0d.7). It relies on the full-history checkout with tags of P0's
`test` job (`fetch-depth: 0`, L.P0-0a.6, IC-6): in a shallow checkout a record's sha is absent and
`record.is_admissible` raises `MissingShaError`, which this module lets through.

**The anchor: this module is the one statement of B's record-fact anchor, which CM-6 cites.**
`resolve(head)` reads `p = record.select("host-proc", A)` and `record.paired_docker(p)` where

* A = HEAD when the record `select` returns at HEAD is a checkpoint role-2 record (below), else
* A = the `wr-ckpt/slice-b` commit, when that tag names a `WR-Merge: J-SLICE-B` carrier;
* with neither (before the tag, on a head whose selected record is a triage or per-merge record, or
  none) `resolve` returns `None` and the record-fact nodes skip.

**The candidate test (B's only own logic), applied to the selected record only.** A selected
host-proc record is a checkpoint role-2 record when it is a `mode = run` record whose sha is a
J-SLICE-B or J-ROOT role-1 commit: a commit whose first-parent diff lies within that checkpoint
lane's globs (CM-5) and includes its `tests/proof/reviews/*-<ckpt>.toml`, which role 1 always
re-records with the reviewed `sha`, so the commit is never empty. That covers a first attempt, a
re-run under CM-5's one failure exit (a new PR on the checkpoint gate branch that re-runs roles 1-2)
and a checkpoint the landing loop returned "checkpoint rebased" (roles 1-2 re-run, CM-3). A
per-merge MJ record, an RB-13 catch-up record and L.RB-13.1's triage record never pass it, and a
paired host-docker record with `mode = preflight` counts as none. `select` already returns the
record newest by ancestry among the admissible ones (CM-6), so a re-run supersedes the failed
attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.proof import fence as fence_mod
from tests.proof import trailers
from tests.proof.host import record as record_mod

ROOT = Path(__file__).resolve().parents[3]

# checkpoint gate merge id -> the name its review records and lane carry (`*-<name>.toml`)
CHECKPOINTS = {"J-SLICE-B": "slice-b", "J-ROOT": "root"}
TAG = "wr-ckpt/slice-b"
TRIGGER_MERGE = "J-SLICE-B"


@dataclass(frozen=True)
class Pair:
    """The role-2 pair one anchor resolves: the host-proc record `record.select` returned and the
    host-docker record `record.paired_docker` returned for it (`None` when there is none, or when it
    is a `preflight` record)."""

    anchor: str
    host_proc: dict[str, Any]
    host_docker: dict[str, Any] | None


def checkpoint_lanes(config: fence_mod.FenceConfig | None = None) -> dict[str, list[str]]:
    """Checkpoint name (`slice-b`, `root`) -> its lane's globs (CM-5), for each checkpoint whose
    gate the fence declares; a checkpoint the fence does not know yet contributes nothing."""
    config = config or fence_mod.load_fence()
    lanes: dict[str, list[str]] = {}
    for gate in config.gates:
        if gate.merge not in CHECKPOINTS:
            continue
        name = CHECKPOINTS[gate.merge]
        for lane in config.lanes:
            if gate.branch.startswith(lane.branch_prefix):
                lanes[name] = list(lane.globs)
    return lanes


def is_role1_commit(sha: str, cwd: Path, lanes: dict[str, list[str]]) -> bool:
    """A commit whose first-parent diff lies within one checkpoint lane's globs and includes that
    checkpoint's `tests/proof/reviews/*-<ckpt>.toml` (role 1 always re-records a review, so the
    commit is never empty). A root commit and a commit with no parent are not role-1 commits."""
    parent = fence_mod._git(cwd, "rev-parse", "--verify", "-q", f"{sha}^1")  # noqa: SLF001
    if parent.returncode != 0:
        return False
    paths = fence_mod.diff_paths(cwd, parent.stdout.strip(), sha)
    if not paths:
        return False
    for name, globs in lanes.items():
        review = f"tests/proof/reviews/*-{name}.toml"
        if all(fence_mod.glob_match(p, globs) for p in paths) and any(
            fence_mod.glob_match(p, [review]) for p in paths
        ):
            return True
    return False


def is_checkpoint_record(
    record: dict[str, Any], cwd: Path, lanes: dict[str, list[str]] | None = None
) -> bool:
    """The candidate test, applied to one selected host-proc record."""
    if record.get("mode") != "run":
        return False
    return is_role1_commit(
        str(record.get("sha")), cwd, checkpoint_lanes() if lanes is None else lanes
    )


def tag_commit(cwd: Path) -> str | None:
    """The commit `wr-ckpt/slice-b` names, when it names a newest `WR-Merge: J-SLICE-B` carrier
    (CM-1: only a checkpoint id repeats; a tag on any other commit is not the checkpoint's)."""
    proc = fence_mod._git(cwd, "rev-parse", "--verify", "-q", f"refs/tags/{TAG}^{{commit}}")  # noqa: SLF001
    if proc.returncode != 0:
        return None
    commit = proc.stdout.strip()
    return commit if trailers.newest(TRIGGER_MERGE, commit, cwd) == commit else None


def _docker_of(proc: dict[str, Any], cwd: Path) -> dict[str, Any] | None:
    """The host-docker record `record.paired_docker` pairs with `proc`; a `preflight` record counts
    as none (it is `$PRE`'s record, not a role-2 run)."""
    docker = record_mod.paired_docker(proc, cwd)
    return None if docker is not None and docker.get("mode") == "preflight" else docker


def resolve(head: str = "HEAD", cwd: Path | None = None) -> Pair | None:
    """The checkpoint role-2 pair at `head` (see the module docstring), or `None`."""
    cwd = cwd or ROOT
    selected = record_mod.select("host-proc", head, cwd)
    if selected is not None and is_checkpoint_record(selected, cwd):
        return Pair(head, selected, _docker_of(selected, cwd))
    tagged = tag_commit(cwd)
    if tagged is None:
        return None
    proc = record_mod.select("host-proc", tagged, cwd)
    return None if proc is None else Pair(tagged, proc, _docker_of(proc, cwd))


def diff_clean_problems(docker: dict[str, Any] | None) -> list[str]:
    """WR-PROOF-6:diff-clean-checkpoint: `diff.unattributed == []` on the host-docker record."""
    unattributed = ((docker or {}).get("diff") or {}).get("unattributed")
    return [] if unattributed == [] else [f"host-docker diff.unattributed is {unattributed!r}"]


def engine_untouched_problems(docker: dict[str, Any] | None) -> list[str]:
    """WR-CON-3:engine-untouched-checkpoint: `diff.engine_state_changed == false`."""
    changed = ((docker or {}).get("diff") or {}).get("engine_state_changed")
    return [] if changed is False else [f"host-docker diff.engine_state_changed is {changed!r}"]
