"""L.CS-1.4: the CK decline drill (CM-7, MC-CORE-12), exercised on planted histories.

Every planted case runs in a temporary git repo with repo-local identity. The real-history
drill, `test_decline_patch_applies_and_isolates[<CK>]`, has one parameter per module of
`declines/`; with none on HEAD it is collected empty (self-test only), and it runs the full
nested REG only under the `ck-isolation` job or when named by node id."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.core.tooling import ck_drill
from tests.proof import fence as fence_mod
from tests.proof import register

ROOT = Path(__file__).resolve().parents[3]

BASE_CORE = """\
def stop(x):
    return x


def run(x):
    return stop(x)
"""

CK_CORE = """\
SWITCH: bool = True


def stop(x):
    return x


def run(x):
    if SWITCH:
        stop(x)
    return x
"""

LATER_FUNCTION = "\n\ndef later(x):\n    return x + 1\n"
LATER_CORE = CK_CORE + LATER_FUNCTION

BASE_DOCS = "# agents\n\nintro\n"
CK_DOCS = "# agents\n\nintro\n\n<!-- K-9 -->\nthe K-9 text\n<!-- /K-9 -->\n"

ENTRY_T9 = """\
[[entry]]
id = "T-9"
mechanism = "a workaround"
introduced_by = "L.P0-1.1"
serves = ["A1.1"]
probe = "true"
removal_condition = "the CK lands"
removed_by = "L.CK-9.1"
permanent = false
"""
ENTRY_OTHER = ENTRY_T9.replace("T-9", "T-5").replace("L.CK-9.1", "L.SV-1.1")

LABELS = (
    """\
[[label]]
id = "WR-X-1:other"
row = "WR-X-1"
step = "core"
slice = "core"
tier = "PROC"
venue = "CI"
posture = "claim"
declared_by = "L.CS-1.1"
"""
    + """
[[label]]
id = "WR-PROOF-10:K-9"
row = "WR-PROOF-10"
step = "core"
slice = "core"
tier = "INSPECT"
venue = "CI"
posture = "claim"
declared_by = "L.CK-9.1"
"""
)

DECLINE = {
    "merge": "CK-9",
    "k": "K-9",
    "switch": {"module": "pkg.core", "name": "SWITCH", "declined": False},
    "restores": ["T-9"],
    "labels": ["WR-PROOF-10:K-9"],
    "clauses": ["A9.9"],
}
LANE_GLOBS = [
    "pkg/**",
    "docs/agents.md",
    "tests/core/**",
    "tests/proof/temporary.toml",
    "tests/proof/labels.d/core.toml",
]

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
ENV |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV)
    assert proc.returncode == 0, f"git {args}: {proc.stderr}"
    return proc.stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _merge_branch(repo: Path, branch: str, trailer: str) -> str:
    _git(repo, "merge", "--no-ff", "-q", branch, "-m", f"merge {branch}\n\n{trailer}")
    return _git(repo, "rev-parse", "HEAD")


def _plant(tmp_path: Path, *, core_after_ck: str = CK_CORE) -> tuple[Path, str]:
    """master: base; branch ck-9: the CK merge's leaves; master: WR-Merge: CK-9 landing; a later
    merge edits pkg/core.py (a file of the CK's landing diff). Returns (repo, base sha)."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    _write(repo, "pkg/core.py", BASE_CORE)
    _write(repo, "docs/agents.md", BASE_DOCS)
    _write(repo, "tests/proof/temporary.toml", ENTRY_T9 + "\n" + ENTRY_OTHER)
    _write(repo, "tests/proof/labels.d/core.toml", LABELS)
    _write(repo, "other.py", "x = 1\n")
    base = _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "wr/x/ck-9")
    _write(repo, "pkg/core.py", core_after_ck)
    _write(repo, "docs/agents.md", CK_DOCS)
    _write(repo, "tests/proof/temporary.toml", ENTRY_OTHER)
    _write(repo, "tests/core/test_ck9.py", "def test_ck9():\n    pass\n")
    _commit(repo, "L.CK-9.1: the CK leaf")
    _git(repo, "checkout", "-q", "master")
    return repo, base


def _land(repo: Path) -> None:
    _merge_branch(repo, "wr/x/ck-9", "WR-Merge: CK-9")
    _git(repo, "checkout", "-q", "-b", "wr/x/later")
    _write(repo, "pkg/core.py", (repo / "pkg/core.py").read_text() + LATER_FUNCTION)
    _commit(repo, "L.LATER.1: a later merge edits a file of the CK's landing diff")
    _git(repo, "checkout", "-q", "master")
    _merge_branch(repo, "wr/x/later", "WR-Merge: LATER")


