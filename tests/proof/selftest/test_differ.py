"""Selftest for `python -m tests.proof.differ d1` (L.P0-0c.4): every
planted defect must render red."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from tests.proof import differ, normalize

# Extractor targets resolved by dotted "module:function" (this module).


def _fixed_value() -> dict[str, object]:
    return {"a": 1, "b": 2}


def _renamed_key_value() -> dict[str, object]:
    return {"a": 1, "c": 2}


def _additive_named_value() -> dict[str, object]:
    return {"a": 1, "b": 2, "extra": 3}


def _write_facets(tmp_path: Path, facets: list[dict[str, object]]) -> None:
    lines = []
    for f in facets:
        lines.append("[[facet]]")
        for k, v in f.items():
            lines.append(f"{k} = {v!r}" if not isinstance(v, str) else f'{k} = "{v}"')
        lines.append("")
    (tmp_path / "facets.toml").write_text("\n".join(lines))


def test_planted_unexpected_diff_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    golden_path = golden_dir / "f.json"
    golden_path.write_text(json.dumps({"a": 1, "b": 2}))

    _write_facets(
        tmp_path,
        [
            {
                "id": "f",
                "additive": "named",
                "golden": str(golden_path.relative_to(tmp_path)),
                "extractor": f"{__name__}:_renamed_key_value",
            }
        ],
    )
    monkeypatch.setattr(differ, "FACETS_PATH", tmp_path / "facets.toml")
    monkeypatch.setattr(differ, "ROOT", tmp_path)
    monkeypatch.setattr(differ, "DIVERGENCE_PATH", tmp_path / "no-such-divergence.toml")

    rc = differ.cmd_d1(Namespace(strict=False, facet=[]))
    assert rc == 1


def test_planted_missing_due_entry_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    golden_path = golden_dir / "f.json"
    golden_path.write_text(json.dumps({"a": 1}))

    _write_facets(
        tmp_path,
        [
            {
                "id": "pending_facet",
                "additive": "named",
                "golden": "golden/pending.json",
                "extractor": "pending",
            }
        ],
    )
    (tmp_path / "divergence.toml").write_text(
        '[[entry]]\nid = "D-1"\nfacet = "pending_facet"\ndue_checkpoint = "s0"\n'
    )
    monkeypatch.setattr(differ, "FACETS_PATH", tmp_path / "facets.toml")
    monkeypatch.setattr(differ, "ROOT", tmp_path)
    monkeypatch.setattr(differ, "DIVERGENCE_PATH", tmp_path / "divergence.toml")

    rc = differ.cmd_d1(Namespace(strict=False, facet=[]))
    assert rc == 1


def test_normalizer_does_not_mask_key_rename() -> None:
    golden = normalize.normalize({"a": 1, "b": 2})
    current = normalize.normalize({"a": 1, "c": 2})
    missing, unexpected = normalize.structural_diff(golden, current, policy="named")
    assert missing == ["$.b"]
    assert unexpected == ["$.c"]


def test_additive_policy_named_vs_free() -> None:
    golden = normalize.normalize({"a": 1, "b": 2})
    current = normalize.normalize({"a": 1, "b": 2, "extra": 3})

    missing_named, unexpected_named = normalize.structural_diff(golden, current, policy="named")
    assert missing_named == []
    assert unexpected_named == ["$.extra"]

    missing_free, unexpected_free = normalize.structural_diff(golden, current, policy="free")
    assert missing_free == []
    assert unexpected_free == []


def test_unbuilt_mode_exits_2() -> None:
    for mode in differ.UNBUILT_MODES:
        assert differ.main([mode]) == 2


def _codes_current(**extra: str) -> dict[str, object]:
    return normalize.normalize({"codes": {"OLD": "a.old", **extra}})


def _codes_diff(current: dict[str, object]) -> tuple[list[str], list[str]]:
    golden = normalize.normalize({"codes": {"OLD": "a.old"}})
    missing, unexpected = normalize.structural_diff(golden, current, policy="named")
    return missing, differ.drop_named_additive_codes(unexpected, current)


def _name_codes(monkeypatch: pytest.MonkeyPatch, *codes: str) -> None:
    entries = [{"facet": "refusal_codes", "direction": "additive", "codes": list(codes)}]
    monkeypatch.setattr(differ, "load_divergence", lambda: entries)


def test_unnamed_new_code_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _name_codes(monkeypatch, "x.other")
    assert _codes_diff(_codes_current(NEW="x.new")) == ([], ["$.codes.NEW"])


def test_named_new_code_passes_exact_and_glob(monkeypatch: pytest.MonkeyPatch) -> None:
    _name_codes(monkeypatch, "x.new")
    assert _codes_diff(_codes_current(NEW="x.new")) == ([], [])
    _name_codes(monkeypatch, "x.*")
    assert _codes_diff(_codes_current(NEW="x.new")) == ([], [])


def test_removed_or_renamed_s0_code_fails_even_if_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _name_codes(monkeypatch, "a.*", "b.*")
    assert _codes_diff(normalize.normalize({"codes": {}}))[0] == ["$.codes.OLD"]
    assert _codes_diff(normalize.normalize({"codes": {"OLD": "b.old"}}))[0] == ["$.codes.OLD"]
