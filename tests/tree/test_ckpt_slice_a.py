"""L.TR-6.7: planted self-tests of `tests/proof/ckpt/slice_a.py` (CM-5, MC-28; WR-PROOF-1 column A).

These are the only default-collected checkpoint tests of the tree band: each is true on every later
head. They import the condition module (which runs nothing: a condition is a pure function of a
`World`) and exercise its verdicts over synthetic worlds built from the real static data (the
matrix map, `labels.d`, `deferrals.toml`, the register) and synthetic results. The live evaluation
runs only under `python -m tests.proof.meta ckpt slice-a` (DM-80).

`green_world()` satisfies every condition; each planted test breaks exactly one thing in it and
requires the condition that owns that thing to fail (and, where the plan names it, only that
one)."""

from __future__ import annotations

import argparse
import functools
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.proof import ckpt as ckpt_mod
from tests.proof import deferrals as deferrals_mod
from tests.proof import ledger as ledger_mod
from tests.proof import meta as meta_mod
from tests.proof import transcribe as transcribe_mod
from tests.proof.ckpt import slice_a
from tests.proof.host import record as record_mod
from tests.tree.joins import lift_set_check

ROOT = Path(__file__).resolve().parents[2]
UNPROVEN = "UNPROVEN"
INTERPRETER = "3.12.8"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}

proves_column_a = pytest.mark.proves(
    "WR-PROOF-1", "WR-PROOF-1:column-A", "A", "tree", "LOGIC", "CI"
)


@functools.cache
def _static() -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    clauses = list(transcribe_mod.load_matrix_map())
    labels = list(meta_mod._load_all_labels())  # noqa: SLF001
    return clauses, labels, slice_a.both_clauses_in(ROOT / "tests")


def _proven_keys(clauses: list[dict[str, Any]], labels: list[dict[str, Any]]) -> list[str]:
    """Every key a green candidate proves: column A's clauses, the twelve tree clauses, every tree
    label that is a claim (a gated or both-variant label is decided by no result)."""
    keys = set(slice_a.column_a_keys(clauses)) | set(slice_a.TREE_CLAUSES)
    keys |= {
        str(lb["id"])
        for lb in labels
        if lb.get("step") == "tree" and lb.get("posture") not in ("gated_on", "both_variant")
    }
    return sorted(keys)


def _ci_record(key: str, nodeid: str, outcome: str = "passed") -> dict[str, Any]:
    return {
        "nodeid": nodeid,
        "outcome": outcome,
        "gate": "ci-test",
        "venue": "CI",
        "interpreter": INTERPRETER,
        "labels": [key],
    }


def green_world() -> slice_a.World:
    """A world in which every condition of the module holds."""
    clauses, labels, both = _static()
    by_id = {str(lb["id"]): lb for lb in labels}
    keys = _proven_keys(clauses, labels)
    records = [_ci_record(k, f"tests/x/test_{i}.py::test_k") for i, k in enumerate(keys)]
    records += [_ci_record("", node) for node in slice_a.CONTRACT_PARITY_NODES]
    host_results = []
    for i, key in enumerate(keys):
        label = by_id.get(key)
        venue = str(label["venue"]) if label else ("BOTH" if key in both else "CI")
        if venue in ("BOTH", "HOST"):
            tree = label is not None and label.get("step") == "tree"
            path = "tests/tree" if tree or key in slice_a.TREE_CLAUSES else "tests/core"
            host_results.append(
                {"nodeid": f"{path}/host_{i}.py::test_k", "outcome": "PASSED", "labels": [key]}
            )
    host_results.append(
        {
            "nodeid": f"{slice_a.CONTAINMENT_MODULE}::test_containment",
            "outcome": "PASSED",
            "labels": [],
        }
    )
    host_record = {
        "schema": 1,
        "gate": "host-proc",
        "sha": "0" * 40,
        "mode": "run",
        "python": INTERPRETER,
        "platform": "darwin",
        "results": host_results,
        "status": "PASSED",
    }
    return slice_a.World(
        report={k: {"status": slice_a.PROVEN} for k in [*keys, "review:RV-5"]},
        records=records,
        labels=labels,
        clauses=clauses,
        both_clauses=set(both),
        deferrals=deferrals_mod.load_deferrals(),
        host_record=host_record,
        register={mechanism: False for mechanism in slice_a.ABSENT_MECHANISMS},
        rollback=list(_toml("rollback.toml").get("boundary", [])),
        d2_exceptions=list(_toml("d2_exceptions.toml").get("exception", [])),
        open_question_ids=set(slice_a.OPEN_QUESTIONS),
        slice_a_states=["tr5-inflight-choice_long_running", "tr5-terminal-live_state"],
        lint={name: (0, "") for name in ("ruff check", "ruff format --check", "mypy")},
        review={
            "record": {"outcome": "pass", "sha": "0" * 40},
            "error": None,
            "ancestor": True,
            "shown": True,
        },
    )


