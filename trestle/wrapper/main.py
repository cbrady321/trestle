"""Wrapper entry point — one quiet wrapper per run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trestle.wrapper.reactor import run_reactor
from trestle.wrapper.spawn import spawn_child


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-wrapper")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    evidence = run_dir / "evidence"
    spec = json.loads((evidence / "spec.json").read_text(encoding="utf-8"))
    timeout_s = int(spec.get("timeout_s", 300))

    proc = spawn_child(run_dir)
    run_reactor(
        proc,
        console_dir=evidence / "console",
        report_path=evidence / "wrapper_report.json",
        timeout_s=timeout_s,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
