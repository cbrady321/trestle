"""The one-vertex spine fixture (L.SV-5.9; TM-B2-3 `one-vertex-spine-fixture`): a published
workflow plugin whose declared tree is one leaf, driven through the fakes of `trestle_packs.fakes`.

`mode` picks what the fake marker does: `advance` creates it and it turns ready after two polls,
`skip` plants an instance that is already ready (found), `hang` never turns ready (a cancel or the
deadline ends the wait). `env` is the environment argument (`env_arg`, WR-OWN-8)."""

from __future__ import annotations

from datetime import timedelta

from trestle_packs.fakes import FakeMarker

from tests.fixtures.workflows.spine_support import UNIT, SpineLeaf, drive
from trestle.plugin import Context, trestle
from trestle.workflow import WorkflowEntry

ENTRY = WorkflowEntry(
    root=UNIT,
    units={UNIT: SpineLeaf()},
    deadline=timedelta(seconds=120),
)


@trestle(deadline=120, env_arg="env")
def spine_leaf(ctx: Context, env: str = "dev", mode: str = "advance") -> dict[str, str]:
    lag = 0 if mode == "skip" else 2
    marker = FakeMarker(ctx.tmp / "markers", "run", lag_polls=lag, never_ready=mode == "hang")
    if mode == "skip":
        marker.plant_found("marker")
    drive(ctx, ENTRY, {"env": env, "mode": mode}, marker=marker)
    return {"env": env, "mode": mode}
