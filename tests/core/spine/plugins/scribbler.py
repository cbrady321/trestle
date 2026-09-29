"""CS-2 fixture plugin: leaves two writers behind, one in its own group and one in its own
session, that keep writing into the run's evidence directory and rewriting its `result.json`.
A survivor of a stop that wrote after the terminal row is what WR-CANCEL-2 forbids (survey I1).
The plugin itself ignores `ctx.cancelled`."""

from __future__ import annotations

import subprocess
import sys
import time

from tests.proof.tolerances import POLL_FINE_S
from trestle.plugin.surface import Context, trestle

_WRITER = """
import pathlib, sys, time
evidence, name, poll = pathlib.Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
n = 0
while True:
    n += 1
    with open(evidence / f"late_{name}.log", "a", encoding="utf-8") as fh:
        fh.write(f"{n}\\n")
    (evidence / "result.json").write_text(f'{{"writer": "{name}", "n": {n}}}', encoding="utf-8")
    time.sleep(poll)
"""


def _spawn(evidence: str, name: str, *, new_session: bool) -> None:
    subprocess.Popen(
        [sys.executable, "-c", _WRITER, evidence, name, str(POLL_FINE_S)],
        start_new_session=new_session,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@trestle
def scribbler(ctx: Context, seconds: float = 120.0) -> dict[str, bool]:
    evidence = str(ctx.tmp.parent.parent / "evidence")
    _spawn(evidence, "group", new_session=False)
    _spawn(evidence, "session", new_session=True)
    (ctx.tmp / "ready").write_text(str(ctx.tmp), encoding="utf-8")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(POLL_FINE_S)
    return {"done": True}
