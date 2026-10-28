"""Selftest for `python -m tests.proof.fossils generate` (L.P0-0c.6)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from tests.proof import fossils, records


def test_generate_spine_states_reads_back_via_seam(tmp_path: Path) -> None:
    fossils_root = tmp_path / "fossils"
    fossils_root.mkdir()
    (fossils_root / "s0").mkdir()
    (fossils_root / "s0" / "MANIFEST.toml").write_text(
        """
[[state]]
id = "succeeded"
producer = "tests.proof.fossils:produce_succeeded"
absent = false

[[state]]
id = "failed"
producer = "tests.proof.fossils:produce_failed"
absent = false
"""
    )

    rc = fossils.cmd_generate(
        Namespace(checkpoint="s0", states="succeeded,failed", fossils_root=str(fossils_root))
    )
    assert rc == 0

    succeeded_home = fossils_root / "s0" / "succeeded" / "home"
    failed_home = fossils_root / "s0" / "failed" / "home"
    assert succeeded_home.exists()
    assert failed_home.exists()

    succeeded_run_dir = next((succeeded_home / "runs").rglob("r_*"))
    node = records.node_record(succeeded_run_dir)
    assert node.terminal == "succeeded"

    failed_run_dir = next((failed_home / "runs").rglob("r_*"))
    node = records.node_record(failed_run_dir)
    assert node.terminal == "failed"


def test_pending_state_reported_not_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fossils_root = tmp_path / "fossils"
    fossils_root.mkdir()
    (fossils_root / "s0").mkdir()
    (fossils_root / "s0" / "MANIFEST.toml").write_text(
        """
[[state]]
id = "created"
producer = "pending"
absent = false
"""
    )

    rc = fossils.cmd_generate(
        Namespace(checkpoint="s0", states="created", fossils_root=str(fossils_root))
    )
    assert rc == 0
    assert "pending" in capsys.readouterr().out


def test_generate_reads_every_band_manifest(tmp_path: Path) -> None:
    fossils_root = tmp_path / "fossils"
    fossils_root.mkdir()
    (fossils_root / "s0").mkdir()
    (fossils_root / "s0" / "MANIFEST.toml").write_text(
        """
[[state]]
id = "succeeded"
producer = "tests.proof.fossils:produce_succeeded"
absent = false
"""
    )
    (fossils_root / "otherband").mkdir()
    (fossils_root / "otherband" / "MANIFEST.toml").write_text(
        """
[[state]]
id = "other-state"
producer = "tests.proof.fossils:produce_succeeded"
absent = false
"""
    )

    states = fossils.load_states(fossils_root)
    assert "succeeded" in states
    assert "other-state" in states
    assert states["other-state"][0] == "otherband"

    rc = fossils.cmd_generate(
        Namespace(checkpoint="s0", states="all", fossils_root=str(fossils_root))
    )
    assert rc == 0
    assert (fossils_root / "s0" / "succeeded" / "home").exists()
    assert (fossils_root / "otherband" / "other-state" / "home").exists()

    # A state id planted in two MANIFESTs is refused.
    (fossils_root / "s0" / "MANIFEST.toml").write_text(
        """
[[state]]
id = "other-state"
producer = "pending"
absent = false
"""
    )
    with pytest.raises(ValueError):
        fossils.load_states(fossils_root)
