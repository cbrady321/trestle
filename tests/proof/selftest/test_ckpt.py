"""Selftest for the checkpoint meta-check framework (CM-5, MC-28;
L.P0-0d.4)."""

from __future__ import annotations

import argparse
import os
import subprocess
import types
from pathlib import Path

import pytest

from tests.proof import ckpt as ckpt_mod
from tests.proof import deferrals as deferrals_mod
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
    r.mkdir()
    _sh(r, "init", "-q", "-b", "master")
    _sh(r, "commit", "-q", "--allow-empty", "-m", "root")
    return r


def _fake_module(trigger="J-X", tag=None, conditions=None):
    mod = types.ModuleType("fake_ckpt_module")
    mod.TRIGGER_MERGE = trigger
    mod.TAG = tag
    mod.CONDITIONS = conditions or []
    return mod


def test_planted_failing_condition_blocks_tag(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J-X")
    sha = _sh(repo, "rev-parse", "HEAD")
    mod = _fake_module(conditions=[ckpt_mod.Condition("c1", lambda _c: (False, "planted failure"))])
    monkeypatch.setattr(ckpt_mod, "load_module", lambda name: mod)
    monkeypatch.setattr(meta_mod, "ROOT", repo)
    args = argparse.Namespace(name="x", dry=False, preview=False, commit=sha)
    rc = meta_mod.cmd_ckpt(args)
    assert rc == 1


def test_non_trigger_commit_not_evaluated(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "not a trigger")
    sha = _sh(repo, "rev-parse", "HEAD")
    calls = []
    mod = _fake_module(
        conditions=[ckpt_mod.Condition("c1", lambda _c: calls.append(1) or (True, ""))]
    )
    monkeypatch.setattr(ckpt_mod, "load_module", lambda name: mod)
    monkeypatch.setattr(meta_mod, "ROOT", repo)
    args = argparse.Namespace(name="x", dry=False, preview=False, commit=sha)
    rc = meta_mod.cmd_ckpt(args)
    assert rc == 0
    assert calls == []


def test_newest_trigger_commit_evaluated_older_ignored(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J-X")
    old_sha = _sh(repo, "rev-parse", "HEAD")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "retry")
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J-X")
    new_sha = _sh(repo, "rev-parse", "HEAD")
    mod = _fake_module(conditions=[ckpt_mod.Condition("c1", lambda _c: (True, ""))])
    monkeypatch.setattr(ckpt_mod, "load_module", lambda name: mod)
    monkeypatch.setattr(meta_mod, "ROOT", repo)
    rc_old = meta_mod.cmd_ckpt(
        argparse.Namespace(name="x", dry=False, preview=False, commit=old_sha)
    )
    rc_new = meta_mod.cmd_ckpt(
        argparse.Namespace(name="x", dry=False, preview=False, commit=new_sha)
    )
    assert rc_old == 0  # older carrier: no-op
    assert rc_new == 0  # newest carrier: evaluated and passed


def test_preview_skips_merge_only_conditions():
    calls = []
    mod = _fake_module(
        conditions=[
            ckpt_mod.Condition("merge-only", lambda _c: calls.append("m") or (True, ""), True),
            ckpt_mod.Condition("plain", lambda _c: calls.append("p") or (True, "")),
        ]
    )
    ckpt_mod.evaluate(mod, "deadbeef", preview=True)
    assert calls == ["p"]


def test_unknown_ckpt_name_exit_2():
    with pytest.raises(ckpt_mod.UnknownCkptError):
        ckpt_mod.load_module("does-not-exist")


def test_digest_stable():
    report = {"b": 1, "a": 2}
    d1 = ckpt_mod.ledger_digest(report)
    d2 = ckpt_mod.ledger_digest({"a": 2, "b": 1})
    assert d1 == d2


def test_deferrals_schema_exact(tmp_path):
    good = tmp_path / "good.toml"
    good.write_text(
        '[[deferral]]\nlabel = "WR-X-1:foo"\nfrom_step = "core"\ncloses_at = "SL-8"\n'
        'citation = "c"\ndeclared_by = "L.X.1"\n'
    )
    assert len(deferrals_mod.load_deferrals(good)) == 1

    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[[deferral]]\nlabel = "WR-X-1:foo"\nfrom_step = "core"\ncloses_at = "SL-8"\n'
        'citation = "c"\ndeclared_by = "L.X.1"\nregister_id = "TM-C5-1"\n'
    )
    with pytest.raises(deferrals_mod.DeferralLoadError):
        deferrals_mod.load_deferrals(bad)