def test_decline_patch_on_planted_ck_history(tmp_path: Path) -> None:
    repo, _ = _plant(tmp_path)
    _land(repo)
    head = _git(repo, "rev-parse", "HEAD")
    report = ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS)
    assert report.problems == []
    patch = report.patch
    # the file a later merge edited stays in place, carrying the decline through its switch
    assert "pkg/core.py" in patch.left and "pkg/core.py" not in patch.reverted
    assert patch.edits["pkg/core.py"] == LATER_CORE.replace(
        "SWITCH: bool = True", "SWITCH: bool = False"
    )
    assert "def later(x)" in patch.edits["pkg/core.py"]  # no textual revert
    # files no later commit changed are reverted from the landing diff, inside the lane's globs
    assert patch.reverted == {
        "docs/agents.md",
        "tests/core/test_ck9.py",
        "tests/proof/temporary.toml",
    }
    assert patch.edits["tests/core/test_ck9.py"] is None
    assert "K-9" not in patch.edits["docs/agents.md"]
    # the register entry comes back named-not-removed, once, and the label is na
    restored = ck_drill.register_entries_of(patch.edits["tests/proof/temporary.toml"])
    assert restored["T-9"]["removed_by"] == "named-not-removed"
    assert restored["T-9"]["citation"] == "K-9 declined" and restored["T-9"]["serves"] == []
    assert restored["T-5"]["removed_by"] == "L.SV-1.1"
    assert patch.edits["tests/proof/labels.d/core.toml"].count('id = "WR-PROOF-10:K-9"') == 1
    assert 'posture = "na"' in patch.edits["tests/proof/labels.d/core.toml"]
    # nothing outside CM-7's derived set was touched, and the drill left master alone
    assert report.touched == patch.scope
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert "scratch" not in _git(repo, "worktree", "list")


def test_planted_patch_loads_under_p0_register_and_label_loaders(tmp_path: Path) -> None:
    repo, _ = _plant(tmp_path)
    _land(repo)
    patch = ck_drill.derive_patch(repo, DECLINE, LANE_GLOBS, ck_drill.span_for(repo, "CK-9"))
    for text in (patch.edits["tests/proof/temporary.toml"],):
        p = tmp_path / "temporary.toml"
        p.write_text(text)
        entries = register.load_entries(temporary_path=p, d_dir=tmp_path / "none")
        by_id = {e["id"]: e for e in entries}
        assert by_id["T-9"]["removed_by"] == "named-not-removed" and by_id["T-9"]["serves"] == []


def test_drill_on_the_cks_own_pr_derives_the_scope_from_the_pr_diff(tmp_path: Path) -> None:
    repo, base = _plant(tmp_path)
    _git(repo, "checkout", "-q", "wr/x/ck-9")  # before the merge lands: HEAD is the PR head H
    span = ck_drill.span_for(repo, "CK-9", pr_base=base)
    assert span.landing is None and span.base == base
    report = ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS, span=span)
    assert report.problems == []
    # no later commit exists: every lane-glob file of X..H is reverted, so the switch's own file
    # goes back to the base and carries no decline (the K-block and the register entry come back)
    assert report.patch.left == set()
    assert report.patch.reverted == {
        "pkg/core.py",
        "docs/agents.md",
        "tests/core/test_ck9.py",
        "tests/proof/temporary.toml",
    }
    assert report.touched == report.patch.scope


LATER_DOCS = "\nthe later chunk's text\n"


def _marker(repo: Path, gate: str) -> str:
    _git(repo, "commit", "-q", "--allow-empty", "-m", f"Bundle-Merge: {gate}")
    return _git(repo, "rev-parse", "HEAD")


