"""Selftest for bundle landing: one PR branch carrying several plan merges,
landed by one loop run as one `WR-Merge: <id>` commit per gate, in gate order
(CM-1 trailers derived, CM-2 gates and R1-R6, CM-3 single-writer landing).
Temp repos with a bare origin, `gh` faked at the module seam; no network."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod
from tests.proof import trailers as trailers_mod

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
ENV.update(GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

BRANCH = "wr/x/bundle"
CI_YML = "on: [pull_request]\njobs:\n  lint: {runs-on: ubuntu-latest}\n"


def _sh(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _ident(repo: Path) -> None:
    _sh(repo, "config", "user.name", "t")
    _sh(repo, "config", "user.email", "t@t")


def _gate_toml(gates: list[tuple[str, list[str]]]) -> str:
    out = 'phase = "p"\n[[lane]]\nname = "x"\nbranch_prefix = "wr/x/"\nglobs = ["a/**"]\n'
    for merge, requires in gates:
        out += f'[[gate]]\nbranch = "{BRANCH}"\nmerge = "{merge}"\n'
        out += f"requires_merge = {json.dumps(requires)}\n"
    return out


@pytest.fixture
def rig(tmp_path: Path, monkeypatch):
    origin = tmp_path / "origin.git"
    _sh(tmp_path, "init", "-q", "--bare", str(origin))
    runner = tmp_path / "runner"
    _sh(tmp_path, "clone", "-q", str(origin), str(runner))
    _ident(runner)
    (runner / "a").mkdir()
    (runner / "a" / "seed.txt").write_text("seed")
    _sh(runner, "add", "a/seed.txt")
    _sh(runner, "commit", "-q", "-m", "root")
    _sh(runner, "push", "-q", "origin", "HEAD:refs/heads/master")
    _sh(runner, "checkout", "-q", "-b", BRANCH)

    (tmp_path / "fence.toml").write_text("leave = []\n")
    (tmp_path / "fence.d").mkdir()
    (tmp_path / "ci.yml").write_text(CI_YML)
    monkeypatch.setattr(fence_mod, "ROOT", runner)
    monkeypatch.setattr(fence_mod, "FENCE_PATH", tmp_path / "fence.toml")
    monkeypatch.setattr(fence_mod, "FENCE_D_DIR", tmp_path / "fence.d")
    monkeypatch.setattr(fence_mod, "CI_YML_PATH", tmp_path / "ci.yml")
    state = {"pr_head": None, "conclusions": {"lint": "success"}}
    monkeypatch.setattr(fence_mod, "pr_head_via_gh", lambda _b, _c: state["pr_head"])
    monkeypatch.setattr(fence_mod, "job_conclusions_via_gh", lambda _s, _c: state["conclusions"])
    return {
        "origin": origin,
        "runner": runner,
        "tmp": tmp_path,
        "state": state,
        "fence_d": tmp_path / "fence.d" / "p.toml",
    }


def _config(rig, gates: list[tuple[str, list[str]]]) -> fence_mod.FenceConfig:
    rig["fence_d"].write_text(_gate_toml(gates))
    return fence_mod.load_fence(rig["tmp"] / "fence.toml", rig["tmp"] / "fence.d")


def _chunk(rig, gid: str, files: dict[str, str] | None = None, marker: bool = True) -> None:
    """One gate's chunk on the bundle branch: a content commit, then (unless
    `marker` is false) its empty `Bundle-Merge: <gid>` commit."""
    runner = rig["runner"]
    for name, text in (files or {f"a/{gid.lower()}.txt": gid}).items():
        (runner / name).parent.mkdir(parents=True, exist_ok=True)
        (runner / name).write_text(text)
        _sh(runner, "add", name)
    _sh(runner, "commit", "-q", "--allow-empty", "-m", f"chunk {gid}")
    if marker:
        _sh(runner, "commit", "-q", "--allow-empty", "-m", f"Bundle-Merge: {gid}")


def _publish(rig) -> str:
    _sh(rig["runner"], "push", "-q", "-f", "origin", f"HEAD:refs/heads/{BRANCH}")
    head = _sh(rig["runner"], "rev-parse", "HEAD")
    rig["state"]["pr_head"] = head
    return head


def _merge(rig, cfg, head, **kwargs):
    return fence_mod.fence_merge(
        cfg,
        rig["runner"],
        BRANCH,
        head,
        rig["state"]["pr_head"],
        job_conclusions=rig["state"]["conclusions"],
        required=["lint"],
        **kwargs,
    )


def _master(rig) -> str:
    return _sh(rig["origin"], "rev-parse", "master")


def _master_subjects(rig) -> list[str]:
    """First-parent subjects on origin/master, oldest first."""
    out = _sh(rig["origin"], "log", "--first-parent", "--reverse", "--format=%s", "master")
    return out.splitlines()


def _count_pushes(monkeypatch) -> list[tuple[str, ...]]:
    pushes: list[tuple[str, ...]] = []
    real = fence_mod._git

    def spy(cwd, *args, **kwargs):
        if args and args[0] == "push":
            pushes.append(args)
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(fence_mod, "_git", spy)
    return pushes


# --- (a) two gates ---------------------------------------------------------


def test_two_gate_bundle_lands_as_two_merges_in_one_push(rig, monkeypatch):
    cfg = _config(rig, [("B", ["A"]), ("A", [])])  # config order is not landing order
    _chunk(rig, "A")
    _chunk(rig, "B")
    head = _publish(rig)
    pushes = _count_pushes(monkeypatch)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.LANDED, msg
    assert len(pushes) == 1  # one plain non-force push for the whole run
    assert "--force" not in pushes[0] and "-f" not in pushes[0]
    subjects = _master_subjects(rig)
    assert subjects[-2:] == ["WR-Merge: A", "WR-Merge: B"]
    _sh(rig["runner"], "fetch", "-q", "origin")
    a = trailers_mod.landing("A", ref="origin/master", cwd=rig["runner"])
    b = trailers_mod.landing("B", ref="origin/master", cwd=rig["runner"])
    assert a and b and a != b
    assert msg == b
    # B's merge is on top of A's; B's second parent is the branch head
    assert _sh(rig["origin"], "rev-parse", "master^") == a
    assert _sh(rig["origin"], "rev-parse", "master^2") == head
    # each merge carries only its own chunk
    assert _sh(rig["origin"], "diff", "--name-only", f"{a}^", a) == "a/a.txt"
    assert _sh(rig["origin"], "diff", "--name-only", f"{b}^", b) == "a/b.txt"


# --- (b) three gates -------------------------------------------------------


def test_three_gate_bundle_lands_in_requires_merge_order(rig):
    cfg = _config(rig, [("C", ["B"]), ("A", []), ("B", ["A"])])
    root = _sh(rig["runner"], "rev-parse", "origin/master")
    for gid in ("A", "B", "C"):
        _chunk(rig, gid)
    head = _publish(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.LANDED, msg
    assert _master_subjects(rig)[-3:] == ["WR-Merge: A", "WR-Merge: B", "WR-Merge: C"]
    _sh(rig["runner"], "fetch", "-q", "origin")
    history = fence_mod.check_history(f"{root}..origin/master", rig["runner"])
    assert history.ok, history.message  # one trailer per merge, no anomaly


def test_a_landing_that_already_exists_is_a_wr_fix(rig):
    """The single-branch rule carries over: an id with a landing on the base
    lands again as `WR-Fix`."""
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    head = _publish(rig)
    # A lands on its own earlier (an unrelated earlier branch), then the bundle
    _sh(rig["runner"], "checkout", "-q", "-b", "earlier", "origin/master")
    (rig["runner"] / "a" / "early.txt").write_text("e")
    _sh(rig["runner"], "add", "a/early.txt")
    _sh(rig["runner"], "commit", "-q", "-m", "earlier work")
    _sh(rig["runner"], "checkout", "-q", "-b", "_landing", "origin/master")
    _sh(rig["runner"], "merge", "-q", "--no-ff", "-m", "WR-Merge: A", "earlier")
    _sh(rig["runner"], "push", "-q", "origin", "HEAD:refs/heads/master")
    # the bundle branch now must contain master (R3): merge it forward
    _sh(rig["runner"], "checkout", "-q", BRANCH)
    _sh(rig["runner"], "merge", "-q", "--no-edit", "origin/master")
    head = _publish(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.LANDED, msg
    assert _master_subjects(rig)[-2:] == ["WR-Fix: A", "WR-Merge: B"]


# --- (c) idempotent re-run after a partial landing ---------------------------


def test_rerun_after_partial_landing_lands_only_the_rest(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"]), ("C", ["B"])])
    _chunk(rig, "A")
    boundary_a = _sh(rig["runner"], "rev-parse", "HEAD")
    _chunk(rig, "B")
    _chunk(rig, "C")
    head = _publish(rig)
    # a previous run landed gate A and stopped before the rest
    _sh(rig["runner"], "checkout", "-q", "-b", "_partial", "origin/master")
    _sh(rig["runner"], "merge", "-q", "--no-ff", "-m", "WR-Merge: A", boundary_a)
    _sh(rig["runner"], "push", "-q", "origin", "HEAD:refs/heads/master")
    a_landing = _master(rig)
    _sh(rig["runner"], "checkout", "-q", BRANCH)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.LANDED, msg
    subjects = _master_subjects(rig)
    assert subjects[-3:] == ["WR-Merge: A", "WR-Merge: B", "WR-Merge: C"]
    assert subjects.count("WR-Merge: A") == 1  # never re-landed
    assert _sh(rig["origin"], "rev-parse", "master~2") == a_landing


def test_rerun_after_full_landing_pushes_nothing_new(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    head = _publish(rig)
    assert _merge(rig, cfg, head)[0] == fence_mod.FenceMergeExit.LANDED
    before = _master(rig)
    code, _msg = _merge(rig, cfg, head)
    assert code == fence_mod.FenceMergeExit.LANDED
    assert _master(rig) == before


# --- (d)-(f) boundary markers ------------------------------------------------


def test_missing_marker_is_refused_naming_the_gate(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A", marker=False)  # gate A has no marker
    _chunk(rig, "B")
    head = _publish(rig)
    before = _master(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate A" in msg and "Bundle-Merge: A" in msg
    assert _master(rig) == before


def test_markers_out_of_order_are_refused(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "B")
    _chunk(rig, "A")
    head = _publish(rig)
    before = _master(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate A" in msg and "out of order" in msg
    assert _master(rig) == before


def test_a_fix_commit_after_the_last_marker_belongs_to_the_last_chunk(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    _chunk(rig, "Z", marker=False)  # a post-marker fix, inside the lane globs
    head = _publish(rig)
    gates = fence_mod.match_gates(BRANCH, cfg.gates)
    run = fence_mod.resolve_bundle(rig["runner"], gates, _master(rig), head)
    assert run.boundaries["B"] == head

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.LANDED, msg
    assert _master_subjects(rig)[-2:] == ["WR-Merge: A", "WR-Merge: B"]
    assert _sh(rig["origin"], "rev-parse", "master^{tree}") == _sh(
        rig["runner"], "rev-parse", "HEAD^{tree}"
    )
    assert _sh(rig["origin"], "show", "master:a/z.txt") == "Z"


def test_a_commit_after_a_non_last_marker_needs_its_own_gates_marker(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B", marker=False)  # gate B never gets its marker
    head = _publish(rig)
    before = _master(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate B" in msg and "Bundle-Merge: B" in msg
    assert _master(rig) == before


def test_a_post_last_marker_commit_outside_the_lane_is_refused_by_r4(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    _chunk(rig, "Z", files={"outside.txt": "out of lane"}, marker=False)
    head = _publish(rig)
    before = _master(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "R4" in msg and "outside.txt" in msg
    assert _master(rig) == before


def test_a_post_last_marker_trailer_or_second_marker_is_refused(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    _sh(rig["runner"], "commit", "-q", "--allow-empty", "-m", "typed by hand\n\nWR-Fix: B")
    head = _publish(rig)
    before = _master(rig)
    code, msg = _merge(rig, cfg, head)
    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "R5" in msg or "trailer" in msg
    assert _master(rig) == before

    _sh(rig["runner"], "reset", "-q", "--hard", "origin/master")
    _chunk(rig, "A")
    _chunk(rig, "B")
    _sh(rig["runner"], "commit", "-q", "--allow-empty", "-m", "Bundle-Merge: B")
    head = _publish(rig)
    code, msg = _merge(rig, cfg, head)
    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate B" in msg and "more than one" in msg
    assert _master(rig) == before


def test_a_post_last_marker_merge_of_a_non_base_branch_is_refused(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    runner = rig["runner"]
    _sh(runner, "checkout", "-q", "-b", "side")
    (runner / "a").mkdir(exist_ok=True)
    (runner / "a" / "side.txt").write_text("s")
    _sh(runner, "add", "a/side.txt")
    _sh(runner, "commit", "-q", "-m", "side work")
    _sh(runner, "checkout", "-q", BRANCH)
    _sh(runner, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    head = _publish(rig)
    before = _master(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate B" in msg and "merges something other than the base" in msg
    assert _master(rig) == before


def test_duplicate_and_unknown_markers_are_refused(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "A", files={"a/more.txt": "m"})
    _chunk(rig, "B")
    head = _publish(rig)
    code, msg = _merge(rig, cfg, head)
    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate A" in msg and "more than one" in msg

    _sh(rig["runner"], "reset", "-q", "--hard", "origin/master")
    _chunk(rig, "A")
    _chunk(rig, "Q")
    _chunk(rig, "B")
    head = _publish(rig)
    code, msg = _merge(rig, cfg, head)
    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate Q" in msg


def test_a_merge_forward_of_master_after_the_last_marker_is_allowed(rig):
    """`_refresh_stale` merges `origin/master` into a stale READY branch; that
    tail must not make an otherwise valid bundle unlandable."""
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    _chunk(rig, "Z", marker=False)  # a fix, then the merge-forward
    _publish(rig)
    other = rig["tmp"] / "other"
    _sh(rig["tmp"], "clone", "-q", str(rig["origin"]), str(other))
    _ident(other)
    _sh(other, "checkout", "-q", "-B", "master", "origin/master")
    (other / "z.txt").write_text("z")
    _sh(other, "add", "z.txt")
    _sh(other, "commit", "-q", "-m", "unrelated master work")
    _sh(other, "push", "-q", "origin", "HEAD:refs/heads/master")
    _sh(rig["runner"], "fetch", "-q", "origin")
    _sh(rig["runner"], "merge", "-q", "--no-edit", "origin/master")
    head = _publish(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.LANDED, msg
    assert _master_subjects(rig)[-2:] == ["WR-Merge: A", "WR-Merge: B"]


# --- (g) K-doc rule per gate ---------------------------------------------------


def test_kdoc_rule_is_enforced_per_gate(rig, monkeypatch):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    # docs for gate B are edited in A's chunk, not B's: B's own merge misses them
    _chunk(rig, "A", files={"a/a.txt": "A", "a/docs-b.md": "doc"})
    _chunk(rig, "B")
    head = _publish(rig)
    k_items = [{"id": "K-B", "landing_merge": "B", "docs": ["a/docs-b.md"]}]
    monkeypatch.setattr("tests.proof.kdoc.load_k_doc_map", lambda *a, **kw: k_items)
    before = _master(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate B" in msg and "kdoc" in msg and "a/docs-b.md" in msg
    assert _master(rig) == before  # gate A's landing was not pushed either


def test_kdoc_rule_passes_when_the_gates_own_chunk_edits_the_docs(rig, monkeypatch):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B", files={"a/b.txt": "B", "a/docs-b.md": "doc"})
    head = _publish(rig)
    k_items = [{"id": "K-B", "landing_merge": "B", "docs": ["a/docs-b.md"]}]
    monkeypatch.setattr("tests.proof.kdoc.load_k_doc_map", lambda *a, **kw: k_items)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.LANDED, msg


# --- (h) all-or-nothing --------------------------------------------------------


def test_refusal_at_the_second_gate_leaves_origin_master_unchanged(rig, monkeypatch):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B", files={"outside.txt": "out of lane"})  # R4 fails at gate B only
    head = _publish(rig)
    before = _master(rig)
    pushes = _count_pushes(monkeypatch)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate B" in msg and "R4" in msg and "outside.txt" in msg
    assert _master(rig) == before
    assert pushes == []


def test_a_typed_wr_merge_in_a_chunk_is_refused_at_its_gate(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _sh(rig["runner"], "commit", "-q", "--allow-empty", "-m", "typed by hand\n\nWR-Merge: B")
    _chunk(rig, "B")
    head = _publish(rig)
    before = _master(rig)

    code, msg = _merge(rig, cfg, head)

    assert code == fence_mod.FenceMergeExit.VERDICT_REFUSED
    assert "gate B" in msg and "R5" in msg
    assert _master(rig) == before


def test_push_refusal_applies_to_the_whole_run(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    head = _publish(rig)
    before = _master(rig)
    code, _msg = _merge(rig, cfg, head, push_result="non-ff")
    assert code == fence_mod.FenceMergeExit.MASTER_MOVED
    code, _msg = _merge(rig, cfg, head, push_result="other")
    assert code == fence_mod.FenceMergeExit.PUSH_REFUSED
    assert _master(rig) == before


def test_master_moved_is_reported_not_refused(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A")
    _chunk(rig, "B")
    head = _publish(rig)
    other = rig["tmp"] / "other"
    _sh(rig["tmp"], "clone", "-q", str(rig["origin"]), str(other))
    _ident(other)
    _sh(other, "checkout", "-q", "-B", "master", "origin/master")
    _sh(other, "commit", "-q", "--allow-empty", "-m", "out of band")
    _sh(other, "push", "-q", "origin", "HEAD:refs/heads/master")
    code, _msg = _merge(rig, cfg, head)
    assert code == fence_mod.FenceMergeExit.MASTER_MOVED


# --- (i) match_gate / match_gates -------------------------------------------------


def test_match_gate_on_a_bundle_branch_points_at_match_gates():
    gates = [
        fence_mod.Gate(branch=BRANCH, merge="A"),
        fence_mod.Gate(branch=BRANCH, merge="B", requires_merge=["A"]),
    ]
    with pytest.raises(fence_mod.GateMatchError, match="match_gates"):
        fence_mod.match_gate(BRANCH, gates)
    assert [g.merge for g in fence_mod.match_gates(BRANCH, gates)] == ["A", "B"]


def test_match_gates_orders_topologically_and_rejects_cycles_and_prefix_mixes():
    def gate(merge, requires=(), branch=BRANCH):
        return fence_mod.Gate(branch=branch, merge=merge, requires_merge=list(requires))

    ordered = fence_mod.match_gates(
        BRANCH, [gate("C", ["B"]), gate("B", ["A", "EXTERNAL"]), gate("A")]
    )
    assert [g.merge for g in ordered] == ["A", "B", "C"]
    with pytest.raises(fence_mod.GateMatchError, match="cycle"):
        fence_mod.match_gates(BRANCH, [gate("A", ["B"]), gate("B", ["A"])])
    with pytest.raises(fence_mod.GateMatchError, match="prefix"):
        fence_mod.match_gates(BRANCH, [gate("A"), gate("B", branch="wr/x/")])
    with pytest.raises(fence_mod.GateMatchError, match="checkpoint"):
        fence_mod.match_gates(BRANCH, [gate("A"), gate("J-CORE")])
    with pytest.raises(fence_mod.GateMatchError, match="0 gate"):
        fence_mod.match_gates("wr/other/x", [gate("A")])


def test_match_gates_on_an_ordinary_branch_is_match_gate():
    gates = [fence_mod.Gate(branch="wr/x/", merge="P"), fence_mod.Gate(branch="wr/y/q", merge="Q")]
    assert fence_mod.match_gates("wr/x/anything", gates) == [gates[0]]
    assert fence_mod.match_gates("wr/y/q", gates) == [gates[1]]


# --- (j) trailer rules ---------------------------------------------------------------


def test_bundle_marker_is_not_a_trailer_but_a_typed_wr_merge_still_is():
    marker = trailers_mod.Commit(sha="1", message="Bundle-Merge: A\n")
    assert marker.trailers() == []
    assert marker.bundle_markers() == ["A"]
    typed = trailers_mod.Commit(sha="2", message="work\n\nWR-Merge: A\n")
    assert typed.trailers() == [("WR-Merge", "A")]
    assert typed.bundle_markers() == []


def test_check_pr_accepts_markers_and_rejects_a_typed_trailer(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _sh(rig["runner"], "fetch", "-q", "origin")
    base = _sh(rig["runner"], "rev-parse", "origin/master")
    _chunk(rig, "A")
    _chunk(rig, "B")
    head = _sh(rig["runner"], "rev-parse", "HEAD")
    result = fence_mod.check_pr(cfg, rig["runner"], BRANCH, head, base)
    assert result.ok, result.message

    _sh(rig["runner"], "commit", "-q", "--allow-empty", "-m", "WR-Merge: B")
    _chunk(rig, "B2", marker=False)
    typed = _sh(rig["runner"], "rev-parse", "HEAD")
    result = fence_mod.check_pr(cfg, rig["runner"], BRANCH, typed, base)
    assert not result.ok and result.rule == "R5"


def test_check_pr_on_a_bundle_checks_markers_predecessors_and_the_lane_once(rig):
    cfg = _config(rig, [("A", ["PRE"]), ("B", ["A"])])
    _sh(rig["runner"], "fetch", "-q", "origin")
    base = _sh(rig["runner"], "rev-parse", "origin/master")
    _chunk(rig, "A")
    _chunk(rig, "B")
    head = _sh(rig["runner"], "rev-parse", "HEAD")
    result = fence_mod.check_pr(cfg, rig["runner"], BRANCH, head, base)
    assert not result.ok and result.rule == "R2"
    assert "gate A" in result.message and "PRE" in result.message

    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "C", files={"outside.txt": "x"})
    _chunk(rig, "D", marker=False)
    bad = _sh(rig["runner"], "rev-parse", "HEAD")
    result = fence_mod.check_pr(cfg, rig["runner"], BRANCH, bad, base)
    assert not result.ok and result.rule == "R4"


def test_check_pr_reports_a_bundle_marker_problem_naming_the_gate(rig):
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _sh(rig["runner"], "fetch", "-q", "origin")
    base = _sh(rig["runner"], "rev-parse", "origin/master")
    _chunk(rig, "B")
    _chunk(rig, "A")
    head = _sh(rig["runner"], "rev-parse", "HEAD")
    result = fence_mod.check_pr(cfg, rig["runner"], BRANCH, head, base)
    assert not result.ok and result.rule == "bundle" and "gate A" in result.message


# --- (k) the loop prints and persists the refusal ------------------------------------


def _ready_bundle(rig, gates):
    rig["fence_d"].write_text(_gate_toml(gates))
    head = _publish(rig)
    assert fence_mod.main(["ready", "mark"]) == 0
    return head


def test_ready_mark_on_a_bundle_is_keyed_by_the_last_gate_and_records_the_gates(rig, capsys):
    gates = [("B", ["A"]), ("A", [])]
    _chunk(rig, "A")
    _chunk(rig, "B")
    head = _ready_bundle(rig, gates)
    state_dir = fence_mod.default_state_dir(rig["runner"])
    (entry,) = fence_mod.ready_status(state_dir)
    assert entry["_merge_id"] == "B" and entry["gates"] == ["A", "B"]
    assert entry["branch"] == BRANCH and entry["head_sha"] == head
    capsys.readouterr()
    assert fence_mod.main(["ready", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["gates"] == ["A", "B"]
    # ci-status reads the branch head; it never needs a gate
    assert (
        fence_mod.ci_status(
            rig["runner"],
            branch=BRANCH,
            required=["lint"],
            conclusions_reader=lambda *_: {"lint": "success"},
        )
        == 0
    )
    assert fence_mod.main(["ready", "unmark"]) == 0
    assert fence_mod.ready_status(state_dir) == []


def test_land_once_lands_a_bundle_as_one_merge_per_gate(rig, capsys):
    _chunk(rig, "A")
    _chunk(rig, "B")
    _ready_bundle(rig, [("A", []), ("B", ["A"])])

    assert fence_mod.main(["land", "--once"]) == 0

    assert _master_subjects(rig)[-2:] == ["WR-Merge: A", "WR-Merge: B"]
    assert fence_mod.ready_status(fence_mod.default_state_dir(rig["runner"])) == []
    assert "fence land: B: landed" in capsys.readouterr().out


def test_land_once_prints_and_persists_the_refusal_reason(rig, capsys):
    _chunk(rig, "A")
    _chunk(rig, "B", files={"outside.txt": "x"})
    _ready_bundle(rig, [("A", []), ("B", ["A"])])
    before = _master(rig)
    capsys.readouterr()

    assert fence_mod.main(["land", "--once"]) == 0

    out = capsys.readouterr().out
    assert "fence land: B: verdict refused: gate B: R4: outside.txt" in out
    (entry,) = fence_mod.ready_status(fence_mod.default_state_dir(rig["runner"]))
    assert entry["returns"] == 1 and entry["state"] == "ready"
    assert entry["reason"].startswith("verdict refused: gate B: R4:")
    assert _master(rig) == before


def test_attempt_landing_records_the_refusal_message_in_the_entry(rig):
    """The reason is no longer dropped to a bare "verdict refused": the entry
    (and the escalation log, when it escalates) keep the gate and rule."""
    cfg = _config(rig, [("A", []), ("B", ["A"])])
    _chunk(rig, "A", marker=False)
    _chunk(rig, "B")
    head = _publish(rig)
    state_dir = rig["tmp"] / "state"
    fence_mod._atomic_write(
        fence_mod._ready_path(state_dir, "B"),
        {
            "state": "ready",
            "branch": BRANCH,
            "returns": 0,
            "ckpt_rebases": 0,
            "reason": None,
            "landing_sha": None,
            "marked_at": 1.0,
            "head_sha": head,
            "gates": ["A", "B"],
        },
    )
    deps = fence_mod.LandingDeps(
        cfg=cfg,
        cwd=rig["runner"],
        state_dir=state_dir,
        pr_head_sha=head,
        job_conclusions={"lint": "success"},
        required=["lint"],
    )

    assert fence_mod.attempt_landing("B", deps) == "verdict refused"

    entry = json.loads(fence_mod._ready_path(state_dir, "B").read_text())
    assert "gate A" in entry["reason"] and "Bundle-Merge: A" in entry["reason"]
    assert entry["returns"] == 1  # still classified and counted as a verdict refusal
