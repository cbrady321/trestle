"""A run that spawns many short-lived subprocesses, `width` at a time (v0.4 step 2's small-ledger
test): like a pytest suite, each one is attributed to the run and gets an identity row."""

from __future__ import annotations

import subprocess

from trestle.plugin.surface import Context, trestle


@trestle
def spawner(
    ctx: Context, batches: int = 5, width: int = 5, seconds: float = 0.15
) -> dict[str, int]:
    spawned = 0
    for _ in range(batches):
        procs = [subprocess.Popen(["/bin/sleep", str(seconds)]) for _ in range(width)]
        for proc in procs:
            proc.wait()
        spawned += len(procs)
    return {"spawned": spawned}
