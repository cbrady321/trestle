"""CM-1: merge ids, branches and the trailer. The one trailer reader —
`register`, `fence`, `meta ckpt` and `meta kdoc` import this and it never
imports `fence` (L.P0-0d.7).

`landing(id)` = the oldest `WR-Merge: id` carrier (product ids only,
never a `WR-Fix`). `newest(id)` = the newest carrier (checkpoint ids may
repeat: a re-run re-carries `WR-Merge: J-<NAME>`). `anomalies(range)`
lists a second product carrier or a commit with two trailers; it never
raises on history content — an anomaly is a `fence check --history`
failure, not a load error here.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TRAILER_RE = re.compile(r"^(WR-Merge|WR-Fix): (\S+)$", re.MULTILINE)
RECORD_SEP = "\x02"
FIELD_SEP = "\x01"


def _is_checkpoint_id(merge_id: str) -> bool:
    """Only checkpoint ids repeat (CM-1): `J0` or `J-<NAME>`."""
    return merge_id == "J0" or merge_id.startswith("J-")


@dataclass
class Commit:
    sha: str
    message: str

    def trailers(self) -> list[tuple[str, str]]:
        return TRAILER_RE.findall(self.message)


def _commits(ref: str, cwd: Path | None = None) -> list[Commit]:
    """First-parent commits reachable from `ref` (or in a `A..B` range),
    oldest first. Returns `[]` for an empty/unresolvable range rather than
    raising, so a fresh repo with no matching history reads as empty."""
    cwd = cwd or ROOT
    proc = subprocess.run(
        [
            "git",
            "log",
            "--first-parent",
            "--reverse",
            f"--pretty=format:%H{FIELD_SEP}%B{RECORD_SEP}",
            ref,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    commits = []
    for rec in proc.stdout.split(RECORD_SEP):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, _, message = rec.partition(FIELD_SEP)
        commits.append(Commit(sha=sha, message=message))
    return commits


def landing(merge_id: str, ref: str = "HEAD", cwd: Path | None = None) -> str | None:
    """The oldest `WR-Merge: <merge_id>` carrier reachable from `ref`.
    A `WR-Fix: <merge_id>` commit is never returned (CM-1: a WR-Fix is
    never a landing)."""
    for commit in _commits(ref, cwd):
        for kind, cid in commit.trailers():
            if kind == "WR-Merge" and cid == merge_id:
                return commit.sha
    return None


def newest(merge_id: str, ref: str = "HEAD", cwd: Path | None = None) -> str | None:
    """The newest `WR-Merge: <merge_id>` carrier reachable from `ref`
    (checkpoint ids only repeat; for a product id this equals `landing`)."""
    result = None
    for commit in _commits(ref, cwd):
        for kind, cid in commit.trailers():
            if kind == "WR-Merge" and cid == merge_id:
                result = commit.sha
    return result


def anomalies(rng: str, cwd: Path | None = None) -> list[str]:
    """A second `WR-Merge` carrier of a product id, or a commit carrying
    two trailers, within `rng` (a `git log` range or ref). Never raises on
    history content."""
    found: list[str] = []
    seen_product: dict[str, str] = {}
    for commit in _commits(rng, cwd):
        trailers = commit.trailers()
        if len(trailers) >= 2:
            found.append(f"{commit.sha}: carries {len(trailers)} trailers ({trailers})")
        for kind, cid in trailers:
            if kind != "WR-Merge" or _is_checkpoint_id(cid):
                continue
            if cid in seen_product:
                found.append(
                    f"{commit.sha}: second WR-Merge carrier of product id {cid!r} "
                    f"(first at {seen_product[cid]})"
                )
            else:
                seen_product[cid] = commit.sha
    return found