def test_band_rule_every_deferral_closing_in_band_proven():
    deferrals = [
        {"label": "WR-X-1:foo", "closes_at": "SL-8"},
        {"label": "WR-X-2:bar", "closes_at": "SL-9"},
    ]
    violations = deferrals_mod.band_rule_violations(
        deferrals, band_merges={"SL-8", "SL-9"}, proven_labels={"WR-X-1:foo"}
    )
    assert violations == ["WR-X-2:bar: closes_at='SL-9' but not PROVEN"]
    assert (
        deferrals_mod.band_rule_violations(
            deferrals, band_merges={"SL-8", "SL-9"}, proven_labels={"WR-X-1:foo", "WR-X-2:bar"}
        )
        == []
    )


def test_ckpt_idempotent_on_evaluated_commit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _sh(repo, "commit", "-q", "--allow-empty", "-m", "WR-Merge: J-X")
    sha = _sh(repo, "rev-parse", "HEAD")
    _sh(repo, "tag", "-a", "wr-ckpt/x", "-m", "tag", sha)
    calls = []
    mod = _fake_module(
        tag="wr-ckpt/x",
        conditions=[ckpt_mod.Condition("c1", lambda _c: calls.append(1) or (True, ""))],
    )
    monkeypatch.setattr(ckpt_mod, "load_module", lambda name: mod)
    monkeypatch.setattr(meta_mod, "ROOT", repo)
    rc = meta_mod.cmd_ckpt(argparse.Namespace(name="x", dry=False, preview=False, commit=sha))
    assert rc == 0
    assert calls == []  # conditions never re-evaluated; already tagged