def _toml(name: str) -> dict[str, Any]:
    import tomllib

    return tomllib.loads((ROOT / "tests" / "proof" / name).read_text(encoding="utf-8"))


def _verdicts(world: slice_a.World) -> dict[str, tuple[bool, str]]:
    return {sid: verdict(world) for sid, verdict in slice_a.VERDICTS.items()}


def _failing(world: slice_a.World) -> set[str]:
    return {sid for sid, (ok, _reason) in _verdicts(world).items() if not ok}


# ---- the module ---------------------------------------------------------------------------


def test_module_declares_s0_and_s2_to_s14() -> None:
    assert slice_a.TRIGGER_MERGE == "J-SLICE-A" and slice_a.TAG == "wr-ckpt/slice-a"
    wanted = [f"J-SLICE-A-S{n}" for n in (0, *range(2, 15))]
    assert [c.id for c in slice_a.CONDITIONS] == wanted
    assert all(isinstance(c, ckpt_mod.Condition) and not c.merge_only for c in slice_a.CONDITIONS)
    assert set(slice_a.TREE_CLAUSES) == {
        key
        for clause in _static()[0]
        if clause.get("step") == "tree"
        for key in slice_a.clause_parts(clause)
    }


def test_green_world_satisfies_every_condition() -> None:
    assert _verdicts(green_world()) == {sid: (True, "") for sid in slice_a.VERDICTS}


# ---- S2, S3: clauses ----------------------------------------------------------------------


@proves_column_a
def test_planted_red_tree_clause_blocks_slice_a() -> None:
    """A tree clause that is UNPROVEN in the ledger fails S2 (and S3, which holds every clause of
    column A), and no other condition."""
    for clause in slice_a.TREE_CLAUSES:
        world = green_world()
        world.report[clause] = {"status": UNPROVEN}
        assert _failing(world) == {"S2", "S3"}, clause
    ok, reason = slice_a.verdict_s2(_planted(green_world(), "A1.6", UNPROVEN))
    assert not ok and "A1.6 is not PROVEN" in reason


def _planted(world: slice_a.World, key: str, status: str) -> slice_a.World:
    world.report[key] = {"status": status}
    return world


@pytest.mark.parametrize("mechanism", slice_a.ABSENT_MECHANISMS)
@proves_column_a
def test_planted_present_mechanism_blocks_slice_a(mechanism: str) -> None:
    """A mechanism of the band that is still present fails S2 alone (CM-7); a register entry that
    does not exist cannot read `absent` and fails it too; a register violation fails it."""
    world = green_world()
    world.register[mechanism] = True
    assert _failing(world) == {"S2"}
    ok, reason = slice_a.verdict_s2(world)
    assert f"{mechanism} is still present" in reason
    gone = green_world()
    del gone.register[mechanism]
    assert _failing(gone) == {"S2"}
    violated = green_world()
    violated.register_violations = ["X is claimed while a present entry serves it"]
    assert _failing(violated) == {"S2"}


def _non_tree_part_clause() -> str:
    clauses, _labels, both = _static()
    for key in slice_a.column_a_keys(clauses):
        if key not in slice_a.TREE_CLAUSES and key not in both:
            return key
    raise AssertionError("no non-tree CI clause in column A")


def _both_clause_outside_the_tree() -> str:
    _clauses, _labels, both = _static()
    return sorted(k for k in both if k not in slice_a.TREE_CLAUSES)[0]


