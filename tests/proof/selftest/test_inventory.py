"""Self-tests for the gap/facet/fossil-state inventories and their `meta
inventory` CLI (L.P0-0a.4)."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GAPS_PATH = ROOT / "tests" / "proof" / "gaps.toml"
FACETS_PATH = ROOT / "tests" / "proof" / "facets.toml"
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "fossils" / "s0" / "MANIFEST.toml"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.proof.meta", "inventory", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_21_gaps_11_facets_19_states_declared() -> None:
    gaps = tomllib.loads(GAPS_PATH.read_text())["gap"]
    facets = tomllib.loads(FACETS_PATH.read_text())["facet"]
    states = tomllib.loads(MANIFEST_PATH.read_text())["state"]
    assert len(gaps) == 21
    assert len({g["id"] for g in gaps}) == 21
    assert len(facets) == 11
    assert len({f["id"] for f in facets}) == 11
    # 19 real S0 states plus one additional absent entry (`crashed`, K-13).
    present = [s for s in states if not s.get("absent")]
    assert len(present) == 19
    assert len({s["id"] for s in present}) == 19
    crashed = [s for s in states if s["id"] == "crashed"]
    assert crashed and crashed[0]["absent"] is True


def test_g_c4_target_is_none_others_required() -> None:
    gaps = tomllib.loads(GAPS_PATH.read_text())["gap"]
    by_id = {g["id"]: g for g in gaps}
    assert by_id["G-C4"]["target"] == "none(DM-20)"
    for gap_id, entry in by_id.items():
        if gap_id != "G-C4":
            assert entry["target"] == "required", gap_id


def _pending_total() -> int:
    gaps = tomllib.loads(GAPS_PATH.read_text())["gap"]
    facets = tomllib.loads(FACETS_PATH.read_text())["facet"]
    states = tomllib.loads(MANIFEST_PATH.read_text())["state"]
    return (
        sum(1 for g in gaps if g.get("entry") == "pending")
        + sum(1 for f in facets if f.get("extractor") == "pending")
        + sum(1 for s in states if s.get("producer") == "pending" and not s.get("absent"))
    )


def test_count_pending_exits_0_iff_pending() -> None:
    # TM-P0-8 is removed once every pending state is discharged, so the
    # expected count is read from the inventory, not frozen at >= 1.
    expected = _pending_total()
    proc = _run("--count-pending")
    assert int(proc.stdout.strip()) == expected
    assert proc.returncode == (0 if expected else 1), proc.stdout + proc.stderr


def test_absent_state_is_never_pending() -> None:
    from tests.proof import meta

    assert meta._state_pending({"producer": "pending", "absent": True}) is False
    assert meta._state_pending({"producer": "pending", "absent": False}) is True
    assert meta._state_pending({"producer": "m:f", "absent": False}) is False


def test_count_pending_lane_filter() -> None:
    # Lanes discharge their own entries (TM-P0-8), so the expected lane-A
    # count is read from the inventory rather than frozen at its P0-0a value.
    gaps = tomllib.loads(GAPS_PATH.read_text())["gap"]
    expected_a = sum(1 for g in gaps if g["lane"] == "A" and g.get("entry") == "pending")
    proc_a = _run("--count-pending", "--lane", "A")
    proc_z = _run("--count-pending", "--lane", "does-not-exist")
    assert int(proc_a.stdout.strip()) == expected_a
    assert proc_a.returncode == (0 if expected_a else 1)
    assert proc_z.returncode == 1
    assert int(proc_z.stdout.strip()) == 0


def test_strict_fails_on_pending() -> None:
    proc = _run("--strict")
    assert proc.returncode == (1 if _pending_total() else 0), proc.stdout + proc.stderr