def test_ckpt_job_not_on_pull_request():
    """CM-4/CM-5: the `ckpt` job runs on push and workflow_dispatch only, so
    it is outside the required-job list; its checkout sees full history."""
    yaml = pytest.importorskip("yaml")
    from tests.proof import fence as fence_mod

    ci_path = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
    workflow = yaml.safe_load(ci_path.read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert "workflow_dispatch" in triggers
    job = workflow["jobs"]["ckpt"]
    assert "pull_request" not in job["if"]
    assert "push" in job["if"] and "workflow_dispatch" in job["if"]
    assert "ckpt" not in fence_mod.required_jobs_from_ci(ci_path)
    others = set(workflow["jobs"]) - {"ckpt"}
    assert set(job["needs"]) == others
    assert job["permissions"] == {"contents": "write", "checks": "read"}


def test_preview_evaluates_a_non_carrier_pr_head(tmp_path, monkeypatch):
    """CM-5: `--preview` runs on the PR head, which never carries the
    trigger trailer; a real run there is a no-op."""
    repo = _repo(tmp_path)
    sha = _sh(repo, "rev-parse", "HEAD")
    calls = []
    mod = _fake_module(
        conditions=[ckpt_mod.Condition("c1", lambda _c: calls.append(1) or (False, "why"))],
    )
    monkeypatch.setattr(ckpt_mod, "load_module", lambda name: mod)
    monkeypatch.setattr(meta_mod, "ROOT", repo)
    real = argparse.Namespace(name="x", dry=False, preview=False, commit=sha)
    assert meta_mod.cmd_ckpt(real) == 0 and calls == []
    preview = argparse.Namespace(name="x", dry=False, preview=True, commit=sha)
    assert meta_mod.cmd_ckpt(preview) == 1 and calls == [1]


# --- L.CZ.9: tests/proof/ckpt/root.py --------------------------------------------------------

ROOT_STEPS = ("1", "2", "3", "4", "5", "6", "7")


def _root_mod():
    from tests.proof.ckpt import root as root_mod

    return root_mod


def _clean_root_world(root_mod, *, hosts=True, reviews=True):
    """A synthetic world in which every step holds: every command exits 0, the role-2 pair is
    admissible and pass-set clean, and the five root reviews are recorded."""
    docker = {
        "sha": "a" * 40,
        "gate": "host-docker",
        "status": "PASSED",
        "results": [{"nodeid": root_mod.HEALTHY_MACHINE_NODE, "outcome": "PASSED", "labels": []}],
        "diff": {"unattributed": [], "engine_state_changed": False},
    }
    proc = {"sha": "a" * 40, "gate": "host-proc", "status": "PASSED", "results": []}
    review = {"outcome": "pass", "sha": "b" * 40}
    return root_mod.World(
        is_carrier=True,
        in_ci=True,
        spine_junit=True,
        report={f"review:{rid}": {"status": "PROVEN"} for rid in root_mod.REVIEWS},
        host_proc=proc if hosts else None,
        host_docker=docker if hosts else None,
        root_reviews={rid: dict(review) for rid in root_mod.REVIEWS} if reviews else {},
    )


def _verdicts(root_mod, world):
    return {cid: root_mod.VERDICTS[cid](world) for cid in root_mod.CONDITION_IDS}


def test_root_module_conditions_registered():
    root_mod = _root_mod()
    assert root_mod.TRIGGER_MERGE == "J-ROOT" and root_mod.TAG is None
    ids = [c.id for c in root_mod.CONDITIONS]
    assert ids == root_mod.CONDITION_IDS
    # all seven steps of `## Root integration (J-ROOT)` are registered
    for step in ROOT_STEPS:
        assert any(i.startswith(f"J-ROOT-{step}") for i in ids), f"step {step} has no condition"
    # the parts that wait for L.J-ROOT.1's reviews or L.J-ROOT.2's records say so in their ids
    waiting = {i for i in ids if i.endswith(":host-record") or i.endswith(":review")}
    assert waiting == {
        "J-ROOT-4:review",
        "J-ROOT-5:host-record",
        "J-ROOT-6:host-record",
        "J-ROOT-7:review",
    }
    # a live condition is never default-collected (DM-80)
    assert (
        not root_mod.__file__.endswith(("_test.py",))
        and "test_" not in Path(root_mod.__file__).name
    )

    world = _clean_root_world(root_mod)
    assert all(ok for ok, _ in _verdicts(root_mod, world).values()), _verdicts(root_mod, world)


def test_root_module_never_imports_the_fence():
    import ast

    root_mod = _root_mod()
    tree = ast.parse(Path(root_mod.__file__).read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
    assert not [n for n in imported if n == "tests.proof.fence" or n.endswith(".fence")]
    # step 1 is the fence's own command, run as a subprocess and only for the carrier
    assert root_mod.COMMANDS["fence_history"][2:5] == ["tests.proof.fence", "check", "--history"]
    quiet = root_mod.World(results={"fence_history": (1, "planted")}, is_carrier=False)
    assert root_mod.verdict_1(quiet) == (True, "")
    loud = root_mod.World(results={"fence_history": (1, "planted")}, is_carrier=True)
    assert root_mod.verdict_1(loud)[0] is False


def test_root_conditions_each_fail_on_their_planted_defect():
    root_mod = _root_mod()
    planted = {
        "J-ROOT-2": {"d4": (1, "unequal")},
        "J-ROOT-3": {"perf": (1, "budget exceeded")},
        "J-ROOT-3:spine-budget": {"spine_budget": (1, "over the budget")},
        "J-ROOT-4": {"security_guards": (1, "forbidden binary passed")},
        "J-ROOT-5": {"spine": (1, "not one call")},
        "J-ROOT-6:baseline": {"mypy": (1, "1 error")},
        "J-ROOT-6:host-record": {"enforce": (1, "no admissible host-proc record")},
        "J-ROOT-6:d1-closure": {"d1_closure": (1, "unappeared entry")},
        "J-ROOT-6:audit-rows": {"audit_rows": (1, "orphan row")},
        "J-ROOT-6:register": {"register_final": (1, "probe present")},
        "J-ROOT-6:open-questions": {"open_questions_final": (1, "claimed")},
        "J-ROOT-7": {"locality": (1, "cross-phase import")},
    }
    for cid, results in planted.items():
        world = _clean_root_world(root_mod)
        world.results = results
        verdicts = _verdicts(root_mod, world)
        assert verdicts[cid][0] is False, cid
        others = [c for c, (ok, _) in verdicts.items() if not ok and c != cid]
        # the d1 closure is step 2's and SC-3's (step 6): one command, two conditions
        assert others == (["J-ROOT-2"] if cid == "J-ROOT-6:d1-closure" else []), cid

    # the spine junit: absent outside CI is not evaluable, absent in CI fails
    world = _clean_root_world(root_mod)
    world.spine_junit, world.in_ci = False, False
    assert root_mod.verdict_3_spine(world) == (True, "")
    world.in_ci = True
    assert root_mod.verdict_3_spine(world)[0] is False


def test_root_host_conditions_judge_role_2_through_the_record_module():
    root_mod = _root_mod()
    from tests.proof.host import record as record_mod

    world = _clean_root_world(root_mod)
    assert root_mod.verdict_5_host(world)[0] is True
    # the pass set is CM-6's: a FAILED node in the host-proc record fails the condition
    world.host_proc = dict(
        world.host_proc,
        results=[{"nodeid": "t.py::x", "outcome": "FAILED", "labels": []}],
    )
    assert record_mod.pass_set_violations(world.host_proc, {}) == ["t.py::x: FAILED"]
    ok, why = root_mod.verdict_5_host(world)
    assert not ok and "host-proc pass set: t.py::x: FAILED" in why
    # each further fact of the host-docker record is a condition of its own
    for change in (
        {"status": "FAILED"},
        {"diff": {"unattributed": ["container x"], "engine_state_changed": False}},
        {"diff": {"unattributed": [], "engine_state_changed": True}},
        {"results": []},
    ):
        fresh = _clean_root_world(root_mod)
        fresh.host_docker = dict(fresh.host_docker, **change)
        assert root_mod.verdict_5_host(fresh)[0] is False, change
    stale = _clean_root_world(root_mod)
    stale.admissible = lambda record: (False, "a non-record path changed")
    assert "not admissible" in root_mod.verdict_5_host(stale)[1]


def test_root_live_world_resolves_records_only_through_record_select(monkeypatch):
    root_mod = _root_mod()
    from tests.proof.host import record as record_mod

    calls = []
    proc = {"sha": "c" * 40, "gate": "host-proc"}
    monkeypatch.setattr(
        record_mod, "select", lambda gate, anchor, cwd=None: calls.append((gate, anchor)) or proc
    )
    monkeypatch.setattr(
        record_mod,
        "paired_docker",
        lambda record, cwd=None: calls.append(("paired", record)) or None,
    )
    live = root_mod.LiveWorld("deadbeef")
    assert live.host_proc is proc and live.host_docker is None
    assert calls == [("host-proc", "deadbeef"), ("paired", proc)]


def test_root_dry_run_lists_only_host_record_and_review_pending(monkeypatch, capsys):
    """On a head where every command holds but L.J-ROOT.1/.2 have not run, `meta ckpt root --dry`
    lists only the `:host-record` and `:review` parts."""
    root_mod = _root_mod()
    world = _clean_root_world(root_mod, hosts=False, reviews=False)
    world.results = {}  # every command exits 0
    # the enforce command needs the host records, so it is the one that waits for them
    world.results["enforce"] = (1, "no admissible host-proc record at the candidate")
    monkeypatch.setattr(root_mod, "make_world", lambda commit: world)
    rc = meta_mod.main(["ckpt", "root", "--dry"])
    pending = [line for line in capsys.readouterr().out.splitlines() if line.startswith("pending:")]
    assert rc == 0
    assert pending == [
        "pending:J-ROOT-4:review",
        "pending:J-ROOT-5:host-record",
        "pending:J-ROOT-6:host-record",
        "pending:J-ROOT-7:review",
    ]
