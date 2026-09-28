"""`python -m tests.proof.differ` (CSC-5, MC-06): the divergence engine.

`L.P0-0c.4` builds the engine and mode `d1` (golden-S0, bidirectional,
in-process facet diff). `d2` (backward straddle, cross-worktree) is
`L.P0-0c.7`. Every other mode is registered here as "not built" and exits 2
naming its own future builder leaf, so an early caller of an unbuilt mode
fails loudly rather than silently no-op'ing.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from tests.proof import normalize as normalize_mod

ROOT = Path(__file__).resolve().parents[2]
FACETS_PATH = ROOT / "tests" / "proof" / "facets.toml"
DIVERGENCE_PATH = ROOT / "tests" / "proof" / "divergence.toml"
D2_EXCEPTIONS_PATH = ROOT / "tests" / "proof" / "d2_exceptions.toml"
D2_DRIVER_PATH = ROOT / "tests" / "proof" / "d2_driver.py"
FOSSILS_ROOT_DEFAULT = ROOT / "tests" / "fixtures" / "fossils"

# CM-9's exact schema: [[exception]] {id, reader, states, declared_by, note?}.
# `retires_at`, `scope`, `register_id` are withdrawn keys (CM-12) and a load
# error if present.
CM9_REQUIRED_KEYS = {"id", "reader", "states", "declared_by"}
CM9_OPTIONAL_KEYS = {"note"}
CM9_ALL_KEYS = CM9_REQUIRED_KEYS | CM9_OPTIONAL_KEYS

# Modes this delivery (P0) does not build. Each names its own CSC-5 builder
# leaf (plan-workflow-runtime.md's differ mode table); `d1`'s closure mode
# is `L.CZ.4`, listed for completeness though it never appears as a CLI
# argument today (there is no `differ.py cz4` subcommand to close).
UNBUILT_MODES = {
    "d3": "L.CS-2.4",
    "d4": "L.SV-3.4",
    "d5": "L.SV-4.2",
    "d6": "L.TR-6.1",
    "d7": "L.TR-4.7",
    "d8": "L.SL-3.3",
}


def load_facets() -> list[dict[str, object]]:
    return list(tomllib.loads(FACETS_PATH.read_text()).get("facet", []))


def load_divergence() -> list[dict[str, object]]:
    if not DIVERGENCE_PATH.exists():
        return []
    return list(tomllib.loads(DIVERGENCE_PATH.read_text()).get("entry", []))


def _resolve_extractor(spec: str):
    module_name, func_name = spec.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _due(entry: dict[str, object]) -> bool:
    """An entry is "due" once its `due_checkpoint` is an ancestor of HEAD.
    P0 has no checkpoint history yet (J0 has not happened): every P0-court
    entry is treated as due immediately (checkpoint "P0" or unset), which is
    the conservative reading — a missing diff for a P0-due entry fails now
    rather than silently later."""
    due_checkpoint = entry.get("due_checkpoint")
    return due_checkpoint in (None, "", "P0", "s0")


def cmd_d1(args: argparse.Namespace) -> int:
    facets = load_facets()
    if args.facet:
        wanted = set(args.facet)
        facets = [f for f in facets if f["id"] in wanted]

    ok = True
    diffed_facets: set[str] = set()
    for facet in facets:
        fid = facet["id"]
        extractor = facet.get("extractor", "pending")
        if extractor == "pending":
            if args.strict:
                print(f"d1: STRICT: facet {fid!r} has no extractor yet (pending)")
                ok = False
            continue

        diffed_facets.add(fid)
        func = _resolve_extractor(str(extractor))
        current = normalize_mod.normalize(func())
        golden_path = ROOT / str(facet["golden"])
        if not golden_path.exists():
            print(f"d1: UNEXPECTED: facet {fid!r} has no golden file {golden_path}")
            ok = False
            continue
        golden = normalize_mod.normalize(json.loads(golden_path.read_text()))
        missing, unexpected = normalize_mod.structural_diff(
            golden, current, policy=str(facet.get("additive", "named"))
        )
        if missing or unexpected:
            print(f"d1: UNEXPECTED DIFF: facet {fid!r}: missing={missing} unexpected={unexpected}")
            ok = False

    for entry in load_divergence():
        if entry.get("facet") not in {f["id"] for f in facets}:
            continue
        if _due(entry) and entry.get("facet") not in diffed_facets:
            print(
                f"d1: MISSING DUE DIFF: divergence entry {entry.get('id')} ({entry.get('facet')})"
            )
            ok = False

    return 0 if ok else 1


def _load_exceptions(path: Path) -> list[dict[str, object]]:
    """CM-9 named exceptions (L.P0-0c.7). Not a register entry (CM-7):
    nothing here ever retires, and the withdrawn keys (CM-12) are a load
    error, never silently ignored."""
    if not path.exists():
        return []
    exceptions = list(tomllib.loads(path.read_text()).get("exception", []))
    for exc in exceptions:
        extra = set(exc) - CM9_ALL_KEYS
        missing = CM9_REQUIRED_KEYS - set(exc)
        if extra or missing:
            raise ValueError(
                f"d2_exceptions.toml: {exc.get('id')}: bad schema "
                f"(extra={extra}, missing={missing})"
            )
    return exceptions


def _excused(exceptions: list[dict[str, object]], reader: str, state_path: str) -> bool:
    """An exception excuses a divergence only in its `states` globs and only
    when `differ d2` runs with exactly its `reader` (CM-9)."""
    for exc in exceptions:
        if exc.get("reader") != reader:
            continue
        for pattern in exc.get("states", []):
            if fnmatch.fnmatch(state_path, str(pattern)):
                return True
    return False


def cmd_d2(args: argparse.Namespace) -> int:
    """`python -m tests.proof.differ d2 --reader <git ref|s0> [--fossils <dir>]`
    (L.P0-0c.7): backward straddle — an older reader (any git ref; `s0` is
    an alias for `5fbdd2f`) reads today's committed fossil corpus through
    `d2_driver.py`, run in a subprocess with that reader worktree (and its
    `packages/trestle-packs`) first on `PYTHONPATH`, never installed."""
    from tests.proof import fossils as fossils_mod

    reader = args.reader
    ref = "5fbdd2f" if reader == "s0" else reader
    fossils_root = Path(args.fossils) if args.fossils else FOSSILS_ROOT_DEFAULT

    states = fossils_mod.load_states(fossils_root)
    to_check: dict[str, str] = {}
    for state_id, (band, entry) in states.items():
        if entry.get("producer") in (None, "pending") or entry.get("absent"):
            continue
        to_check[state_id] = str(fossils_root / band / state_id / "home")

    if not to_check:
        print("d2: no fossil states with a real producer to check")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "reader"
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), ref],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            print(f"d2: git worktree add failed for ref {ref!r}: {add.stderr}")
            return 1
        try:
            states_json = Path(tmp) / "states.json"
            states_json.write_text(json.dumps(to_check))
            env = dict(os.environ)
            env["PYTHONPATH"] = f"{worktree}{os.pathsep}{worktree / 'packages' / 'trestle-packs'}"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(D2_DRIVER_PATH),
                    "--reader-root",
                    str(worktree),
                    "--states-json",
                    str(states_json),
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 0:
                print(f"d2: driver failed (reader={reader}): {proc.stderr}")
                return 1
            report_lines = [line for line in proc.stdout.splitlines() if line.strip()]
            report = json.loads(report_lines[-1]) if report_lines else {}
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=ROOT,
                capture_output=True,
            )

    exceptions = _load_exceptions(D2_EXCEPTIONS_PATH)
    ok = True
    for state_id, result in report.items():
        band, _entry = states[state_id]
        expected = state_id
        actual = result.get("projected_state")
        if actual != expected:
            state_path = f"{band}/{state_id}"
            if _excused(exceptions, reader, state_path):
                continue
            print(
                f"d2: UNEXPECTED: state {state_path!r} projected {actual!r}, "
                f"expected {expected!r} (reader={reader})"
            )
            ok = False

    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.differ")
    sub = parser.add_subparsers(dest="mode", required=True)

    d1 = sub.add_parser("d1")
    d1.add_argument("--strict", action="store_true")
    d1.add_argument("--facet", action="append", default=[])

    d2 = sub.add_parser("d2")
    d2.add_argument("--reader", required=True)
    d2.add_argument("--fossils", default=None)

    for mode in UNBUILT_MODES:
        sub.add_parser(mode)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "d1":
        return cmd_d1(args)
    if args.mode == "d2":
        return cmd_d2(args)
    if args.mode in UNBUILT_MODES:
        print(f"differ {args.mode}: not built in P0; builder is {UNBUILT_MODES[args.mode]}")
        return 2
    parser.error(f"unknown mode {args.mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
