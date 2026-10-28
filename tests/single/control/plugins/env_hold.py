"""L.SL-8.2 fixture plugin: a run on the environment named by `env` (WR-OWN-8). It writes `ready`
into its tmp directory and holds until `go` is written there (or `seconds` pass), so a test can
read who is running before letting the run end. `leave` also leaves a process of the run's own
group behind, carrying the run's tmp path in its argv (so a stop that is not confirmed leaves
something a test must reap)."""

from __future__ import annotations

import subprocess
import sys
import time

from tests.proof.tolerances import POLL_FINE_S
from trestle.plugin.surface import Context, trestle

_HOLD = "import sys, time; time.sleep(float(sys.argv[1]))"


@trestle(env_arg="env")
def env_hold(
    ctx: Context, env: str = "dev", seconds: float = 120.0, leave: bool = False
) -> dict[str, str]:
    if leave:
        subprocess.Popen(
            [sys.executable, "-c", _HOLD, str(seconds), str(ctx.tmp)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    (ctx.tmp / "ready").write_text("1", encoding="utf-8")
    end = time.monotonic() + seconds
    while time.monotonic() < end and not (ctx.tmp / "go").exists():
        time.sleep(POLL_FINE_S)
    return {"env": env}
