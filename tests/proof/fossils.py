"""`python -m tests.proof.fossils generate` (CSC-5, MC-11; L.P0-0c.6).

Reads every band's `tests/fixtures/fossils/*/MANIFEST.toml` (P0's own
`s0/`; a later checkpoint's own band directory, e.g. `single/`,
`tree-trl/`, `b/`) and runs the selected states' producers
("module:function") into `tests/fixtures/fossils/<band>/<state>/home/
{runs,snapshots,idempotency.json}`. No band edits this module: adding a
checkpoint means adding its own `<band>/MANIFEST.toml`, never touching
`fossils.py`.
"""

from __future__ import annotations

import argparse
import importlib
import tomllib
from pathlib import Path

from tests.proof import harness

ROOT = Path(__file__).resolve().parents[2]
FOSSILS_ROOT = ROOT / "tests" / "fixtures" / "fossils"
FIXTURE_PLUGIN_DIR = Path(__file__).resolve().parent / "fixtures" / "plugins"


def _manifests(fossils_root: Path) -> dict[str, Path]:
    """band name -> its MANIFEST.toml path, for every band under
    `fossils_root` (sorted for deterministic load-order errors)."""
    return {p.parent.name: p for p in sorted(fossils_root.glob("*/MANIFEST.toml"))}


def load_states(fossils_root: Path = FOSSILS_ROOT) -> dict[str, tuple[str, dict[str, object]]]:
    """state id -> (band, manifest entry), across every band's MANIFEST. A
    state id declared in two MANIFESTs is a load error (`ValueError`)."""
    states: dict[str, tuple[str, dict[str, object]]] = {}
    for band, path in _manifests(fossils_root).items():
        data = tomllib.loads(path.read_text())
        for entry in data.get("state", []):
            sid = str(entry["id"])
            if sid in states:
                raise ValueError(
                    f"fossil state id {sid!r} declared in two MANIFESTs: "
                    f"{states[sid][0]!r} and {band!r}"
                )
            states[sid] = (band, entry)
    return states


def _resolve_producer(spec: str):
    module_name, func_name = spec.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def produce_succeeded(home: Path) -> None:
    """Spine fossil: one run of the shared `echo` plugin to completion."""
    home.mkdir(parents=True, exist_ok=True)
    kernel = harness.fresh_kernel(home=home)
    harness.run_to_dir(kernel, "echo", {"message": "fossil-succeeded"}, wait_ms=5000)


def produce_failed(home: Path) -> None:
    """Spine fossil: one run of a plugin that raises, terminal `failed`."""
    home.mkdir(parents=True, exist_ok=True)
    kernel = harness.fresh_kernel(plugin_dirs=[FIXTURE_PLUGIN_DIR], home=home)
    harness.run_to_dir(kernel, "boom", {}, wait_ms=5000)


def cmd_generate(args: argparse.Namespace) -> int:
    fossils_root = Path(args.fossils_root) if args.fossils_root else FOSSILS_ROOT
    states = load_states(fossils_root)

    if args.states == "all":
        selected = sorted(states)
    else:
        wanted = [s.strip() for s in args.states.split(",") if s.strip()]
        for sid in wanted:
            if sid not in states:
                print(f"fossils generate: unknown state {sid!r}")
                return 1
            band, _entry = states[sid]
            if band != args.checkpoint:
                print(
                    f"fossils generate: state {sid!r} belongs to band {band!r}, "
                    f"not --checkpoint {args.checkpoint!r}"
                )
                return 1
        selected = wanted

    pending: list[str] = []
    for sid in selected:
        band, entry = states[sid]
        producer = entry.get("producer", "pending")
        if producer == "pending" or entry.get("absent"):
            pending.append(sid)
            continue
        home = fossils_root / band / sid / "home"
        func = _resolve_producer(str(producer))
        func(home)

    if pending:
        print(f"fossils generate: {len(pending)} pending state(s), not failed: {pending}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.fossils")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--checkpoint", required=True)
    generate.add_argument("--states", required=True)
    generate.add_argument("--fossils-root", default=None, dest="fossils_root")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate":
        return cmd_generate(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
