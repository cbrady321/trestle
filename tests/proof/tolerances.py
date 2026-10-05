"""Tolerance bounds resolution (MC-09) (L.P0-0a.4).

Every timing bound the proof court needs is read through this module under
its MC-09 published name, resolved at call time: `trestle.common.clock`
first (once CS-2 publishes it), else the named S0 constant it corresponds
to, else `ToleranceUnpublished`. **No later leaf ever edits this file**
(DM-60): a bound becomes readable purely by CS-2 defining it on
`trestle.common.clock`, never by a change here.

`S0_LITERAL_SITES` is computed by an AST walk over `trestle/**`, never
hard-coded, so the CLI empties it once CS-2 moves the two remaining
`grace_s=`/`kill_s=` numeric literals (`trestle/server/conductor.py`
L62/L65) into a published bound.
"""

from __future__ import annotations

import argparse
import ast
import importlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# The MC-09 published bound names and the S0 constant each falls back to
# (module, attribute) when `trestle.common.clock` does not yet define them.
# A bound with no S0 fallback (added post-S0) is `(None, None)`.
_S0_FALLBACKS: dict[str, tuple[str | None, str | None]] = {
    "grace": ("trestle.server.runs", "CANCEL_GRACE_S"),
    "kill": ("trestle.server.runs", "CANCEL_KILL_S"),
    "release_slice": (None, None),
    "finalization_margin": (None, None),
    "deadline_ceiling": (None, None),
    "sweep_parallelism": (None, None),
    "stop_bound": (None, None),
    "APPEND_COST_RATIO": (None, None),
    "FINALIZATION_RESERVE_S": (None, None),
}


# Harness patience: how long a proof test waits on a process, a join or a
# settle. These are proof-court scaffolding, not MC-09 product bounds, so
# they have no `trestle.common.clock` counterpart and are plain constants
# (SA-05 keeps every literal out of the proof tests themselves).
HARNESS_WAIT_MS = 5000  # run_to_dir wait for a run to reach a terminal state
PROC_WAIT_S = 5.0  # Popen.wait after a kill
JOIN_WAIT_S = 10.0  # thread/host join
SETTLE_LONG_S = 1.0  # let a forked tree finish reparenting
STABILITY_GAP_S = 0.5  # gap between two reads that must agree
SETTLE_S = 0.3  # let a just-spawned process appear in ps
SETTLE_SHORT_S = 0.2
POLL_S = 0.1  # poll interval
POLL_FINE_S = 0.05


class ToleranceUnpublished(RuntimeError):
    """Raised on reading a tolerance that is neither published on
    `trestle.common.clock` nor available as an S0 fallback constant."""


def _clock_module() -> Any:
    try:
        return importlib.import_module("trestle.common.clock")
    except ImportError:
        return None


def _resolve(name: str) -> float:
    clock = _clock_module()
    if clock is not None and hasattr(clock, name):
        return float(getattr(clock, name))
    s0_module, s0_attr = _S0_FALLBACKS.get(name, (None, None))
    if s0_module is not None:
        mod = importlib.import_module(s0_module)
        if hasattr(mod, s0_attr):
            return float(getattr(mod, s0_attr))
    raise ToleranceUnpublished(name)


def grace() -> float:
    return _resolve("grace")


def kill() -> float:
    return _resolve("kill")


def release_slice() -> float:
    return _resolve("release_slice")


def finalization_margin() -> float:
    return _resolve("finalization_margin")


def deadline_ceiling() -> float:
    return _resolve("deadline_ceiling")


def sweep_parallelism() -> float:
    return _resolve("sweep_parallelism")


def stop_bound() -> float:
    return _resolve("stop_bound")


def append_cost_ratio() -> float:
    return _resolve("APPEND_COST_RATIO")


def finalization_reserve_s() -> float:
    return _resolve("FINALIZATION_RESERVE_S")


_LITERAL_KEYWORDS = ("grace_s", "kill_s")


def find_s0_literal_sites(root: Path = ROOT) -> list[tuple[str, int]]:
    """AST-scan `trestle/**` for a call passing a numeric literal to a
    `grace_s=`/`kill_s=` keyword argument: the still-unpublished stop-bound
    literals CS-2 will move onto `trestle.common.clock`."""
    sites: list[tuple[str, int]] = []
    for path in sorted((root / "trestle").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg in _LITERAL_KEYWORDS and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, (int, float)) and not isinstance(
                        kw.value.value, bool
                    ):
                        sites.append((str(path.relative_to(root)), node.lineno))
    return sorted(set(sites))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.tolerances")
    parser.add_argument("--list-s0-literal-sites", action="store_true", dest="list_sites")
    args = parser.parse_args(argv)

    if args.list_sites:
        sites = find_s0_literal_sites()
        for file, lineno in sites:
            print(f"{file}:{lineno}")
        return 0 if sites else 1

    parser.error("no action given")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