@pytest.mark.parametrize("case", ["non_tree_clause", "both_clause_without_admissible_record"])
@proves_column_a
def test_planted_unproven_column_a_clause_blocks_slice_a(case: str) -> None:
    """S3 is evaluated inside the module, over the ledger and the S0 record: an UNPROVEN clause of a
    cell that is no tree clause fails it, and so does a venue-BOTH clause whose only passing result
    is a CI run because no admissible host record exists."""
    world = green_world()
    if case == "non_tree_clause":
        key = _non_tree_part_clause()
        world.report[key] = {"status": UNPROVEN}
        assert _failing(world) == {"S3"}
        assert key in slice_a.verdict_s3(world)[1]
    else:
        key = _both_clause_outside_the_tree()
        assert key in world.both_clauses and _ledger_ci_pass(world, key)
        world.host_record = None  # no admissible record: the CI result alone proves nothing
        failing = _failing(world)
        assert "S3" in failing and "S0" in failing
        ok, reason = slice_a.verdict_s3(world)
        assert not ok and f"{key} (BOTH): no admissible host-proc record" in reason
        # and a record that exists but does not carry the clause's node as PASSED is no proof
        world = green_world()
        assert world.host_record is not None
        world.host_record["results"] = [
            r for r in world.host_record["results"] if key not in r["labels"]
        ]
        assert "S3" in _failing(world)


def _ledger_ci_pass(world: slice_a.World, key: str) -> bool:
    return slice_a._proven(world, key) and slice_a._named_gate_pass(world.records, key)  # noqa: SLF001


def test_column_a_is_the_forty_clauses_of_the_a_cells() -> None:
    clauses, _labels, _both = _static()
    keys = slice_a.column_a_keys(clauses)
    assert {k.split(":")[0].split(".")[0] for k in keys} == {f"A{n}" for n in range(1, 10)}
    assert len({k.split(":")[0] for k in keys}) == 40
    assert set(slice_a.TREE_CLAUSES) <= set(keys)


# ---- S0: the host record ------------------------------------------------------------------


