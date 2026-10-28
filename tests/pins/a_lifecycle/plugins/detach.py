"""Lane-A fixture plugin: a plugin that ignores cooperative cancel and
leaves a setsid grandchild behind (survey E4/E5; G-A1, G-A3).

Lives under `tests/pins/a_lifecycle/plugins/`, never `tests/fixtures/
plugins/` (the S0-enumerated shared directory), so S0's plugin catalog is
untouched.
"""

from __future__ import annotations

import subprocess
import sys
import time

from trestle.plugin.surface import Context, trestle

_HOLD = "import sys, time; time.sleep(float(sys.argv[1]))"


@trestle
def detach(ctx: Context, seconds: float = 120.0) -> dict[str, bool]:
    # The grandchild carries this run's tmp path in its argv, so a process
    # table read can attribute it to the run by that token alone.
    subprocess.Popen(
        [sys.executable, "-c", _HOLD, str(seconds), str(ctx.tmp)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (ctx.tmp / "ready").write_text("1", encoding="utf-8")
    # Deliberately never consults ctx.cancelled: cancel must be enforced from
    # outside the plugin.
    time.sleep(seconds)
    return {"done": True}
