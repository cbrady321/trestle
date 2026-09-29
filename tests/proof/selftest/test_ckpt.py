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