@proves_column_a
def test_planted_failed_other_phase_node_blocks_slice_a(tmp_path: Path) -> None:
    """S0 has no cross-phase tolerance: a host-proc record selected by `record.select` over a
    synthetic repository, in which every tree node passed and one node another phase owns FAILED,
    blocks the checkpoint through CM-6's role-2 pass set."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    (repo / "code.py").write_text("x = 1\n")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "code")
    code_sha = _sh(repo, "rev-parse", "HEAD")
    world = green_world()
    assert world.host_record is not None
    record = dict(world.host_record, sha=code_sha, status="FAILED")
    record["results"] = [
        *world.host_record["results"],
        {
            "nodeid": "tests/core/spine/test_other.py::test_other_phase",
            "outcome": "FAILED",
            "labels": [],
        },
    ]
    directory = repo / "tests" / "proof" / "host" / "host-proc"
    directory.mkdir(parents=True)
    (directory / f"{code_sha}.json").write_text(json.dumps(record), encoding="utf-8")
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "records only")
    selected = record_mod.select("host-proc", "HEAD", cwd=repo)
    assert selected is not None and selected["sha"] == code_sha  # admissible: records-only diff
    world.host_record = selected
    ok, reason = slice_a.verdict_s0(world)
    assert not ok and "test_other_phase: FAILED" in reason
    assert not any(
        r["outcome"] != "PASSED" for r in selected["results"] if "tests/tree/" in r["nodeid"]
    )
    # the same record with the node passing is accepted: the failure is what blocks it
    fixed = dict(selected, results=[dict(r, outcome="PASSED") for r in selected["results"]])
    world.host_record = fixed
    assert slice_a.verdict_s0(world) == (True, "")
    # a skipped node is allowed only when its label is gated_on / both_variant / na
    skipped = green_world()
    assert skipped.host_record is not None
    skipped.host_record["results"].append(
        {"nodeid": "tests/tree/x.py::t", "outcome": "SKIPPED", "labels": ["A1.6"]}
    )
    assert not slice_a.verdict_s0(skipped)[0]
    # no record at all, and a node run that failed, each block it
    assert not slice_a.verdict_s0(slice_a.World())[0]
    node_failed = green_world()
    node_failed.s0_node = (1, "FAILED")
    assert _failing(node_failed) == {"S0"}


def _sh(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


# ---- S4: tree labels ----------------------------------------------------------------------


def _rendered(world: slice_a.World, tmp_path: Path) -> dict[str, dict[str, Any]]:
    """The real MC-02 ledger over `world.records` (a results directory, one declared gate)."""
    results = tmp_path / "results"
    results.mkdir()
    (results / "ci-test.jsonl").write_text(
        "\n".join(json.dumps(r) for r in world.records) + "\n", encoding="utf-8"
    )
    config = tmp_path / "meta_config.toml"
    config.write_text('[gates]\nnames = ["ci-test"]\n', encoding="utf-8")
    return ledger_mod.render(
        results_dir=results,
        meta_config_path=config,
        gates_dir=tmp_path / "gates.d",
        reviews_dir=tmp_path / "reviews",
    )


def _ci_tree_label() -> str:
    _clauses, labels, _both = _static()
    for lb in labels:
        if lb.get("step") == "tree" and lb.get("venue") == "CI" and lb.get("posture") == "claim":
            return str(lb["id"])
    raise AssertionError("no CI claim label of the tree")


@pytest.mark.parametrize("outcome", ["xfailed", "skipped", "unrun"])
@proves_column_a
def test_planted_xfailed_tree_label_blocks_slice_a(outcome: str, tmp_path: Path) -> None:
    """S4 counts a skipped, xfailed or unrun label test UNPROVEN, over the real ledger renderer:
    the label's only test is xfailed (or skipped, or has no result at all)."""
    world = green_world()
    label = _ci_tree_label()
    others = [r for r in world.records if label not in r["labels"]]
    planted = (
        [] if outcome == "unrun" else [_ci_record(label, "tests/x/test_planted.py::t", outcome)]
    )
    world.records = [*others, *planted]
    world.report = _rendered(world, tmp_path)
    assert world.report.get(label, {}).get("status") != slice_a.PROVEN
    ok, reason = slice_a.verdict_s4(world)
    assert not ok and label in reason
    # the same ledger with the test passing proves it: the outcome is what blocks the label
    world.records = [*others, _ci_record(label, "tests/x/test_planted.py::t")]
    (tmp_path / "again").mkdir()
    world.report = _rendered(world, tmp_path / "again")
    ok, reason = slice_a.verdict_s4(world)
    assert label not in reason


def test_gated_and_both_variant_labels_need_no_result() -> None:
    world = green_world()
    for label in world.labels:
        if label.get("posture") in ("gated_on", "both_variant"):
            assert slice_a.label_problems(world, label) == []
    gated = [lb for lb in world.labels if lb.get("posture") in ("gated_on", "both_variant")]
    assert gated, "the tree band declares OQ-27 / OQ-31 labels"


def test_host_label_needs_the_host_record_and_both_needs_both_halves() -> None:
    world = green_world()
    host = "WR-TERM-7:capacity-default-measured@host"
    assert slice_a.label_problems(world, {"id": host, "venue": "HOST"}) == []
    assert world.host_record is not None
    world.host_record["results"] = [
        r for r in world.host_record["results"] if host not in r["labels"]
    ]
    assert slice_a.label_problems(world, {"id": host, "venue": "HOST"})
    both = "WR-UNIT-2:started-paths-are-vertices"
    fresh = green_world()
    fresh.report[both] = {"status": UNPROVEN}
    assert slice_a.label_problems(fresh, {"id": both, "venue": "BOTH"})


# ---- S13: deferrals -----------------------------------------------------------------------


