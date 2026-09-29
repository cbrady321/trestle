"""CS-2 fixture plugin: leaves a process of its own group running after it returns or raises. The
process carries this run's tmp path in its argv, so a process-table read attributes it to the run
(a daemon a plugin forgot, survey E4; WR-OWN-3). `wait_go` holds the plugin until `go` is
written, so a test can read the tree before the run ends."""

from __future__ import annotations

import subprocess
import sys
import time

from tests.proof.tolerances import POLL_FINE_S
from trestle.plugin.surface import Context, trestle

_HOLD = "import sys, time; time.sleep(float(sys.argv[1]))"


@trestle
def leaver(
    ctx: Context, seconds: float = 120.0, fail: bool = False, wait_go: bool = False
) -> dict[str, bool]:
    subprocess.Popen(
        [sys.executable, "-c", _HOLD, str(seconds), str(ctx.tmp)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (ctx.tmp / "ready").write_text("1", encoding="utf-8")
    while wait_go and not (ctx.tmp / "go").exists():  # let a test look at the tree first
        time.sleep(POLL_FINE_S)
    if fail:
        raise RuntimeError("the plugin failed and left a process behind")
    return {"done": True}