def _plant_bundle(tmp_path: Path, *, later_marker: bool = True) -> tuple[Path, dict[str, str]]:
    """master: base (no switch module); bundle branch, first-parent: chunk A adds pkg/core.py (as
    CL-C2 adds `_codec.py`) + `Bundle-Merge: A`; the CK-9 chunk + `Bundle-Merge: CK-9`; a LATER
    chunk editing docs/agents.md (a CK chunk file) + `Bundle-Merge: LATER` unless not yet written.
    origin/master stays at the base. Returns (repo, shas by name)."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    _write(repo, "docs/agents.md", BASE_DOCS)
    _write(repo, "tests/proof/temporary.toml", ENTRY_T9 + "\n" + ENTRY_OTHER)
    _write(repo, "tests/proof/labels.d/core.toml", LABELS)
    _write(repo, "other.py", "x = 1\n")
    shas = {"base": _commit(repo, "base")}
    _git(repo, "update-ref", "refs/remotes/origin/master", shas["base"])
    _git(repo, "checkout", "-q", "-b", "wr/core-bundle/x")
    _write(repo, "pkg/core.py", BASE_CORE)
    _write(repo, "other.py", "x = 2\n")
    _commit(repo, "L.A.1: an earlier gate adds the switch module")
    shas["A"] = _marker(repo, "A")
    _write(repo, "pkg/core.py", CK_CORE)
    _write(repo, "docs/agents.md", CK_DOCS)
    _write(repo, "tests/proof/temporary.toml", ENTRY_OTHER)
    _write(repo, "tests/core/test_ck9.py", "def test_ck9():\n    pass\n")
    _commit(repo, "L.CK-9.1: the CK leaf")
    shas["CK-9"] = _marker(repo, "CK-9")
    _write(repo, "docs/agents.md", CK_DOCS + LATER_DOCS)
    shas["later"] = _commit(repo, "L.LATER.1: a later gate edits a file of the CK's chunk")
    if later_marker:
        shas["LATER"] = _marker(repo, "LATER")
    return repo, shas


def test_bundle_pr_drill_scopes_each_ck_to_its_own_chunk(tmp_path: Path) -> None:
    repo, shas = _plant_bundle(tmp_path)
    span = ck_drill.span_for(repo, "CK-9")
    # the chunk between the previous gate's marker and CK-9's own; the marker is its landing
    assert (span.base, span.head, span.landing) == (shas["A"], shas["CK-9"], shas["CK-9"])
    report = ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS)
    assert report.problems == []
    patch = report.patch
    # only the chunk's own files: chunk A's other.py / pkg/core.py creation is not the CK's
    assert "other.py" not in patch.scope
    assert patch.reverted == {"pkg/core.py", "tests/core/test_ck9.py", "tests/proof/temporary.toml"}
    assert patch.edits["pkg/core.py"] == BASE_CORE  # the pre-chunk text, not deleted
    assert patch.edits["tests/core/test_ck9.py"] is None
    # a chunk file a later chunk changed is left in place: the later edit survives the decline
    assert patch.left == {"docs/agents.md"}
    docs = patch.edits["docs/agents.md"]
    assert docs is not None and "K-9" not in docs and docs.endswith(LATER_DOCS)
    assert report.touched == patch.scope


def test_bundle_span_whole_pr_diff_deletes_an_earlier_gates_switch_module(tmp_path: Path) -> None:
    """The CK-3/4 failure at H: over merge-base..HEAD the switch module an earlier gate added is
    reverted to the base, i.e. deleted; over the CK's own chunk it goes back to that gate's text."""
    repo, shas = _plant_bundle(tmp_path)
    whole = ck_drill.Span(base=shas["base"], head="HEAD", landing=None)
    with pytest.raises(ck_drill.DrillFailure, match="has no file at HEAD"):
        ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS, span=whole)
    assert ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS).problems == []


def test_bundle_span_trailing_chunk_and_pr_merge_ref(tmp_path: Path) -> None:
    repo, shas = _plant_bundle(tmp_path, later_marker=False)
    # a gate whose marker is not written yet is the trailing chunk: last marker..HEAD
    span = ck_drill.span_for(repo, "LATER")
    assert (span.base, span.head, span.landing) == (shas["CK-9"], "HEAD", None)
    _marker(repo, "LATER")
    # actions/checkout on a PR: HEAD merges the bundle head into an advanced master
    _git(repo, "checkout", "-q", "master")
    _write(repo, "unrelated.txt", "master moved on\n")
    master = _commit(repo, "master moves on")
    _git(repo, "update-ref", "refs/remotes/origin/master", master)
    _git(repo, "checkout", "-q", "--detach", master)
    _git(repo, "merge", "--no-ff", "-q", "wr/core-bundle/x", "-m", "Merge into master")
    span = ck_drill.span_for(repo, "CK-9")
    assert (span.base, span.head, span.landing) == (shas["A"], shas["CK-9"], shas["CK-9"])
    assert ck_drill.span_for(repo, "A").base == shas["base"]  # the first gate: the fork point


def test_ordinary_pr_without_markers_keeps_the_whole_pr_diff(tmp_path: Path) -> None:
    repo, base = _plant(tmp_path)
    _git(repo, "update-ref", "refs/remotes/origin/master", base)
    _git(repo, "checkout", "-q", "wr/x/ck-9")
    assert ck_drill.span_for(repo, "CK-9") == ck_drill.Span(base=base, head="HEAD", landing=None)


def test_regression_runs_the_declined_tree_and_skips_the_baseline_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(root: Path, cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        calls.append((cmd, env))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr(ck_drill, "_run", fake_run)
    _write(tmp_path, ".github/workflows/ci.yml", "jobs: {}\n")
    assert ck_drill.run_regression(tmp_path, DECLINE).failures == []
    pytest_cmd, env = next((c, e) for c, e in calls if any(a.startswith("--deselect") for a in c))
    # spawned `python -P` children import trestle from the path: the declined scratch tree first
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path)
    assert "--deselect=tests/proof/selftest/test_baseline.py" in pytest_cmd


# A planted serial collection: plain files of uneven size, the two shared-resource files, one
# timing-sensitive node inside an otherwise parallel file, the results reader inside its own file,
# and the packages' test files.
FLAKY = "tests/test_user_stories.py::test_us08_hostile_plugin_control_plane_responsive"
READER = "tests/core/docs/test_cl_d1_deferrals.py::test_core_deferrals_cited_and_unproven"
SERIAL_NODES = (
    [f"tests/core/test_f{i}.py::test_{j}" for i in range(30) for j in range(1 + i % 7)]
    + ["tests/core/docs/test_cl_d1_deferrals.py::test_a", READER]
    + [f"tests/core/test_g{i}.py::test_0" for i in range(3)]
    + [f"tests/test_m6_extending.py::test_{j}" for j in range(3)]
    + ["tests/test_mcp_http_smoke.py::test_serve[a]", "tests/test_mcp_http_smoke.py::test_serve[b]"]
    + ["tests/test_user_stories.py::test_us01", FLAKY, "tests/test_user_stories.py::test_us09"]
    + [f"packages/trestle-packs/tests/test_p{i}.py::test_x" for i in range(4)]
    + [f"packages/trestle-env/tests/unit/test_e{i}.py::test_y" for i in range(2)]
)
REGISTERED = "tests/core/test_f3.py::test_9"


def _files(nodes: list[str]) -> set[str]:
    return {n.split("::", 1)[0] for n in nodes}


def test_reg_partition_deals_whole_files_and_keeps_the_serial_tail() -> None:
    plan = ck_drill.reg_partition(SERIAL_NODES, workers=4)
    chunks, tail = plan.chunks, plan.tail
    dealt = [n for g in [*chunks, *plan.packages, tail, *plan.readers] for n in g.nodes]
    assert sorted(dealt) == sorted(SERIAL_NODES)  # every node exactly once
    # whole files: no file's nodes are split between two parallel chunks
    for a in chunks:
        for b in chunks:
            assert a is b or not (_files(a.nodes) & _files(b.nodes))
    assert [len(c.nodes) for c in chunks] == sorted((len(c.nodes) for c in chunks), reverse=True)
    assert 1 < len(chunks) <= 4 * ck_drill.REG_CHUNKS_PER_WORKER
    # the shared-resource files and the timing-sensitive node run serially, after the chunks
    assert set(tail.paths) == {"tests/test_m6_extending.py", "tests/test_mcp_http_smoke.py", FLAKY}
    assert FLAKY in tail.nodes and len(tail.nodes) == 6
    (stories,) = [c for c in chunks if "tests/test_user_stories.py" in c.paths]
    assert stories.deselect == [FLAKY] and FLAKY not in stories.nodes
    # the results reader runs alone; its file's other node stays in a chunk
    assert [(r.paths, r.nodes) for r in plan.readers] == [([READER], [READER])]
    (docs,) = [c for c in chunks if "tests/core/docs/test_cl_d1_deferrals.py" in c.paths]
    assert READER in docs.deselect and READER not in docs.nodes
    # each package's files run in one process given its testpaths root, as REG reaches them
    packs, env = plan.packages
    assert packs.paths == ["packages/trestle-packs/tests"] and env.paths == [
        "packages/trestle-env/tests"
    ]
    assert _files(packs.nodes) == {f"packages/trestle-packs/tests/test_p{i}.py" for i in range(4)}
    assert len(env.nodes) == 2
    assert not any(p.startswith("packages/") for c in chunks for p in c.paths)


def test_reg_partition_moves_a_package_node_to_the_serial_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A race-shaped package node runs in the serial tail by its node id; its package's process
    keeps the testpaths root and deselects it, also when it is its file's only node (the root
    collects that file all the same): still every node exactly once. test_reuse_twin's
    `cancelled` case is no longer serial (its stop waits for the sibling's end): it stays in its
    package's process."""
    raced = (
        "packages/trestle-env/tests/proc/test_readiness_cancel.py::"
        "test_cancel_during_readiness_wait_prompt"
    )
    other = "packages/trestle-env/tests/proc/test_readiness_cancel.py::test_found_is_kept"
    # a planted serial node, the only node of its file
    alone = "packages/trestle-env/tests/twin/test_alone.py::test_raced_alone"
    monkeypatch.setattr(ck_drill, "REG_SERIAL_NODES", (*ck_drill.REG_SERIAL_NODES, alone))
    reused = (
        "packages/trestle-env/tests/twin/test_reuse_twin.py::"
        "test_prestarted_postgres_reused_untouched[cancelled]"
    )
    nodes = [*SERIAL_NODES, other, raced, alone, reused]
    plan = ck_drill.reg_partition(nodes, workers=4)
    dealt = [n for g in [*plan.chunks, *plan.packages, plan.tail, *plan.readers] for n in g.nodes]
    assert sorted(dealt) == sorted(nodes)
    assert {raced, alone} <= set(plan.tail.paths) and {raced, alone} <= set(plan.tail.nodes)
    (env,) = [g for g in plan.packages if g.paths == ["packages/trestle-env/tests"]]
    assert env.deselect == [raced, alone] and {other, reused} <= set(env.nodes)
    assert not {raced, alone} & set(env.nodes)
    assert reused not in plan.tail.nodes


