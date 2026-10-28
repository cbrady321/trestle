"""CS-3 fixture plugin: ends its own process with a signal, as something outside Trestle would
(a session teardown that signals the server's process group)."""

from __future__ import annotations

import os
import signal

from trestle.plugin.surface import Context, trestle


@trestle
def self_killer(ctx: Context, signum: int = 15) -> dict[str, str]:
    os.kill(os.getpid(), signal.Signals(signum))
    return {"unreachable": "yes"}
