"""Selftest for `meta enforce --scope ci|checkpoint` (L.CZ.1; CSC-5, CM-6, WR-PROOF-1).

Planted worlds and planted git histories only: every HOST record here is a JSON file written into
a scratch repository, and no test reads a CI artifact or runs a live check. The live command
`python -m tests.proof.meta enforce --scope ci` is accepted at the CZ PR head once J-SLICE-B has
landed (its anchor is that checkpoint's carrier).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.proof import meta as meta_mod

ROOT = Path(__file__).resolve().parents[3]
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}
PROVEN = {"status": "PROVEN", "corroborating_314": False, "n_results": 1}
UNPROVEN = {"status": "UNPROVEN", "corroborating_314": False, "n_results": 1}

CI_LABEL = {
    "id": "WR-X-1:planted-ci",
    "row": "WR-X-1",
    "tier": "LOGIC",
    "venue": "CI",
    "posture": "claim",
}
DOCKER_LABEL = {
    "id": "WR-X-2:planted-docker@host",
    "row": "WR-X-2",
    "tier": "DOCKER",
    "venue": "HOST",
    "posture": "claim",
}
PROC_LABEL = {
    "id": "WR-X-3:planted-proc",
    "row": "WR-X-3",
    "tier": "PROC",
    "venue": "HOST",
    "posture": "claim",
}
LABELS = [CI_LABEL, DOCKER_LABEL, PROC_LABEL]
REPORT = {CI_LABEL["id"]: PROVEN}  # the host-only keys have no CI result at all


def _sh(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _sh(repo, "init", "-q", "-b", "master")
    (repo / "code.txt").write_text("v1")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "product")
    return repo


def _head(repo: Path) -> str:
    return _sh(repo, "rev-parse", "HEAD")


def _record(gate: str, sha: str, outcome: str = "PASSED") -> dict:
    labels = {"host-proc": [PROC_LABEL["id"]], "host-docker": [DOCKER_LABEL["id"]]}[gate]
    return {
        "schema": 1,
        "gate": gate,
        "sha": sha,
        "mode": "run",
        "python": "3.12.9",
        "platform": "darwin",
        "results": [{"nodeid": f"t.py::{gate}", "outcome": outcome, "labels": labels}],
        "status": "PASSED" if outcome == "PASSED" else "FAILED",
    }


def _write_records(repo: Path, sha: str, outcome: str = "PASSED") -> None:
    for gate in ("host-proc", "host-docker"):
        directory = repo / "tests" / "proof" / "host" / gate
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{sha}.json").write_text(json.dumps(_record(gate, sha, outcome)))
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "records")


def _carrier(repo: Path, merge_id: str = "J-SLICE-B", tag: str | None = "wr-ckpt/slice-b") -> str:
    _sh(repo, "commit", "-q", "--allow-empty", "-m", f"checkpoint\n\nWR-Merge: {merge_id}")
    sha = _head(repo)
    if tag:
        _sh(repo, "tag", "-f", tag, sha)
    return sha


def _product_change(repo: Path, text: str) -> None:
    (repo / "code.txt").write_text(text)
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", f"product {text}")


def _never(_cwd: Path, _sha: str, _name: str) -> str:  # a check-run read must not happen here
    raise AssertionError("no check run is read for a tag-marked checkpoint")


def _world(scope: str, repo: Path, commit: str = "HEAD", **kw) -> meta_mod.EnforceWorld:
    evidence = meta_mod.host_evidence(scope, cwd=repo, commit=commit, check_run_reader=_never)
    return meta_mod.EnforceWorld(
        scope=scope, report=dict(REPORT), labels=list(LABELS), clauses=[], **evidence, **kw
    )


def _proves():
    """The marker every node of this file carries: L.CZ.1 declares this one label."""
    return pytest.mark.proves(
        "WR-PROOF-1", "WR-PROOF-1:every-cell-green-or-na", "core", "CZ", "LOGIC", "CI"
    )


@_proves()
def test_planted_unproven_ci_clause_fails_scope_ci(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    world = meta_mod.EnforceWorld(
        scope="ci",
        report={CI_LABEL["id"]: UNPROVEN},
        labels=[CI_LABEL],
        clauses=[{"id": "A9.9", "parts": []}],
        markers={"A9.9": [("LOGIC", "CI")]},
    )
    problems = meta_mod.enforce_problems(world)
    assert any(CI_LABEL["id"] in p for p in problems)
    assert any("A9.9" in p and "not PROVEN" in p for p in problems)

    # the command exits 1 on the same world and 0 once every key is PROVEN
    monkeypatch.setattr(meta_mod, "live_world", lambda scope, commit="HEAD": world)
    assert meta_mod.main(["enforce", "--scope", "ci"]) == 1
    assert "not PROVEN" in capsys.readouterr().out
    world.report = {CI_LABEL["id"]: PROVEN, "A9.9": PROVEN}
    assert meta_mod.main(["enforce", "--scope", "ci"]) == 0

    # a declared posture needs no result; an undeclared gated_on or na label does not pass
    declared = [
        {"id": "WR-G-1:gated", "posture": "gated_on", "oq": "OQ-25"},
        {"id": "WR-G-2:variant", "posture": "both_variant"},
        {"id": "WR-G-3:na", "posture": "na", "reason": "not applicable here"},
    ]
    assert meta_mod.enforce_problems(meta_mod.EnforceWorld(scope="ci", labels=declared)) == []
    bare = [{"id": "WR-G-1:gated", "posture": "gated_on"}, {"id": "WR-G-3:na", "posture": "na"}]
    assert len(meta_mod.enforce_problems(meta_mod.EnforceWorld(scope="ci", labels=bare))) == 2


@_proves()
def test_shape_docker_host_and_docker_evidenced_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """L.CZ.1.fix2, from the first live run on master + J-SLICE-B: a `shape` label needs no result;
    a HOST key registered only by a docker_host node reads the host-docker record; a
    DOCKER_EVIDENCED key reads the host-docker record, never the CI ledger."""
    shape = {"id": "WR-S-1:shape", "tier": "PROC", "venue": "CI", "posture": "shape"}
    assert meta_mod.enforce_problems(meta_mod.EnforceWorld(scope="ci", labels=[shape])) == []

    def rec(gate: str, labels: list[str], outcome: str = "PASSED") -> dict:
        return {"sha": "a" * 40, "results": [{"nodeid": "t", "outcome": outcome, "labels": labels}]}

    # B4.4's shape: marker PROC at HOST, node docker_host, so only the docker record names it
    world = meta_mod.EnforceWorld(
        scope="ci",
        clauses=[{"id": "B9.8", "parts": []}],
        markers={"B9.8": [("PROC", "HOST")]},
        host_proc=rec("host-proc", []),
        host_docker=rec("host-docker", ["B9.8"]),
    )
    assert meta_mod.enforce_problems(world) == []
    world.host_docker = rec("host-docker", ["B9.8"], "FAILED")
    assert any("B9.8 is not PASSED" in p for p in meta_mod.enforce_problems(world))
    world.host_docker = rec("host-docker", [])
    assert any("B9.8 is not PASSED" in p for p in meta_mod.enforce_problems(world))

    from tests.proof.ckpt import slice_b as slice_b_mod

    key = "WR-Z-9:docker-live"
    monkeypatch.setattr(slice_b_mod, "DOCKER_EVIDENCED", {key: "t"})
    label = {"id": key, "tier": "must", "venue": "CI", "posture": "claim"}
    world = meta_mod.EnforceWorld(
        scope="ci",
        report={key: UNPROVEN},
        labels=[label],
        host_proc=rec("host-proc", []),
        host_docker=rec("host-docker", [key]),
    )
    assert meta_mod.enforce_problems(world) == []  # the CI skip is no evidence either way
    world.host_docker = rec("host-docker", [])
    assert meta_mod.enforce_problems(world) == [
        f"{key} is not PASSED in the host-docker record aaaaaaaaaaaa"
    ]


@_proves()
def test_scope_ci_rerenders_host_clauses_from_committed_records_at_last_checkpoint_merge(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    role2 = _head(repo)
    _write_records(repo, role2)
    carrier = _carrier(repo)
    # a product change after the checkpoint: the records are inadmissible for HEAD, but
    # `--scope ci` selects at the checkpoint's carrier (PC5-4), so they still render the HOST keys
    _product_change(repo, "v2")

    world = _world("ci", repo)
    assert world.anchor == carrier
    assert world.host_proc is not None and world.host_proc["sha"] == role2
    assert world.host_docker is not None and world.host_docker["sha"] == role2
    assert meta_mod.enforce_problems(world) == []

    # the same keys through a record that failed render as failed, whatever the CI ledger says
    failing = _repo(tmp_path / "failing")
    bad = _head(failing)
    _write_records(failing, bad, outcome="FAILED")
    _carrier(failing)
    problems = meta_mod.enforce_problems(_world("ci", failing))
    assert any(PROC_LABEL["id"] in p and "not PASSED" in p for p in problems)
    assert any(DOCKER_LABEL["id"] in p and "not PASSED" in p for p in problems)


@_proves()
def test_scope_ci_needs_no_ci_artifact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    role2 = _head(repo)
    _write_records(repo, role2)
    _carrier(repo)
    # a results directory that says the HOST keys failed, as a CI artifact of some run might
    results = repo / "tests" / "proof" / "results"
    results.mkdir(parents=True)
    (results / "host-proc.jsonl").write_text(
        json.dumps({"nodeid": "t.py::x", "outcome": "failed", "labels": [PROC_LABEL["id"]]})
    )
    world = _world("ci", repo)  # the ledger is the run's own; the HOST keys have no result in it
    assert PROC_LABEL["id"] not in world.report and DOCKER_LABEL["id"] not in world.report
    assert meta_mod.enforce_problems(world) == []
    # with no checkpoint carrier there is no anchor, and no HOST key renders (never a fallback)
    empty = _repo(tmp_path / "empty")
    orphan = _world("ci", empty)
    assert orphan.anchor is None
    assert any("no admissible host-proc" in p for p in meta_mod.enforce_problems(orphan))


@_proves()
def test_scope_checkpoint_requires_admissible_host_records(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    role2 = _head(repo)
    _write_records(repo, role2)
    fresh = _world("checkpoint", repo)
    assert meta_mod.enforce_problems(fresh) == []

    # a non-record path changes after the records' sha: none is admissible for the candidate
    _product_change(repo, "v2")
    stale = _world("checkpoint", repo)
    assert stale.host_proc is None
    problems = meta_mod.enforce_problems(stale)
    assert any("no admissible host-proc record at the candidate" in p for p in problems)
    assert any("no admissible host-docker record at the candidate" in p for p in problems)

    # a record the admissibility reader refuses fails the scope, as does a pass-set violation
    refused = _world("checkpoint", repo, commit="HEAD~1")
    assert meta_mod.enforce_problems(refused) == []
    refused.admissible = lambda record: (False, "planted")  # noqa: ARG005
    assert any("not admissible: planted" in p for p in meta_mod.enforce_problems(refused))
    refused.admissible = lambda record: (True, None)  # noqa: ARG005
    refused.pass_set = lambda record: ["t.py::x: FAILED"]  # noqa: ARG005
    assert any("pass set: t.py::x: FAILED" in p for p in meta_mod.enforce_problems(refused))


@_proves()
def test_scope_ci_selects_newest_of_two_admissible_attempts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _head(repo)
    _write_records(repo, first, outcome="FAILED")  # a role-2 attempt that failed
    second = _head(repo)  # a records-only commit: the re-run's sha
    _write_records(repo, second)
    _carrier(repo)

    world = _world("ci", repo)
    assert world.host_proc is not None and world.host_proc["sha"] == second
    assert world.host_docker is not None and world.host_docker["sha"] == second
    assert meta_mod.enforce_problems(world) == []


@_proves()
def test_scope_ci_anchor_is_last_successful_checkpoint_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert meta_mod.scope_ci_anchor("HEAD", repo, _never) is None
    role2 = _head(repo)
    _write_records(repo, role2)
    good = _carrier(repo)
    assert meta_mod.scope_ci_anchor("HEAD", repo, _never) == good

    # a newer trigger carrier without its success mark is skipped: the anchor stays at `good`
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "retry\n\nWR-Merge: J-SLICE-B")
    failed = _head(repo)
    assert failed != good
    assert meta_mod.scope_ci_anchor("HEAD", repo, _never) == good

    # a check-run checkpoint (J-ROOT) counts through its check run, newest first
    root_carrier = _carrier(repo, "J-ROOT", tag=None)
    seen: list[tuple[str, str]] = []

    def reader(_cwd: Path, sha: str, name: str) -> str:
        seen.append((sha, name))
        return "failure"

    assert meta_mod.scope_ci_anchor("HEAD", repo, reader) == good
    assert seen == [(root_carrier, "ckpt")]
    assert meta_mod.scope_ci_anchor("HEAD", repo, lambda *_a: "success") == root_carrier


def test_print_mode_prints_enforce_and_scope_is_required(capsys) -> None:
    assert meta_mod.main(["enforce", "--print-mode"]) == 0
    assert capsys.readouterr().out.strip() == "enforce"
    assert meta_mod.main(["enforce"]) == 2
    with pytest.raises(SystemExit):
        meta_mod.main(["enforce", "--scope", "bogus"])
    # TM-P0-6's probe reads `report`; it is absent once enforce prints `enforce`
    proc = subprocess.run(
        [sys.executable, "-m", "tests.proof.meta", "register", "--probe", "TM-P0-6"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == "absent", proc.stdout + proc.stderr


def test_clause_keys_and_proves_marker_scan(tmp_path: Path) -> None:
    clauses = [{"id": "A1.1", "parts": [{"name": "core"}, {"name": "single"}]}, {"id": "B9.2"}]
    assert meta_mod.clause_keys(clauses) == ["A1.1:core", "A1.1:single", "B9.2"]
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_planted.py").write_text(
        "import pytest\n"
        '@pytest.mark.proves("WR-X-1", "B9.2", "B", "B", "PROC", "BOTH")\n'
        "def test_a(): pass\n"
        '@pytest.mark.proves("WR-X-1", "WR-X-1:label", "B", "B", "PROC", "BOTH")\n'
        "def test_b(): pass\n"
    )
    assert meta_mod.scan_proves_markers([tests_dir]) == {"B9.2": [("PROC", "BOTH")]}