def test_reg_workers_leave_a_core_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """REG's processes are the cores minus one, at most REG_MAX_WORKERS, never fewer than one."""
    for cores, want in ((1, 1), (2, 1), (4, 3), (16, ck_drill.REG_MAX_WORKERS)):
        cpus = set(range(cores))
        monkeypatch.setattr(ck_drill.os, "sched_getaffinity", lambda _pid, c=cpus: c, raising=False)
        assert ck_drill._reg_workers() == want  # noqa: SLF001


def _fake_pytest(serial: list[str], drop: str | None = None):
    """A stand-in `_run`: a recorded pytest command selects the planted nodes its paths name (all
    of them without paths) minus its deselections, records them where the collection recorder
    would, and writes one proof record per selected node into the results dir; every command
    exits 0. `drop` is a node the serial collection selects and no process runs. The results
    reader's command records the node ids of the results it saw."""
    calls: list[list[str]] = []
    seen_by_reader: list[str] = []

    def fake_run(root: Path, cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        calls.append(cmd)
        if "pytest" in cmd and ck_drill.REG_PLUGIN in cmd:
            args = cmd[cmd.index("pytest") + 1 :]
            values = {i + 1 for i, a in enumerate(args) if a in ("-c", "--rootdir", "-p")}
            paths = [a for i, a in enumerate(args) if not a.startswith("-") and i not in values]
            gone = [a.split("=", 1)[1] for a in args if a.startswith("--deselect=")]
            if "--collect-only" not in args and drop:
                gone.append(drop)
            universe = [*serial, REGISTERED, "tests/proof/selftest/test_baseline.py::test_b"]

            def under(node: str, path: str) -> bool:
                return node == path or node.startswith((path + "::", path + "[", path + "/"))

            picked = [
                n
                for n in universe
                if (not paths or any(under(n, p) for p in paths))
                and not any(under(n, d) for d in gone)
            ]
            Path(env[ck_drill.COLLECTED_ENV]).write_text(json.dumps(picked))
            results = root / "tests" / "proof" / "results"
            if "--collect-only" not in args:
                if paths == [READER]:
                    seen_by_reader.extend(
                        json.loads(x)["nodeid"]
                        for f in results.glob("*.jsonl")
                        for x in f.read_text().splitlines()
                    )
                results.mkdir(parents=True, exist_ok=True)
                with (results / "ci-test.jsonl").open("a") as fh:
                    fh.writelines(json.dumps({"nodeid": n}) + "\n" for n in picked)
        return subprocess.CompletedProcess(cmd, 0, stdout="{}\n", stderr="")

    return fake_run, calls, seen_by_reader


def _parallel(monkeypatch: pytest.MonkeyPatch, fake_run) -> None:
    monkeypatch.setattr(ck_drill, "_run", fake_run)
    monkeypatch.setattr(ck_drill, "registered_nodes", lambda root, ids, env: [REGISTERED])
    # an enclosing parallel REG's recorder (this file runs inside the drill's own REG) is not ours
    monkeypatch.setenv(ck_drill.COLLECTED_ENV, "/nonexistent/enclosing-recorder.json")


def test_parallel_regression_runs_the_serial_collection_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_run, calls, seen_by_reader = _fake_pytest(SERIAL_NODES)
    _parallel(monkeypatch, fake_run)
    _write(tmp_path, ".github/workflows/ci.yml", "jobs:\n  t:\n    steps:\n      - run: mypy\n")
    assert ck_drill.run_regression(tmp_path, DECLINE).failures == []
    # the serial collection is REG's literal `pytest -q` step, with its deselections
    assert "--collect-only" in calls[0]
    assert f"--deselect={REGISTERED}" in calls[0]
    assert "--deselect=tests/proof/selftest/test_baseline.py" in calls[0]
    runs = [" ".join(c) for c in calls]
    tail = next(i for i, c in enumerate(runs) if "tests/test_m6_extending.py" in c)
    reader = next(
        i for i, c in enumerate(runs) if c.endswith(f" {READER} -p {ck_drill.REG_PLUGIN}")
    )
    chunks = [i for i, c in enumerate(runs) if " tests/core/test_f" in c]
    ruff = [i for i, c in enumerate(runs) if " ruff " in c]
    # the tail starts after every parallel process, the reader after the tail; ruff lints last
    assert chunks and max(chunks) < tail < reader < min(ruff)
    assert any("mypy" in c for c in runs)
    # the CSC-12 packs session runs after the packs root's process, never beside it
    root = next(i for i, c in enumerate(runs) if " packages/trestle-packs/tests -p " in c)
    session = next(i for i, c in enumerate(runs) if c.endswith("packages/trestle-packs/tests"))
    assert root < session
    # the reader saw exactly the records of the nodes before it in the serial order
    assert sorted(seen_by_reader) == sorted(SERIAL_NODES[: SERIAL_NODES.index(READER)])
    # and every record is back afterwards, the reader's own included
    lines = (tmp_path / "tests/proof/results/ci-test.jsonl").read_text().splitlines()
    assert sorted(json.loads(x)["nodeid"] for x in lines) == sorted(SERIAL_NODES)


def test_parallel_regression_fails_when_a_node_runs_in_no_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dropped = "tests/core/test_f5.py::test_2"
    fake_run, _, _ = _fake_pytest(SERIAL_NODES, drop=dropped)
    _parallel(monkeypatch, fake_run)
    _write(tmp_path, ".github/workflows/ci.yml", "jobs: {}\n")
    failures = ck_drill.run_regression(tmp_path, DECLINE).failures
    assert failures == [f"REG partition: 1 node(s) ran in no process: [{dropped!r}]"]
    serial = list(SERIAL_NODES)
    problems = ck_drill.partition_problems(serial, {"a": serial[:11], "b": serial[10:], "c": None})
    assert problems == [
        "REG partition: c recorded no collection",
        f"REG partition: {serial[10]} ran in a and b",
    ]


def test_collection_recorder_writes_the_selected_node_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "ids.json"
    session = SimpleNamespace(items=[SimpleNamespace(nodeid="a.py::t[1]")])
    monkeypatch.delenv(ck_drill.COLLECTED_ENV, raising=False)
    ck_drill.pytest_collection_finish(session)
    assert not out.exists()  # inert outside the parallel REG
    monkeypatch.setenv(ck_drill.COLLECTED_ENV, str(out))
    ck_drill.pytest_collection_finish(session)
    assert json.loads(out.read_text()) == ["a.py::t[1]"]


def test_switch_isolation_fails_when_a_changed_call_site_ignores_the_switch(
    tmp_path: Path,
) -> None:
    ungated = CK_CORE.replace("    if SWITCH:\n        stop(x)\n", "    stop(x)\n")
    repo, _ = _plant(tmp_path, core_after_ck=ungated)
    _land(repo)
    # the switch is defined but the changed call `stop(x)` does not read it
    report = ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS)
    assert any("pkg/core.py" in p and "does not read SWITCH" in p for p in report.problems)
    # a guard clause before the call counts as reading the switch
    guarded = CK_CORE.replace(
        "    if SWITCH:\n        stop(x)\n    return x\n",
        "    if not SWITCH:\n        return x\n    stop(x)\n    return x\n",
    )
    repo2, _ = _plant(tmp_path / "second", core_after_ck=guarded)
    _land(repo2)
    assert ck_drill.drill(repo2, DECLINE, lane_globs=LANE_GLOBS).problems == []


