"""CS-2 fixture plugin: a run whose tree holds every kind of process containment must reach
(survey E4/E5). It ignores `ctx.cancelled`, so a stop has to come from outside the plugin.

- a grandchild in the run's own process group (the plugin's own group, not a new session);
- a child that called setsid, whose parent (this process) is alive: the group kill cannot reach
  it, only attribution by parent id can;
- a readiness-poll loop, the shape survey E5 found blocking a stop.

Every spawned process carries this run's tmp path in its argv, so a process-table read can tell
the run's processes from every other. `hold_term` makes the setsid child ignore SIGTERM and log
when one arrives, so a test can see SIGTERM come first and SIGKILL only after the grace.
"""

from __future__ import annotations

import subprocess
import sys
import time

from tests.proof.tolerances import POLL_FINE_S
from trestle.plugin.surface import Context, trestle

_HOLD = "import sys, time; time.sleep(float(sys.argv[1]))"
_HOLD_TERM = """
import signal, sys, time
seconds, log = float(sys.argv[1]), sys.argv[2]
def on_term(signum, frame):
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(repr(time.time()))
signal.signal(signal.SIGTERM, on_term)
end = time.monotonic() + seconds
while time.monotonic() < end:
    time.sleep(0.05)
"""


def _spawn(code: str, *args: str, new_session: bool) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", code, *args],
        start_new_session=new_session,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@trestle
def tree(ctx: Context, seconds: float = 120.0, hold_term: bool = False) -> dict[str, bool]:
    marker = str(ctx.tmp)
    _spawn(_HOLD, str(seconds), marker, new_session=False)  # same-group grandchild
    if hold_term:
        _spawn(_HOLD_TERM, str(seconds), str(ctx.tmp / "term_at"), marker, new_session=True)
    else:
        _spawn(_HOLD, str(seconds), marker, new_session=True)  # setsid child, parent alive
    (ctx.tmp / "ready").write_text("1", encoding="utf-8")
    end = time.monotonic() + seconds
    while time.monotonic() < end:  # a readiness-poll loop that never consults ctx.cancelled
        (ctx.tmp / "poll").exists()
        time.sleep(POLL_FINE_S)
    return {"done": True}
