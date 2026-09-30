"""Wrapper entry point — one quiet wrapper per run."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from trestle.wrapper.reactor import Stopped, install_stop_handler, run_reactor
from trestle.wrapper.spawn import spawn_child


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-wrapper")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    evidence = run_dir / "evidence"

    install_stop_handler()
    try:
        proc = spawn_child(run_dir)
        run_reactor(
            proc,
            console_dir=evidence / "console",
            report_path=evidence / "wrapper_report.json",
        )
    except Stopped:
        return 128 + signal.SIGTERM
    return 0


if __name__ == "__main__":
    sys.exit(main())
