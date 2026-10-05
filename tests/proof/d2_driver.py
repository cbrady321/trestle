"""Subprocess entry point for `differ d2` (L.P0-0c.7, SA-15).

Runs *inside* the reader worktree's own interpreter invocation (the caller
prepends that worktree, and its `packages/trestle-packs`, to `sys.path`
before spawning this module — never installed). Imports only the frozen S0
product surfaces named by this leaf (`trestle.server.ledger`,
`trestle.server.recovery`, `trestle.server.project`, `trestle.server.main`)
so a drift-checking AST test (`test_sa15_d2_surfaces`) can prove this
module never reaches into `tests.proof.*` helpers that may not exist, or
may differ, in an older reader.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.recovery import recover_run_dir


def _assert_reader_origin(reader_root: Path) -> None:
    import trestle

    trestle_file = Path(trestle.__file__).resolve()
    reader_root = reader_root.resolve()
    if reader_root not in trestle_file.parents and trestle_file != reader_root:
        raise AssertionError(
            f"trestle imported from {trestle_file}, not under reader root {reader_root}"
        )


def _project_one(home: Path) -> dict[str, object]:
    """Reads `home`'s run through recovery + the ledger, never mutating the
    committed fossil itself: `home` is copied into a scratch directory
    first (recovery writes into the run directory it is given, e.g. marking
    it `recovered`, and the checked-in fossil corpus must stay byte-stable
    across every `d2` run)."""
    run_dirs = sorted((home / "runs").rglob("r_*"))
    if not run_dirs:
        return {"error": "no run directory under this fossil home"}

    with tempfile.TemporaryDirectory(prefix="d2-fossil-scratch-") as scratch:
        scratch_home = Path(scratch) / "home"
        shutil.copytree(home, scratch_home)
        rel = run_dirs[0].relative_to(home)
        run_dir = scratch_home / rel
        recover_run_dir(run_dir)
        ledger = RunLedger.open(ledger_path(run_dir))
        return {
            "projected_state": ledger.projected_state(),
            "kinds": [r.get("kind") for r in ledger.records],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="d2_driver")
    parser.add_argument("--reader-root", required=True)
    parser.add_argument("--states-json", required=True)
    args = parser.parse_args(argv)

    _assert_reader_origin(Path(args.reader_root))

    states = json.loads(Path(args.states_json).read_text())
    report = {state_id: _project_one(Path(home)) for state_id, home in states.items()}
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