@proves_column_a
def test_planted_unproven_deferred_label_blocks_slice_a() -> None:
    """A deferral whose `closes_at` lies in TR-0..TR-6 with its label not PROVEN fails S13 alone;
    the deferral entry itself stays (CM-8): the check reads the entry, never edits it."""
    world = green_world()
    closing = [d for d in world.deferrals if d["closes_at"] in slice_a.TR_MERGES]
    assert [d["label"] for d in closing] == ["WR-TERM-5:tree-size"]
    world.report["WR-TERM-5:tree-size"] = {"status": UNPROVEN}
    ok, reason = slice_a.verdict_s13(world)
    assert not ok and "WR-TERM-5:tree-size" in reason
    assert "S13" in _failing(world)
    assert [d["label"] for d in world.deferrals if d["closes_at"] in slice_a.TR_MERGES] == [
        "WR-TERM-5:tree-size"
    ]
    # a deferral closing after the band is not this checkpoint's business
    later = green_world()
    later.deferrals = [
        *later.deferrals,
        {
            "label": "X:later",
            "from_step": "core",
            "closes_at": "CZ",
            "citation": "planted",
            "declared_by": "planted",
        },
    ]
    assert slice_a.verdict_s13(later) == (True, "")


# ---- S5, S6, S7, S8, S9, S10, S11, S12, S14 ------------------------------------------------


def test_s5_sets_the_lift_ledger_itself_and_the_check_fails_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5 hands `lift_set_check` the ledger it rendered, through `TRESTLE_LIFT_LEDGER` set in the
    command's own environment; the check still fails when the variable is unset elsewhere."""
    assert slice_a.LEDGER_ENV == lift_set_check.LEDGER_ENV
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
        seen["argv"], seen["env"] = argv, env
        seen["ledger"] = json.loads(Path((env or {})[slice_a.LEDGER_ENV]).read_text("utf-8"))
        return 0, ""

    monkeypatch.setattr(slice_a, "_run", fake_run)
    world = green_world()
    live = slice_a.LiveWorld("HEAD")
    live.__dict__.update(records=world.records, host_record=world.host_record)
    assert live.s5_lift == (0, "")
    assert seen["argv"][-1] == "tests/tree/joins/lift_set_check.py::test_lift_set_closed"
    assert seen["ledger"] == slice_a.lift_ledger_records(world.records, world.host_record)
    assert any(r["venue"] == "HOST" and r["outcome"] == "passed" for r in seen["ledger"])
    monkeypatch.delenv(lift_set_check.LEDGER_ENV, raising=False)
    with pytest.raises(AssertionError, match="is unset"):
        lift_set_check.test_lift_set_closed()


def test_command_conditions_fail_on_a_nonzero_exit() -> None:
    for name, sid in (
        ("s5_trl", "S5"),
        ("s5_lift", "S5"),
        ("d4", "S6"),
        ("d5", "S6"),
        ("d6", "S6"),
        ("d7", "S6"),
        ("spine", "S7"),
        ("d2_single", "S8"),
        ("open_questions", "S9"),
        ("d2_head", "S10"),
    ):
        world = green_world()
        setattr(world, name, (1, "planted failure"))
        assert sid in _failing(world), name
    lint = green_world()
    lint.lint["mypy"] = (1, "1 error")
    assert _failing(lint) == {"S11"}
    unrun = green_world()
    del unrun.lint["ruff check"]
    assert _failing(unrun) == {"S11"}


def test_s8_drain_exceptions_exactly_where_the_boundary_is_drain() -> None:
    world = green_world()
    assert slice_a.drain_problems(world.rollback, world.d2_exceptions) == []
    drain = [dict(b, **{"class": "drain"}) if b["merge"] == "TR-5" else b for b in world.rollback]
    assert slice_a.drain_problems(drain, world.d2_exceptions)  # drain, no E-TR5-DRAIN
    declared = [*world.d2_exceptions, {"id": "E-TRL-DRAIN"}]
    assert slice_a.drain_problems(world.rollback, declared)  # E-TRL-DRAIN, TR-L transparent
    assert slice_a.drain_problems(drain, [*world.d2_exceptions, {"id": "E-TR5-DRAIN"}]) == []


