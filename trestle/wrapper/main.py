"""Wrapper entry point — one quiet wrapper per run."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from trestle.common import credentials, redact
from trestle.wrapper.reactor import Stopped, install_stop_handler, run_reactor
from trestle.wrapper.spawn import spawn_child


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-wrapper")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    evidence = run_dir / "evidence"
    # change 3: the run's credential values, read from the conductor's pipe (never the
    # environment); the console scrub needs them, and the child gets them over a pipe of its own
    delivered = credentials.receive(os.environ)

    install_stop_handler()
    try:
        proc = spawn_child(run_dir, delivered)
        run_reactor(
            proc,
            console_dir=evidence / "console",
            report_path=evidence / "wrapper_report.json",
            scrubber=redact.Scrubber(
                secrets=credentials.scrub_set(delivered), roots=redact.run_roots(run_dir)
            ),
        )
    except Stopped:
        return 128 + signal.SIGTERM
    return 0


if __name__ == "__main__":
    sys.exit(main())