def test_switch_isolation_checks_product_call_sites_not_test_files(tmp_path: Path) -> None:
    """L.P0-0d.29: a CK-changed test file a later merge edits is left in place, and its changed
    calls need not read the switch (REG judges its nodes); an un-gated product call site in the
    same history is still flagged."""
    ungated = CK_CORE.replace("    if SWITCH:\n        stop(x)\n", "    stop(x)\n")
    repo, _ = _plant(tmp_path, core_after_ck=ungated)
    _git(repo, "checkout", "-q", "wr/x/ck-9")
    _write(repo, "tests/core/test_ck9.py", "def test_ck9():\n    assert len([1]) == 1\n")
    _commit(repo, "L.CK-9.2: the CK leaf's test calls something")
    _git(repo, "checkout", "-q", "master")
    _land(repo)
    _git(repo, "checkout", "-q", "-b", "wr/x/marker")
    test_file = repo / "tests/core/test_ck9.py"
    test_file.write_text("import pytest\n\n\n@pytest.mark.slow\n" + test_file.read_text())
    _commit(repo, "L.MARK.1: a later merge adds a marker to the CK's test file")
    _git(repo, "checkout", "-q", "master")
    _merge_branch(repo, "wr/x/marker", "WR-Merge: MARK")

    report = ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS)
    assert "tests/core/test_ck9.py" in report.patch.left
    isolation = [p for p in report.problems if "does not read SWITCH" in p]
    assert isolation and all(p.startswith("pkg/core.py:") for p in isolation), isolation
    assert ck_drill.is_test_path("tests/core/test_ck9.py")
    assert ck_drill.is_test_path("packages/trestle-packs/tests/test_x.py")
    assert not ck_drill.is_test_path("trestle/plugin/_codec.py")


