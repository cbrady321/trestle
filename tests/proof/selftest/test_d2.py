"""Selftest for `python -m tests.proof.differ d2` (L.P0-0c.7)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from tests.proof import d2_driver, differ


def test_reader_origin_asserted() -> None:
    import trestle

    real_root = Path(trestle.__file__).resolve().parents[1]
    d2_driver._assert_reader_origin(real_root)  # no raise

    with pytest.raises(AssertionError):
        d2_driver._assert_reader_origin(Path("/nonexistent-reader-root"))


def test_exception_schema_exact(tmp_path: Path) -> None:
    good = tmp_path / "good.toml"
    good.write_text(
        '[[exception]]\nid = "E-1"\nreader = "s0"\nstates = ["s0/succeeded"]\n'
        'declared_by = "test"\n'
    )
    assert differ._load_exceptions(good) == [
        {"id": "E-1", "reader": "s0", "states": ["s0/succeeded"], "declared_by": "test"}
    ]


def test_exception_withdrawn_keys_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[[exception]]\nid = "E-1"\nreader = "s0"\nstates = ["s0/succeeded"]\n'
        'declared_by = "test"\nretires_at = "J0"\n'
    )
    with pytest.raises(ValueError):
        differ._load_exceptions(bad)

    bad2 = tmp_path / "bad2.toml"
    bad2.write_text(
        '[[exception]]\nid = "E-1"\nreader = "s0"\nstates = ["s0/succeeded"]\n'
        'declared_by = "test"\nscope = "x"\n'
    )
    with pytest.raises(ValueError):
        differ._load_exceptions(bad2)

    bad3 = tmp_path / "bad3.toml"
    bad3.write_text(
        '[[exception]]\nid = "E-1"\nreader = "s0"\nstates = ["s0/succeeded"]\n'
        'declared_by = "test"\nregister_id = "TM-P0-2:G-1"\n'
    )
    with pytest.raises(ValueError):
        differ._load_exceptions(bad3)


def test_exception_excuses_only_its_states_for_its_reader() -> None:
    exceptions = [{"id": "E-1", "reader": "s0", "states": ["s0/succeeded"], "declared_by": "test"}]
    assert differ._excused(exceptions, "s0", "s0/succeeded") is True
    assert differ._excused(exceptions, "other-reader", "s0/succeeded") is False
    assert differ._excused(exceptions, "s0", "s0/failed") is False


def test_any_ref_and_fossils_dir_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fossils_root = tmp_path / "fossils"
    fossils_root.mkdir()
    (fossils_root / "s0").mkdir()
    (fossils_root / "s0" / "MANIFEST.toml").write_text(
        '[[state]]\nid = "created"\nproducer = "pending"\nabsent = false\n'
    )
    rc = differ.cmd_d2(Namespace(reader="HEAD", fossils=str(fossils_root)))
    # No real producer states in this fossils root -> nothing to check.
    assert rc == 0


def test_planted_reader_divergence_reported(tmp_path: Path) -> None:
    from tests.proof import fossils as fossils_mod

    fossils_root = tmp_path / "fossils"
    fossils_root.mkdir()
    band_dir = fossils_root / "s0"
    band_dir.mkdir()
    # Plant a MANIFEST claiming a "succeeded" producer's fossil is actually
    # named "failed" (id mismatched against what the producer really
    # writes), so the reader's real projected_state diverges from the
    # planted expectation.
    (band_dir / "MANIFEST.toml").write_text(
        '[[state]]\nid = "failed"\nproducer = "tests.proof.fossils:produce_succeeded"\n'
        "absent = false\n"
    )
    home = band_dir / "failed" / "home"
    fossils_mod.produce_succeeded(home)

    rc = differ.cmd_d2(Namespace(reader="HEAD", fossils=str(fossils_root)))
    assert rc == 1


def test_manifest_s0_projection_state_is_the_expectation(tmp_path: Path) -> None:
    from tests.proof import fossils as fossils_mod

    fossils_root = tmp_path / "fossils"
    band_dir = fossils_root / "s0"
    band_dir.mkdir(parents=True)
    fossils_mod.produce_succeeded(band_dir / "renamed" / "home")

    def manifest(projected: str) -> str:
        return (
            '[[state]]\nid = "renamed"\nproducer = "tests.proof.fossils:produce_succeeded"\n'
            "absent = false\n"
            f's0_projection = {{ state = "{projected}", kinds = [], meta_keys = [] }}\n'
        )

    (band_dir / "MANIFEST.toml").write_text(manifest("succeeded"))
    assert differ.cmd_d2(Namespace(reader="HEAD", fossils=str(fossils_root))) == 0
    (band_dir / "MANIFEST.toml").write_text(manifest("failed"))
    assert differ.cmd_d2(Namespace(reader="HEAD", fossils=str(fossils_root))) == 1
