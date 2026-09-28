"""`python -m tests.proof.differ` (CSC-5, MC-06): the divergence engine.

`L.P0-0c.4` builds the engine and mode `d1` (golden-S0, bidirectional,
in-process facet diff). `d2` (backward straddle, cross-worktree) is
`L.P0-0c.7`. Every other mode is registered here as "not built" and exits 2
naming its own future builder leaf, so an early caller of an unbuilt mode
fails loudly rather than silently no-op'ing.
"""

from __future__ import annotations

import argparse
import importlib
import json
import tomllib
from pathlib import Path

from tests.proof import normalize as normalize_mod

ROOT = Path(__file__).resolve().parents[2]
FACETS_PATH = ROOT / "tests" / "proof" / "facets.toml"
DIVERGENCE_PATH = ROOT / "tests" / "proof" / "divergence.toml"

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
        from tests.proof import d2_driver

        return d2_driver.cmd_d2(args)
    if args.mode in UNBUILT_MODES:
        print(f"differ {args.mode}: not built in P0; builder is {UNBUILT_MODES[args.mode]}")
        return 2
    parser.error(f"unknown mode {args.mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
