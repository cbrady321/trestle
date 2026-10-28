"""A serving kernel that starts one slow run with a callback and then waits to be killed (run
callbacks test plan 6): argv is the home, the plugin folder and the callback URL. Prints the run id
once the run is running."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from trestle.common.types import RunView
from trestle.server.main import create_kernel

NO_WAIT = 0  # wait_ms=0: the first frame at once
POLL_S = 0.05
IDLE_S = 60.0  # until the test kills this process


def main() -> None:
    home, plugins, url = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    kernel = create_kernel(home=home, plugin_dirs=[plugins], skip_recovery=True)
    kernel.start_service()
    view = kernel.control.run(plugin="slow", args={"seconds": 120.0}, wait_ms=NO_WAIT, callback=url)
    assert isinstance(view, RunView), view
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = kernel.control.project.status(view.run_id)
        if isinstance(status, RunView) and status.state == "running":
            break
        time.sleep(POLL_S)
    print(view.run_id, flush=True)
    while True:
        time.sleep(IDLE_S)


if __name__ == "__main__":
    main()
