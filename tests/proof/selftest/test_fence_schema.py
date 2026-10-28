"""Selftest for the fence's closed schema and loader (CM-2; L.P0-0d.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof import fence as fence_mod


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _base(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "fence.toml",
        """
leave = ["trestle/ops/**"]
record_exempt = ["tests/proof/host/host-*/*.json"]

[phases]
x = ["wr/x-"]
""",
    )


def test_table_form_fragments_normalize(tmp_path):
    base = _base(tmp_path)
    d_dir = tmp_path / "fence.d"
    _write(
        d_dir / "x.toml",
        """
phase = "x"

[lane.0]
name = "x-spine"
branch_prefix = "wr/x-spine/"
globs = ["a/**"]

[gate.0]
branch = "wr/x-spine/x1"
merge = "X-1"
""",
    )
    cfg = fence_mod.load_fence(fence_path=base, d_dir=d_dir)
    assert [lane.name for lane in cfg.lanes] == ["x-spine"]
    assert [gate.merge for gate in cfg.gates] == ["X-1"]


def test_key_alias_rejected(tmp_path):
    base = _base(tmp_path)
    d_dir = tmp_path / "fence.d"
    _write(
        d_dir / "x.toml",
        """
phase = "x"
[[lane]]
name = "x-spine"
branch = "wr/x-spine/"
globs = ["a/**"]
""",
    )
    with pytest.raises(fence_mod.FenceLoadError, match="branch"):
        fence_mod.load_fence(fence_path=base, d_dir=d_dir)

    d_dir2 = tmp_path / "fence.d2"
    _write(
        d_dir2 / "x.toml",
        """
phase = "x"
[[gate]]
branch_prefix = "wr/x-spine/"
merge = "X-1"
""",
    )
    with pytest.raises(fence_mod.FenceLoadError, match="branch_prefix"):
        fence_mod.load_fence(fence_path=base, d_dir=d_dir2)


@pytest.mark.parametrize("key", ["hot", "shared", "requires_tag", "produces_tag"])
def test_withdrawn_keys_rejected(tmp_path, key):
    base = _base(tmp_path)
    d_dir = tmp_path / "fence.d"
    _write(
        d_dir / "x.toml",
        f"""
phase = "x"
[[lane]]
name = "x-spine"
branch_prefix = "wr/x-spine/"
globs = ["a/**"]
{key} = true
""",
    )
    with pytest.raises(fence_mod.FenceLoadError, match="withdrawn"):
        fence_mod.load_fence(fence_path=base, d_dir=d_dir)


def test_unknown_key_rejected(tmp_path):
    base = _base(tmp_path)
    d_dir = tmp_path / "fence.d"
    _write(
        d_dir / "x.toml",
        """
phase = "x"
mystery = 1
[[lane]]
name = "x-spine"
branch_prefix = "wr/x-spine/"
globs = ["a/**"]
""",
    )
    with pytest.raises(fence_mod.FenceLoadError, match="unknown"):
        fence_mod.load_fence(fence_path=base, d_dir=d_dir)

    base2 = _write(
        tmp_path / "fence2.toml",
        """
leave = []
weird = 1
""",
    )
    with pytest.raises(fence_mod.FenceLoadError, match="unknown"):
        fence_mod.load_fence(fence_path=base2, d_dir=tmp_path / "empty.d")


def test_phase_first_pr_may_create_own_fragment_only(tmp_path):
    """A branch may create or edit only the `fence.d` fragment of the phase
    `[phases]` assigns to its prefix (CM-2 R4's full clause, evaluated by
    L.P0-0d.7's `check --pr`); this leaf proves only that `phase_for_branch`
    resolves the right fragment name, which R4 relies on."""
    cfg = fence_mod.load_fence()
    assert fence_mod.phase_for_branch("wr/p0-spine/0d", cfg.phases) == "p0"
    assert fence_mod.phase_for_branch("wr/p0-lane-a/g-a1", cfg.phases) == "p0"
    assert fence_mod.phase_for_branch("wr/cl-a/x", cfg.phases) == "core"
    assert fence_mod.phase_for_branch("wr/unknown/x", cfg.phases) is None


def test_gate_branch_prefix_only_when_trailing_slash(tmp_path):
    gates = [
        fence_mod.Gate(branch="wr/x/rb-1", merge="RB-1"),
        fence_mod.Gate(branch="wr/x/rb-10", merge="RB-10"),
        fence_mod.Gate(branch="wr/x/rb-11", merge="RB-11"),
        fence_mod.Gate(branch="wr/x/rb-12", merge="RB-12"),
        fence_mod.Gate(branch="wr/x/rb-13", merge="RB-13"),
    ]
    # `wr/x/rb-1` (no trailing slash on any gate, so equality only) matches
    # exactly its own gate, never `rb-10`..`rb-13`.
    assert fence_mod.match_gate("wr/x/rb-1", gates).merge == "RB-1"
    assert fence_mod.match_gate("wr/x/rb-10", gates).merge == "RB-10"
    prefix_gates = [
        fence_mod.Gate(branch="wr/p0-lane-a/", merge="P0-1A"),
    ]
    assert fence_mod.match_gate("wr/p0-lane-a/g-a1", prefix_gates).merge == "P0-1A"
    zero_or_two = [
        fence_mod.Gate(branch="wr/x/a", merge="A"),
        fence_mod.Gate(branch="wr/x/b", merge="B"),
    ]
    with pytest.raises(fence_mod.GateMatchError):
        fence_mod.match_gate("wr/x/c", zero_or_two)


def test_bang_glob_excludes_in_order():
    globs = ["tests/proof/**", "!tests/proof/labels.d/p0-1a.toml"]
    assert fence_mod.glob_match("tests/proof/fence.py", globs) is True
    assert fence_mod.glob_match("tests/proof/labels.d/p0-1a.toml", globs) is False
    assert fence_mod.glob_match("tests/proof/labels.d/p0.toml", globs) is True

    # order matters: an include after an exclude re-includes.
    reorder = ["!tests/proof/labels.d/p0-1a.toml", "tests/proof/**"]
    assert fence_mod.glob_match("tests/proof/labels.d/p0-1a.toml", reorder) is True