UNGATED_CORE = CK_CORE.replace("    if SWITCH:\n        stop(x)\n", "    stop(x)\n")
LATER_HEADER = "# a later merge's header line\n"


def _land_later(repo: Path, edit: Callable[[str], str]) -> None:
    """Land CK-9, then a later merge whose only change is `edit` applied to pkg/core.py."""
    _merge_branch(repo, "wr/x/ck-9", "WR-Merge: CK-9")
    _git(repo, "checkout", "-q", "-b", "wr/x/later")
    _write(repo, "pkg/core.py", edit((repo / "pkg/core.py").read_text()))
    _commit(repo, "L.LATER.1: a later merge edits a file of the CK's landing diff")
    _git(repo, "checkout", "-q", "master")
    _merge_branch(repo, "wr/x/later", "WR-Merge: LATER")


def test_step3_reverse_applies_an_ungated_hunk_a_later_merge_left_intact(tmp_path: Path) -> None:
    """CM-7 step 3 as amended (L.CS-1.4.fix1): in a product file of the lane a later merge
    changed elsewhere, M's hunk with an un-gated changed call site is reverse-applied at HEAD, so
    the declined tree holds M's predecessor text there and (b) does not judge it; M's other hunks
    and the later merge's change stay, and the derived set includes the file."""
    repo, _ = _plant(tmp_path, core_after_ck=UNGATED_CORE)
    _land_later(repo, lambda text: LATER_HEADER + text)
    report = ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS)
    assert report.problems == [], report.problems
    patch = report.patch
    assert "pkg/core.py" in patch.left and patch.hunks_reverted["pkg/core.py"]
    declined = patch.edits["pkg/core.py"]
    assert declined is not None
    assert "def run(x):\n    return stop(x)\n" in declined  # M's predecessor text again
    assert declined.startswith(LATER_HEADER)  # the later merge's change stays
    assert "SWITCH: bool = False" in declined  # the gated switch hunk stays, flipped by step 1
    assert "pkg/core.py" in patch.scope and report.touched == patch.scope


