"""CK-8 fixture plugin: a plugin that succeeds and leaves two daemons that keep writing into the
run's evidence directory, one in its own process group and one in a session of its own
(survey E4; WR-OWN-3, WR-CANCEL-2). Both carry this run's tmp path in their argv, so a
process-table read attributes them to the run.

The plugin returns once both have written and `go` exists in its tmp dir, so a test can see both
in the run's identity rows first, and the terminal answer comes while they are alive and writing."""

from __future__ import annotations

import subprocess
import sys
import time

from tests.proof.tolerances import POLL_FINE_S
from trestle.plugin.surface import Context, trestle

_DAEMON = """
import pathlib, sys, time
seconds, target = float(sys.argv[1]), pathlib.Path(sys.argv[2])
end = time.monotonic() + seconds
while time.monotonic() < end:
    with target.open("a", encoding="utf-8") as fh:
        fh.write("x")
    time.sleep(float(sys.argv[3]))
"""


@trestle
def daemons(ctx: Context, seconds: float = 120.0) -> dict[str, bool]:
    evidence = ctx.tmp.parent.parent / "evidence"
    for name, new_session in (("in_group", False), ("setsid", True)):
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _DAEMON,
                str(seconds),
                str(evidence / f"daemon_{name}.log"),
                str(POLL_FINE_S),
                str(ctx.tmp),
            ],
            start_new_session=new_session,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    while not all((evidence / f"daemon_{n}.log").exists() for n in ("in_group", "setsid")):
        time.sleep(POLL_FINE_S)
    (ctx.tmp / "ready").write_text("1", encoding="utf-8")
    while not (ctx.tmp / "go").exists():  # let a test read the tree first
        time.sleep(POLL_FINE_S)
    return {"done": True}
