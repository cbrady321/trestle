"""CS-2 fixture plugin: returns at once, but leaves a descendant holding the wrapper's console
pipes open, so a read of the console to end of file would never return."""

from __future__ import annotations

import os
import subprocess
import sys

from trestle.plugin.surface import Context, trestle

_HOLD = "import sys, time; time.sleep(float(sys.argv[1]))"


@trestle
def pipe_holder(ctx: Context, seconds: float = 120.0) -> dict[str, bool]:
    print("hello from the plugin", flush=True)
    # no redirect: the descendant inherits the plugin's stdout and stderr (the wrapper's pipes)
    subprocess.Popen([sys.executable, "-c", _HOLD, str(seconds), str(ctx.tmp)])
    # `ready` names this process (the wrapper's direct child): a reader that must know the plugin
    # has returned waits for this pid to be reaped, never for a fixed pause
    (ctx.tmp / "ready").write_text(str(os.getpid()), encoding="utf-8")
    return {"done": True}
