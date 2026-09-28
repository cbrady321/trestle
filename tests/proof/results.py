"""Per-result JSONL records (MC-P0-05) and gate/venue vocabulary
(L.P0-0a.3).

A result is written only when `TRESTLE_PROOF_GATE` is set in the
environment; the plugin writes one JSONL line per test outcome to
`tests/proof/results/<gate>.jsonl`. A gate name is *declared* when it is
listed in `meta_config.toml`'s `[gates] names` or in any
`tests/proof/gates.d/*.toml` fragment's `[gates] names` (CSC-13); the
ledger renderer (`ledger.py`) ignores results recorded under an undeclared
gate name.
"""

from __future__ import annotations

import json
import os
import platform
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
META_CONFIG_PATH = ROOT / "tests" / "proof" / "meta_config.toml"
GATES_DIR = ROOT / "tests" / "proof" / "gates.d"
RESULTS_DIR = ROOT / "tests" / "proof" / "results"

LOCAL_VENUE = "LOCAL"


def declared_gates(
    meta_config_path: Path = META_CONFIG_PATH, gates_dir: Path = GATES_DIR
) -> set[str]:
    gates: set[str] = set()
    if meta_config_path.exists():
        data = tomllib.loads(meta_config_path.read_text())
        gates.update(data.get("gates", {}).get("names", []))
    if gates_dir.exists():
        for path in sorted(gates_dir.glob("*.toml")):
            data = tomllib.loads(path.read_text())
            gates.update(data.get("gates", {}).get("names", []))
    return gates


def current_gate() -> str | None:
    return os.environ.get("TRESTLE_PROOF_GATE") or None


def venue_for_gate(gate: str | None) -> str:
    """A CSC-9-style default: undeclared/absent gate is LOCAL; `ci-*` is CI;
    `host-*` is HOST. A gate whose venue truly diverges from its name
    prefix registers its own fragment; none does in P0-0a."""
    if gate is None:
        return LOCAL_VENUE
    if gate.startswith("host-"):
        return "HOST"
    return "CI"


@dataclass
class Record:
    nodeid: str
    outcome: str  # passed | failed | skipped | xfailed | xpassed | error
    gate: str | None
    venue: str
    interpreter: str
    labels: list[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def current_interpreter() -> str:
    return platform.python_version()


def write_record(record: Record, results_dir: Path = RESULTS_DIR) -> None:
    gate = record.gate or "local"
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{gate}.jsonl"
    with path.open("a") as fh:
        fh.write(record.to_json() + "\n")


def read_records(results_dir: Path = RESULTS_DIR) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    if not results_dir.exists():
        return records
    for path in sorted(results_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def clear_results(results_dir: Path = RESULTS_DIR) -> None:
    if not results_dir.exists():
        return
    for path in results_dir.glob("*.jsonl"):
        path.unlink()
