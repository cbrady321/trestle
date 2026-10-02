"""Facet extractors for lane A (L.P0-1A.7): `ledger_kinds`, `meta_fields`,
`fossil_projection`. Registered by "module:function" in
`tests/proof/facets.toml`; their S0 goldens live in
`tests/fixtures/golden/s0/<facet>.json` and `python -m tests.proof.differ d1`
diffs today's code against them.

`ledger_kinds` and `meta_fields` exercise today's kernel: every S0 state is
produced live, in a scratch directory, by `fossil_producers`, and read back
through the independent records seam. `fossil_projection` reads today's
*committed* S0 fossils through both the seam and today's product reader
(`recover_run_dir`, `RunLedger.projected_state`) on a scratch copy.
"""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from tests.pins.a_lifecycle import fossil_producers as fp
from tests.proof import fossils, records
from trestle.server.ledger import TERMINAL_KINDS, RunLedger, ledger_path
from trestle.server.recovery import recover_run_dir

ROOT = fp.ROOT
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "golden" / "s0"
FACET_FUNCS = ("ledger_kinds", "meta_fields", "fossil_projection")

_SPINE_PRODUCERS = {
    "succeeded": fossils.produce_succeeded,
    "failed": fossils.produce_failed,
}


def _run_dir(root: Path, state: str) -> Path:
    dirs = sorted((root / fp.BAND / state / "home" / "runs").glob("*/r_*"))
    assert len(dirs) == 1, (state, dirs)
    return dirs[0]


@functools.lru_cache(maxsize=1)
def _live_root() -> Path:
    """Every S0 state produced live by today's code, once per process."""
    root = Path(tempfile.mkdtemp(prefix="lane-a-live-"))
    fp.generate(fp.STATE_IDS, root)
    for state, produce in _SPINE_PRODUCERS.items():
        home = root / fp.BAND / state / "home"
        home.mkdir(parents=True)
        produce(home)
    return root


def _live_states() -> list[str]:
    return sorted([*fp.STATE_IDS, *_SPINE_PRODUCERS])


def ledger_kinds() -> dict[str, Any]:
    """The S0 ledger kind sequence of every run state, and the terminal kinds."""
    root = _live_root()
    return {
        "terminal_kinds": sorted(TERMINAL_KINDS),
        "sequences": {
            state: records.node_record(_run_dir(root, state)).kinds for state in _live_states()
        },
    }


def meta_fields() -> dict[str, Any]:
    """Every `evidence/meta.json` field, by name and JSON type, per state."""
    root = _live_root()
    out: dict[str, Any] = {}
    for state in _live_states():
        meta_path = _run_dir(root, state) / "evidence" / "meta.json"
        if not meta_path.exists():
            out[state] = None
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        out[state] = {key: type(value).__name__ for key, value in sorted(meta.items())}
    return out


def fossil_projection() -> dict[str, Any]:
    """How today's readers project every committed S0 fossil (before and
    after recovery). A state declared absent (`crashed`, K-13) projects as
    `{"absent": true}`."""
    out: dict[str, Any] = {}
    for sid, (band, entry) in sorted(fossils.load_states(fp.FOSSILS_ROOT).items()):
        if band != fp.BAND:
            continue
        if entry.get("absent"):
            out[sid] = {"absent": True}
            continue
        home = fp.FOSSILS_ROOT / band / sid / "home"
        run_dir = sorted((home / "runs").glob("*/r_*"))[0]
        rows = records.ledger_rows(run_dir)
        node = records.node_record(run_dir)
        meta_path = run_dir / "evidence" / "meta.json"
        before = RunLedger.open(ledger_path(run_dir)).projected_state()
        with tempfile.TemporaryDirectory(prefix="lane-a-proj-") as scratch:
            copy_home = Path(scratch) / "home"
            shutil.copytree(home, copy_home)
            copy_run = sorted((copy_home / "runs").glob("*/r_*"))[0]
            recover_run_dir(copy_run)
            after_ledger = RunLedger.open(ledger_path(copy_run))
            after = {
                "kinds": [r.get("kind") for r in after_ledger.records],
                "projected_state": after_ledger.projected_state(),
            }
        out[sid] = {
            "seam": {
                "kinds": node.kinds,
                "terminal": node.terminal,
                "torn": rows.torn,
                "meta_keys": (
                    sorted(json.loads(meta_path.read_text(encoding="utf-8")))
                    if meta_path.exists()
                    else None
                ),
            },
            "reader": {"projected_state": before, "after_recovery": after},
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.pins.a_lifecycle.golden")
    parser.add_argument("command", choices=["write"])
    parser.parse_args(argv)
    for name in FACET_FUNCS:
        value = globals()[name]()
        path = GOLDEN_DIR / f"{name}.json"
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