def test_step3_leaves_a_hunk_a_later_merge_rewrote_and_b_still_judges_it(tmp_path: Path) -> None:
    """A hunk whose lines (or their one line of context) a later merge changed no longer applies:
    it stays in place and (b) still reports its un-gated call; nothing is fuzzed."""
    repo, _ = _plant(tmp_path, core_after_ck=UNGATED_CORE)
    _land_later(repo, lambda text: text.replace("    stop(x)\n", "    stop(x)  # later\n"))
    report = ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS)
    assert "pkg/core.py" not in report.patch.hunks_reverted
    assert any(
        p.startswith("pkg/core.py:") and "does not read SWITCH" in p for p in report.problems
    )


def test_step3_never_reverts_a_gated_hunk_or_a_test_file(tmp_path: Path) -> None:
    """Only hunks with an un-gated changed call site are reverse-applied: a hunk whose calls read
    the switch is carried by the switch (step 3), and a test file is judged by REG, never here."""
    repo, _ = _plant(tmp_path)
    _land_later(repo, lambda text: LATER_HEADER + text)
    report = ck_drill.drill(repo, DECLINE, lane_globs=LANE_GLOBS)
    assert report.problems == [] and report.patch.hunks_reverted == {}
    assert report.patch.edits["pkg/core.py"] == LATER_HEADER + CK_CORE.replace(
        "SWITCH: bool = True", "SWITCH: bool = False"
    )


def test_patch_touching_a_file_outside_the_derived_set_fails(tmp_path: Path) -> None:
    repo, _ = _plant(tmp_path)
    _land(repo)
    patch = ck_drill.derive_patch(repo, DECLINE, LANE_GLOBS, ck_drill.span_for(repo, "CK-9"))
    with ck_drill.scratch_worktree(repo) as scratch:
        ck_drill.apply_patch(scratch, patch)
        (scratch / "other.py").write_text("x = 2\n")  # a file the derived set does not hold
        with pytest.raises(ck_drill.DrillFailure, match="outside the derived set"):
            ck_drill.assert_within_scope(ck_drill.touched_paths(scratch), patch.scope)


def test_restored_register_entry_keeping_its_original_removed_by_fails(tmp_path: Path) -> None:
    repo, _ = _plant(tmp_path)
    _land(repo)
    patch = ck_drill.derive_patch(repo, DECLINE, LANE_GLOBS, ck_drill.span_for(repo, "CK-9"))
    with ck_drill.scratch_worktree(repo) as scratch:
        ck_drill.apply_patch(scratch, patch)
        assert ck_drill.declined_state_violations(scratch, DECLINE) == []
        # plant the wrong decline: the entry restored as it was, its original removed_by
        _write(scratch, "tests/proof/temporary.toml", ENTRY_T9 + "\n" + ENTRY_OTHER)
        problems = ck_drill.declined_state_violations(scratch, DECLINE)
        assert any("T-9" in p and "named-not-removed" in p for p in problems)