def test_s9_open_questions_and_postures() -> None:
    world = green_world()
    world.open_question_ids = slice_a.OPEN_QUESTIONS | {"OQ-32"}  # answered, so not listed
    assert not slice_a.verdict_s9(world)[0]
    world.open_question_ids = (slice_a.OPEN_QUESTIONS - {"OQ-25"}) | {"OQ-26"}  # one went missing
    assert not slice_a.verdict_s9(world)[0]
    world.open_question_ids = slice_a.OPEN_QUESTIONS | {"OQ-26", "F-11(a)"}  # others may be listed
    assert slice_a.verdict_s9(world)[0]
    world = green_world()
    bad = [dict(lb, posture="claim") if lb.get("oq") == "OQ-27" else lb for lb in world.labels]
    world.labels = bad
    assert not slice_a.verdict_s9(world)[0]


def test_s10_wants_an_inflight_and_a_terminal_multi_vertex_root() -> None:
    for kept in (["tr5-terminal-live_state"], ["tr5-inflight-choice_long_running"], []):
        world = green_world()
        world.slice_a_states = kept
        assert "S10" in _failing(world), kept
    world = green_world()
    world.slice_a_states = ["single-inflight-x", "single-terminal-y"]  # one-vertex, not tree roots
    assert "S10" in _failing(world)


def test_s12_rv5_record() -> None:
    world = green_world()
    world.review = None
    assert _failing(world) == {"S12"}
    for change in ({"ancestor": False}, {"shown": False}):
        world = green_world()
        assert world.review is not None
        world.review.update(change)
        assert _failing(world) == {"S12"}
    world = green_world()
    assert world.review is not None
    world.review["record"] = {"outcome": "fail", "sha": "0" * 40}
    assert _failing(world) == {"S12"}


def test_s14_join_closures() -> None:
    world = green_world()
    world.records = [r for r in world.records if r["nodeid"] != slice_a.CONTRACT_PARITY_NODES[0]]
    assert _failing(world) == {"S14"}
    world = green_world()
    assert world.host_record is not None
    world.host_record["results"] = [
        r
        for r in world.host_record["results"]
        if not r["nodeid"].startswith(slice_a.CONTAINMENT_MODULE)
    ]
    assert _failing(world) == {"S14"}
    world = green_world()
    world.report.pop(slice_a.ANSWER_BOUNDED_LABEL)
    assert "S14" in _failing(world)


# ---- the framework: evaluated only on the trigger, never collected by default --------------


def test_module_not_evaluated_off_trigger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The TR-6 merge commit is not a `WR-Merge: J-SLICE-A` carrier: the module is not evaluated
    there (no world is built), while a carrier evaluates it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _sh(repo, "init", "-q", "-b", "master")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: TR-6")
    tr6 = _sh(repo, "rev-parse", "HEAD")
    worlds: list[str] = []

    def spy(commit: str) -> slice_a.World:
        worlds.append(commit)
        return green_world()

    monkeypatch.setattr(slice_a, "make_world", spy)
    monkeypatch.setattr(meta_mod, "ROOT", repo)
    off = argparse.Namespace(name="slice-a", dry=False, preview=False, commit=tr6)
    assert meta_mod.cmd_ckpt(off) == 0
    assert worlds == [], "the TR-6 merge commit must not evaluate the module"
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J-SLICE-A")
    carrier = _sh(repo, "rev-parse", "HEAD")
    on = argparse.Namespace(name="slice-a", dry=False, preview=False, commit=carrier)
    assert meta_mod.cmd_ckpt(on) == 0
    assert set(worlds) == {carrier}
    # the module reads the world only through the seam: a broken world is a failing condition
    monkeypatch.setattr(slice_a, "make_world", lambda commit: (_ for _ in ()).throw(OSError("x")))
    assert not slice_a.CONDITIONS[0].check(carrier)[0]


def test_default_collection_finds_neither_module_nor_helper() -> None:
    """`slice_a.py` and `lift_set_check.py` match no default pytest pattern (DM-80): a default
    collection of their directories names neither."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/proof/ckpt",
            "tests/tree/joins",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=dict(
            os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
        ),
    )
    assert proc.returncode == 0, proc.stdout[-400:] + proc.stderr[-400:]
    assert "slice_a.py" not in proc.stdout and "lift_set_check.py" not in proc.stdout
    assert "test_j_trl.py" in proc.stdout  # the collection did look at the joins directory
