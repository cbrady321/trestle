"""Change 2b: the `status` row's 4 KiB budget with every capped list at its cap and every cut
string at its longest, and the cuts themselves."""

from __future__ import annotations

import json
import time
from pathlib import Path

from tests.proof import harness
from trestle.common.fsutil import atomic_write_json
from trestle.server import pool as pools
from trestle.server import status_view as sv


def test_cut_bytes_keeps_a_character_boundary() -> None:
    assert sv.cut_bytes("abc", 10) == "abc"
    cut = sv.cut_bytes("é" * 100, sv.ENV_MAX_BYTES)
    assert cut.endswith("…") and len(cut.encode("utf-8")) <= sv.ENV_MAX_BYTES
    cut = sv.cut_bytes("x" * 500, sv.PLUGIN_DIR_MAX_BYTES)
    assert len(cut.encode("utf-8")) <= sv.PLUGIN_DIR_MAX_BYTES


def test_the_envelope_fits_4_kib_at_every_cap(tmp_path: Path) -> None:
    kernel = harness.fresh_kernel(home=tmp_path / "home")
    control = kernel.control
    control.version = "0.5.1+g" + "f" * 40 + ".dirty"
    far = time.time() + 3600
    state = pools.empty_state()
    for n in range(sv.ENVIRONMENTS_MAX * 2):
        key = json.dumps("é" * 200 + str(n))
        state["running"][f"r_20261022T100000Z_{n:04d}aaaaaaaaaa"] = {
            "server": "s_x",
            "key": key,
            "deadline": far + n,
        }
    atomic_write_json(pools.sched_path(control.project.home), state)
    control.project.registry.plugin_dirs = [
        tmp_path / ("missing-" + "d" * 200 + str(n)) / ("e" * 100)
        for n in range(sv.PLUGIN_DIRS_MAX * 2)
    ]
    envelope = sv.status_envelope(control)
    size = len(json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    assert size <= sv.STATUS_BUDGET_BYTES, size
    (row,) = envelope["items"]
    assert set(row) == sv.STATUS_FIELDS
    assert len(row["environments"]) == sv.ENVIRONMENTS_MAX
    assert row["environments_truncated"] is True
    assert all(len(e["env"].encode("utf-8")) <= sv.ENV_MAX_BYTES for e in row["environments"])
    free = [e["free_at"] for e in row["environments"]]
    assert free == sorted(free)
    assert len(row["plugin_dirs_missing"]) == sv.PLUGIN_DIRS_MAX
    assert all(
        len(p.encode("utf-8")) <= sv.PLUGIN_DIR_MAX_BYTES for p in row["plugin_dirs_missing"]
    )
    assert row["slots"]["in_use"] == sv.ENVIRONMENTS_MAX * 2


def test_status_says_nothing_about_credentials(tmp_path: Path) -> None:
    kernel = harness.fresh_kernel(home=tmp_path / "home")
    text = json.dumps(kernel.control.query("status")).lower()
    for word in ("credential", "grant", "store", "keychain", "pid", "port", "session_id"):
        assert word not in text, word
