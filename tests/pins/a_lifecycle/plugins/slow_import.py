"""Lane-A fixture plugin: a module-level probe delay (G-A4).

Importing this module (as admission's and the registry's validation
subprocesses do) sleeps for `TRESTLE_PROBE_DELAY_S`, logging the wall-clock
start and end of every probe to the file named by `TRESTLE_PROBE_LOG`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from trestle.plugin.surface import Context, trestle


def _mark(tag: str) -> None:
    log = os.environ.get("TRESTLE_PROBE_LOG")
    if log:
        with Path(log).open("a", encoding="utf-8") as fh:
            fh.write(f"{tag} {time.time()!r}\n")


_mark("start")
time.sleep(float(os.environ.get("TRESTLE_PROBE_DELAY_S", "0")))
_mark("end")


@trestle
def slow_import(ctx: Context) -> dict[str, bool]:
    return {"ok": True}
