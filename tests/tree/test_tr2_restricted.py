"""L.TR-2.4: the restricted profile scopes a child view to the session that admitted its root
(WR-AUTH-1, WR-UNIT-6).

In-library (the MCP two-session case is L.TR-L.9's, A2c2-4): no tree is admissible before TR-L
(MC-B3-03), and WR-AUTH-1 grants ownership only to the session that received the run id, so the
owning session is seeded on the run's `created` row and the run's lane is written through
`AttemptLane` (TR-2 preamble)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.trees import generators
from tests.proof import harness
from tests.tree import runs
from trestle.common import codes
from trestle.common.types import RequestOutcome, RunView
from trestle.server import answer
from trestle.server.main import Kernel

proves_owner = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:owner-reads-child-view", "A", "tree", "LOGIC", "BOTH"
)
proves_auth = pytest.mark.proves("WR-AUTH-1", "WR-AUTH-1:tree", "A", "tree", "LOGIC", "BOTH")

OWNER = "session-owner"
STRANGER = "session-stranger"
DB = ("data", "db")


def _kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, restricted: bool) -> Kernel:
    home = tmp_path / "home"
    home.mkdir()
    if restricted:
        (home / "config.toml").write_text(
            '[profile]\nmode = "restricted"\nallowlist = ["three_level"]\n', encoding="utf-8"
        )
    (tmp_path / "plugins").mkdir()
    monkeypatch.setenv("TRESTLE_HOME", str(home))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")  # a rewritten plugin is never stale
    kernel = harness.fresh_kernel([tmp_path / "plugins"], home=home)
    assert kernel.profile.restricted is restricted
    return kernel


def _owned_run(kernel: Kernel) -> tuple[runs.TreeRun, str]:
    run = runs.admit(kernel, generators.fixture_tree("three_level"), caller_session=OWNER)
    lane = runs.lane_of(run)
    runs.write_all_ends(run, lane)
    return run, answer.child_handle(run.run_id, DB)


@proves_owner
@proves_auth
def test_owner_session_reads_child_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = _kernel(tmp_path, monkeypatch, restricted=True)
    run, handle = _owned_run(kernel)
    project = kernel.control.project
    view = project.status(handle, caller_session=OWNER)
    assert isinstance(view, RunView)
    assert (view.root_run_id, view.path) == (run.run_id, "data/db")
    # every child of the run, through status and through await_runs alike
    for path in (p for p in run.paths() if p):
        each = project.status(answer.child_handle(run.run_id, path), caller_session=OWNER)
        assert isinstance(each, RunView) and each.path == "/".join(path)
    views = kernel.control.await_runs([handle], timeout_ms=0, caller_session=OWNER)
    assert isinstance(views, list) and [v.path for v in views] == ["data/db"]
    # a forged handle is unknown to its owner too
    forged = project.status(answer.child_handle(run.run_id, ("data", "ghost")), OWNER)
    assert isinstance(forged, RequestOutcome) and forged.code == codes.INVALID_HANDLE


@proves_owner
@proves_auth
def test_other_session_refused_child_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = _kernel(tmp_path, monkeypatch, restricted=True)
    run, handle = _owned_run(kernel)
    project = kernel.control.project
    refused = project.status(handle, caller_session=STRANGER)
    assert isinstance(refused, RequestOutcome)
    # the existing scoped refusal (the one `cancel` gives a run this session did not receive)
    assert (refused.code, refused.retryable, refused.origin) == (
        codes.NOT_OWNER,
        False,
        "projection",
    )
    assert run.run_id in refused.message
    # the refusal is the same for every vertex and does not say whether the vertex exists
    ghost = project.status(answer.child_handle(run.run_id, ("data", "ghost")), STRANGER)
    assert isinstance(ghost, RequestOutcome) and ghost.code == codes.NOT_OWNER
    # through await_runs: the whole batch is refused
    batch = kernel.control.await_runs([handle], timeout_ms=0, caller_session=STRANGER)
    assert isinstance(batch, RequestOutcome) and batch.code == codes.NOT_OWNER
    # a caller outside an MCP session (the operator's CLI or console) is not scoped
    assert isinstance(project.status(handle), RunView)
    # a root view is not scoped: unchanged from before this leaf
    root = project.status(run.run_id, caller_session=STRANGER)
    assert isinstance(root, RunView) and root.run_id == run.run_id
    assert "root_run_id" not in root.to_dict()


def test_full_profile_reads_any_child_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = _kernel(tmp_path, monkeypatch, restricted=False)
    run, handle = _owned_run(kernel)
    view = kernel.control.project.status(handle, caller_session=STRANGER)
    assert isinstance(view, RunView) and view.root_run_id == run.run_id
