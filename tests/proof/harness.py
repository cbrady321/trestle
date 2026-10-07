"""Test harness over today's kernel (L.P0-0b.1; DM-07, DM-08).

Drives runs through the same seam an MCP client would (`ControlSurface`),
never around admission/conductor/project — a proof test that plants a
defect must see it the way a real client would. The one exception is
`run_tree` (L.SV-5.10, MC-26 full): it compiles a workflow fixture and admits
it through `write_admitted_run` (MC-B2-08), the post-refusal half of
admission, so it bypasses only the temporary multi-vertex refusal (DM-07,
`Admission.admit` is that refusal's one home); everything after admission is
the product's own conductor and child.
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from trestle.common.types import (
    AdmitRequest,
    AdmitResultRefused,
    RequestOutcome,
    RunView,
    WorkOrder,
)
from trestle.server.admission import plan_for_admission
from trestle.server.home import is_legacy
from trestle.server.init_cmd import upgrade_home
from trestle.server.ledger import run_dir_for
from trestle.server.main import Kernel, create_kernel
from trestle.server.plugin_schema import validate_args
from trestle.server.snapshots import (
    deadline_of,
    discover_plugin_name,
    load_snapshot_schema,
)

DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "plugins"


def fresh_kernel(
    plugin_dirs: list[Path] | None = None,
    *,
    home: Path | None = None,
) -> Kernel:
    """A kernel over a throwaway `TRESTLE_HOME`.

    Recovery is skipped: the directory this call creates has nothing to
    recover. Defaults to the shared fixture plugin directory
    (`tests/fixtures/plugins`) unless the caller names its own.
    """
    trestle_home = (
        home if home is not None else Path(tempfile.mkdtemp(prefix="trestle-proof-home-"))
    )
    trestle_home.mkdir(parents=True, exist_ok=True)
    if is_legacy(trestle_home):
        # a copied fossil home is a v0.3.0 home: the operator's `trestle init --upgrade` first
        upgrade_home(trestle_home)
    dirs = plugin_dirs if plugin_dirs is not None else [DEFAULT_PLUGIN_DIR]
    return create_kernel(home=trestle_home, plugin_dirs=dirs, skip_recovery=True)


@contextmanager
def patch_snapshot(kernel: Kernel, name: str, **fields: Any) -> Iterator[None]:
    """Replace fields on plugin `name`'s registered snapshot for the life
    of the context (generalizes the `_patch_slow_timeout` idiom in
    `tests/test_m3_durability.py`), restoring the original on exit."""
    snap = kernel.registry.get(name)
    if snap is None:
        raise KeyError(f"no snapshot registered for plugin {name!r}")
    original = kernel.registry.snapshots[name]
    kernel.registry.snapshots[name] = dataclasses.replace(snap, **fields)
    try:
        yield
    finally:
        kernel.registry.snapshots[name] = original


def run_to_dir(
    kernel: Kernel,
    plugin: str,
    args: dict[str, Any] | None = None,
    *,
    wait_ms: int = 5000,
) -> Path:
    """Run `plugin` over `kernel.control.run` and return its run directory.

    Raises if admission refuses the run; does not itself assert the run
    reached a terminal state (a caller with a slow plugin may want to poll
    further — `records.node_record` reads whatever the ledger holds so
    far).
    """
    result = kernel.control.run(plugin=plugin, args=args or {}, wait_ms=wait_ms)
    if isinstance(result, RequestOutcome):
        raise RuntimeError(f"run refused: {result.code} {result.message}")
    assert isinstance(result, RunView)
    return run_dir_for(kernel.home, result.run_id)


@dataclasses.dataclass(frozen=True)
class AdmittedTree:
    """A workflow run admitted by `admit_tree` and not yet driven: its kernel, its run directory
    and the work order the conductor takes. A caller that plants a defect in the run directory
    (a stale digest, a foreign lane entry) does it between `admit_tree` and `drive_tree`."""

    kernel: Kernel
    plugin: str
    run_id: str
    run_dir: Path
    order: WorkOrder


def admit_tree(
    fixture: Path,
    request: Mapping[str, Any] | None = None,
    *,
    kernel: Kernel | None = None,
    plugin_dirs: Sequence[Path] = (),
) -> AdmittedTree:
    """Compile the workflow plugin `fixture` (a plugin source file) and admit a run of it through
    `write_admitted_run` (MC-23, MC-B2-08), refusing nothing the temporary multi-vertex refusal
    (DM-07) would: a compiled multi-vertex plan is admitted here and run in-library.

    `request` is the run's arguments. The fixture's plugin directory is a throwaway copy, listed
    before `plugin_dirs` (a catalog-order knob for the A2.1 node); a `kernel` the caller built
    already knows the plugin and `fixture` only names it. Raises if the request is invalid or the
    plan is refused (a refusal is not a run)."""
    plugin = discover_plugin_name(fixture)
    if plugin is None:
        raise ValueError(f"{fixture} holds no single @trestle entry point")
    if kernel is None:
        plugin_dir = Path(tempfile.mkdtemp(prefix="trestle-proof-plugins-"))
        shutil.copy(fixture, plugin_dir / fixture.name)
        kernel = fresh_kernel([plugin_dir, *plugin_dirs])
    kernel.registry.maybe_refresh()
    snap = kernel.registry.get(plugin)
    if snap is None:
        raise KeyError(f"plugin {plugin!r} is not published by {fixture}")
    req = AdmitRequest(plugin=plugin, args=dict(request or {}))
    validate_args(req.args, load_snapshot_schema(snap))
    deadline_s, _ = deadline_of(snap)
    planned = plan_for_admission(snap, req, deadline_s)
    if isinstance(planned, AdmitResultRefused):
        outcome = planned.outcome
        raise RuntimeError(f"plan refused: {outcome.code} {outcome.message}")
    admission = kernel.control.admission
    admitted = admission.write_run(snap, req, planned)
    admission.scheduler.mint(admitted.run_id, snap.snapshot_id, admitted.spec_hash)
    order = WorkOrder(
        run_id=admitted.run_id,
        snapshot_id=snap.snapshot_id,
        spec_hash=admitted.spec_hash,
        secrets=admitted.secrets,
    )
    return AdmittedTree(
        kernel, plugin, admitted.run_id, run_dir_for(kernel.home, admitted.run_id), order
    )


def drive_tree(admitted: AdmittedTree) -> RunView:
    """Hand an admitted run to the conductor and wait for its terminal answer (the finalized
    terminal row, never a running frame)."""
    control = admitted.kernel.control
    control._drive_background(admitted.order)  # the dispatcher: the run starts as in `run`
    view = control.project.await_terminal(admitted.run_id)
    if isinstance(view, RequestOutcome):
        raise RuntimeError(f"run did not finish: {view.code} {view.message}")
    return view


def run_tree(
    fixture: Path,
    request: Mapping[str, Any] | None = None,
    *,
    kernel: Kernel | None = None,
    plugin_dirs: Sequence[Path] = (),
) -> Path:
    """MC-26 full: `admit_tree`, then run to terminal; returns the run directory."""
    admitted = admit_tree(fixture, request, kernel=kernel, plugin_dirs=plugin_dirs)
    drive_tree(admitted)
    return admitted.run_dir