def test_switch_not_flipped_or_label_not_na_fails(tmp_path: Path) -> None:
    repo, _ = _plant(tmp_path)
    _land(repo)
    patch = ck_drill.derive_patch(repo, DECLINE, LANE_GLOBS, ck_drill.span_for(repo, "CK-9"))
    with ck_drill.scratch_worktree(repo) as scratch:
        ck_drill.apply_patch(scratch, patch)
        _write(scratch, "pkg/core.py", LATER_CORE)  # the switch is back at its recorded default
        _write(scratch, "tests/proof/labels.d/core.toml", LABELS)
        problems = ck_drill.declined_state_violations(scratch, DECLINE)
        assert any("SWITCH is True" in p for p in problems)
        assert any("WR-PROOF-10:K-9 is not na" in p for p in problems)


def _module(directory: Path, name: str, body: str) -> None:
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(body)


def test_declines_module_without_decline_or_listing_files_fails_at_import(tmp_path: Path) -> None:
    declines = tmp_path / "declines"
    good = (
        'DECLINE = {"merge": "CK-9", "k": "K-9",'
        ' "switch": {"module": "pkg.core", "name": "S", "declined": False}}\n'
    )
    _module(declines, "ck_9.py", good)
    assert set(ck_drill.assemble_declines(declines)) == {"CK-9"}

    _module(declines, "ck_9.py", "X = 1\n")  # no DECLINE
    with pytest.raises(ck_drill.DeclineError, match="no DECLINE"):
        ck_drill.assemble_declines(declines)

    listing = good.replace('"k": "K-9",', '"k": "K-9", "files": ["pkg/core.py"],')
    _module(declines, "ck_9.py", listing)  # a `files` key: an entry never lists files
    with pytest.raises(ck_drill.DeclineError, match="never lists files"):
        ck_drill.assemble_declines(declines)

    smuggled = good.replace('"k": "K-9",', '"k": "K-9", "labels": ["docs/agents.md"],')
    _module(declines, "ck_9.py", smuggled)  # a file smuggled in as a value
    with pytest.raises(ck_drill.DeclineError, match="lists a file"):
        ck_drill.assemble_declines(declines)

    _module(declines, "ck_9.py", good.replace("CK-9", "CK-8"))  # module stem is the merge's slug
    with pytest.raises(ck_drill.DeclineError, match="module stem"):
        ck_drill.assemble_declines(declines)


def test_real_declines_assemble_and_name_real_gates() -> None:
    cfg = fence_mod.load_fence()
    merges = {g.merge for g in cfg.gates}
    for merge_id, decline in ck_drill.DECLINES.items():
        assert decline["merge"] == merge_id and merge_id in merges
        assert ck_drill.lane_globs_for(merge_id, cfg)


def test_ck_isolation_job_is_required_and_precedes_ckpt() -> None:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["ck-isolation"]
    # a pull_request job that a push to master also runs, so a required job by CM-4
    assert "ck-isolation" in fence_mod.required_jobs_from_ci()
    assert "if" not in job
    assert True in ci or "on" in ci
    trigger = ci.get("on", ci.get(True))
    assert "pull_request" in trigger and "master" in trigger["push"]["branches"]
    steps = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "pytest tests/core/tooling/test_ck_drill.py -q" in steps
    assert "TRESTLE_PROOF_GATE" not in str(job)  # the job produces no proof result (CSC-13)
    assert "ck-isolation" in ci["jobs"]["ckpt"]["needs"]


_PRESENT = sorted(ck_drill.DECLINES)


@pytest.mark.parametrize("merge_id", _PRESENT, ids=[ck_drill.param_id(m) for m in _PRESENT])
def test_decline_patch_applies_and_isolates(merge_id: str, request: pytest.FixtureRequest) -> None:
    """CM-7 (a) and (b) for one CK merge's decline patch, computed at HEAD in a scratch worktree
    (the real history; REG runs nested, so this runs under `ck-isolation` or when named)."""
    if os.environ.get(ck_drill.NESTED_ENV):
        pytest.skip("inside the drill's own scratch run")
    named = any("test_decline_patch_applies_and_isolates" in str(a) for a in request.config.args)
    if not (named or os.environ.get(ck_drill.ISOLATION_ENV)):
        pytest.skip("runs in the ck-isolation job, or when named by node id")
    decline = ck_drill.DECLINES[merge_id]
    report = ck_drill.drill(
        ROOT,
        decline,
        lane_globs=ck_drill.lane_globs_for(merge_id),
        regression=ck_drill.run_regression,
    )
    assert report.problems == []
