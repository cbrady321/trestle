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


# L.P0-0d.20: named additive selectors (keys / items / grows) for every d1 facet.


def _entry(**selectors: object) -> dict[str, object]:
    return {"id": "ADD-x", "facet": "f", "direction": "additive", **selectors}


def _fdiff(
    golden: object, current: object, entries: list[dict[str, object]], policy: str = "named"
) -> tuple[list[str], list[str], int]:
    result = differ.facet_diff(
        "f",
        normalize.normalize(golden),
        normalize.normalize(current),
        policy=policy,
        entries=entries,
    )
    stale = differ.stale_selectors({"f"}, result.used, entries)
    return result.missing, result.unexpected, len(stale)


KEY = [_entry(keys=["$.frames.*.cleanup"])]
KEY_GOLDEN = {"frames": {"ok": {"a": "int"}}, "fields": ["a", "b"]}


def test_key_named_addition_is_excused() -> None:
    current = {"frames": {"ok": {"a": "int", "cleanup": "dict"}}, "fields": ["a", "b"]}
    assert _fdiff(KEY_GOLDEN, current, KEY) == ([], [], 0)


def test_key_unnamed_addition_fails() -> None:
    current = {
        "frames": {"ok": {"a": "int", "cleanup": "dict", "other": "x"}},
        "fields": ["a", "b"],
    }
    assert _fdiff(KEY_GOLDEN, current, KEY) == ([], ["$.frames.ok.other"], 0)
    # the selector's `*` is one segment: a deeper key of the same name is not named
    current2 = {"frames": {"ok": {"a": "int"}, "k": {"cleanup": 1}}, "fields": ["a", "b"]}
    assert _fdiff(KEY_GOLDEN, current2, KEY)[1] == ["$.frames.k"]


def test_key_removal_or_rename_fails_even_when_named() -> None:
    renamed = {"frames": {"ok": {"cleanup": "int"}}, "fields": ["a", "b"]}
    missing, unexpected, _ = _fdiff(KEY_GOLDEN, renamed, KEY)
    assert missing == ["$.frames.ok.a"]
    assert unexpected == []


def test_key_entry_does_not_excuse_reorder() -> None:
    current = {"frames": {"ok": {"a": "int", "cleanup": "dict"}}, "fields": ["b", "a"]}
    assert _fdiff(KEY_GOLDEN, current, KEY)[0] == ["$.fields[0]", "$.fields[1]"]


def test_key_entry_does_not_excuse_type_change() -> None:
    current = {"frames": {"ok": {"a": 1, "cleanup": "dict"}}, "fields": ["a", "b"]}
    assert _fdiff(KEY_GOLDEN, current, KEY)[0] == ["$.frames.ok.a"]


def test_key_stale_entry_is_reported() -> None:
    assert _fdiff(KEY_GOLDEN, KEY_GOLDEN, KEY) == ([], [], 1)


ITEM = [
    _entry(items=[{"path": "$.sequences.*", "value": "group_stop"}]),
    {**_entry(items=[{"path": "$.sequences.*", "value": "process_identity"}]), "id": "ADD-y"},
]
ITEM_GOLDEN = {"sequences": {"ok": ["created", "started", "ended"], "q": ["created"]}}


def test_item_named_insertion_mid_sequence_is_excused() -> None:
    current = {
        "sequences": {
            "ok": [
                "created",
                "started",
                "process_identity",
                "process_identity",
                "group_stop",
                "ended",
            ],
            "q": ["created"],
        }
    }
    assert _fdiff(ITEM_GOLDEN, current, ITEM) == ([], [], 0)
    # the same on a "free" facet
    assert _fdiff(ITEM_GOLDEN, current, ITEM, policy="free") == ([], [], 0)


def test_item_unnamed_insertion_fails_on_both_policies() -> None:
    current = {
        "sequences": {
            "ok": ["created", "started", "surprise", "group_stop", "ended"],
            "q": ["created"],
        }
    }
    for policy in ("named", "free"):
        missing, unexpected, _ = _fdiff(ITEM_GOLDEN, current, ITEM, policy=policy)
        assert missing == []
        assert unexpected == ['$.sequences.ok[+"surprise"]']
    trailing = {"sequences": {"ok": ["created", "started", "ended", "surprise"], "q": ["created"]}}
    assert _fdiff(ITEM_GOLDEN, trailing, ITEM, policy="free")[1] == ['$.sequences.ok[+"surprise"]']


def test_item_removal_fails() -> None:
    current = {"sequences": {"ok": ["created", "group_stop", "ended"], "q": ["created"]}}
    missing, unexpected, _ = _fdiff(ITEM_GOLDEN, current, ITEM)
    assert missing == ["$.sequences.ok[1]", "$.sequences.ok[2]"]
    assert unexpected == ['$.sequences.ok[+"ended"]']
    assert _fdiff(ITEM_GOLDEN, {"sequences": {"ok": ["created", "started", "ended"]}}, ITEM)[0] == [
        "$.sequences.q"
    ]


def test_item_reorder_of_existing_items_fails() -> None:
    current = {"sequences": {"ok": ["created", "ended", "group_stop", "started"], "q": ["created"]}}
    missing, unexpected, _ = _fdiff(ITEM_GOLDEN, current, ITEM)
    assert missing == ["$.sequences.ok[2]"]
    assert unexpected == ['$.sequences.ok[+"ended"]']


def test_item_type_change_fails() -> None:
    golden = {"sequences": {"ok": ["created", 1]}}
    current = {"sequences": {"ok": ["created", "group_stop", True]}}
    missing, unexpected, _ = _fdiff(golden, current, ITEM[:1])
    assert missing == ["$.sequences.ok[1]"]
    assert unexpected == ["$.sequences.ok[+true]"]
    # a named value is matched exactly, pair values included
    fields = [_entry(items=[{"path": "$.fields", "value": ["cleanup", "CleanupView | None"]}])]
    got = _fdiff(
        {"fields": [["a", "int"]]}, {"fields": [["a", "int"], ["cleanup", "dict"]]}, fields
    )
    assert got == ([], ['$.fields[+["cleanup", "dict"]]'], 1)


def test_item_stale_entry_is_reported() -> None:
    current = {"sequences": {"ok": ["created", "started", "group_stop", "ended"], "q": ["created"]}}
    result = differ.facet_diff(
        "f",
        normalize.normalize(ITEM_GOLDEN),
        normalize.normalize(current),
        policy="named",
        entries=ITEM,
    )
    stale = differ.stale_selectors({"f"}, result.used, ITEM)
    assert [s.entry_id for s in stale] == ["ADD-y"]


GROW = [_entry(grows=[{"path": "$.tools_bytes", "max": 100}])]
GROW_GOLDEN = {"tools_bytes": 50, "names": ["run", "query"]}


def test_grow_named_growth_within_max_is_excused() -> None:
    assert _fdiff(GROW_GOLDEN, {"tools_bytes": 100, "names": ["run", "query"]}, GROW) == ([], [], 0)


def test_grow_unnamed_or_over_max_fails() -> None:
    assert _fdiff(GROW_GOLDEN, {"tools_bytes": 51, "names": ["run", "query"]}, [])[0] == [
        "$.tools_bytes"
    ]
    assert _fdiff(GROW_GOLDEN, {"tools_bytes": 101, "names": ["run", "query"]}, GROW)[0] == [
        "$.tools_bytes"
    ]


def test_grow_shrink_or_removal_fails() -> None:
    assert _fdiff(GROW_GOLDEN, {"tools_bytes": 49, "names": ["run", "query"]}, GROW)[0] == [
        "$.tools_bytes"
    ]
    assert _fdiff(GROW_GOLDEN, {"names": ["run", "query"]}, GROW)[0] == ["$.tools_bytes"]


def test_grow_entry_does_not_excuse_reorder() -> None:
    current = {"tools_bytes": 60, "names": ["query", "run"]}
    assert _fdiff(GROW_GOLDEN, current, GROW)[0] == ["$.names[0]", "$.names[1]"]


def test_grow_type_change_fails() -> None:
    for bad in (60.0, True, "60"):
        assert _fdiff(GROW_GOLDEN, {"tools_bytes": bad, "names": ["run", "query"]}, GROW)[0] == [
            "$.tools_bytes"
        ]


def test_grow_stale_entry_is_reported() -> None:
    assert _fdiff(GROW_GOLDEN, GROW_GOLDEN, GROW) == ([], [], 1)


def test_selectors_need_additive_direction_and_exact_shape() -> None:
    bad = [
        {**_entry(keys=["$.a"]), "direction": "change"},
        {**_entry(keys=["$.a"]), "facet": "refusal_codes"},
        _entry(keys=["a"]),
        _entry(items=[{"path": "$.a"}]),
        _entry(grows=[{"path": "$.a", "max": "8"}]),
        _entry(keys="$.a"),
    ]
    for entry in bad:
        with pytest.raises(ValueError):
            differ.entry_selectors(entry)
    assert differ.entry_selectors({"id": "ADD-seed", "facet": "f", "direction": "additive"}) == []


def _d1_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, divergence: str) -> None:
    golden_path = tmp_path / "golden.json"
    golden_path.write_text(json.dumps({"seq": ["a", "b"]}))
    _write_facets(
        tmp_path,
        [
            {
                "id": "f",
                "additive": "free",
                "golden": "golden.json",
                "extractor": f"{__name__}:_seq_inserted_value",
            }
        ],
    )
    (tmp_path / "divergence.toml").write_text(divergence)
    monkeypatch.setattr(differ, "FACETS_PATH", tmp_path / "facets.toml")
    monkeypatch.setattr(differ, "ROOT", tmp_path)
    monkeypatch.setattr(differ, "DIVERGENCE_PATH", tmp_path / "divergence.toml")


def _seq_inserted_value() -> dict[str, object]:
    return {"seq": ["a", "new", "b"]}


NAMED_NEW = """
[[entry]]
id = "ADD-new"
facet = "f"
direction = "additive"
due_checkpoint = "s0"
items = [{ path = "$.seq", value = "new" }]
"""


def test_d1_excuses_named_insertion_and_fails_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _d1_world(tmp_path, monkeypatch, NAMED_NEW)
    assert differ.cmd_d1(Namespace(strict=True, facet=[])) == 0, capsys.readouterr().out
    _d1_world(tmp_path, monkeypatch, "")
    assert differ.cmd_d1(Namespace(strict=True, facet=[])) == 1
    assert "facet 'f': missing=['$.seq[1]']" in capsys.readouterr().out


def test_d1_reports_stale_and_bad_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = NAMED_NEW + '\n[[entry]]\nid = "ADD-gone"\nfacet = "f"\ndirection = "additive"\n'
    stale += 'items = [{ path = "$.seq", value = "never" }]\n'
    _d1_world(tmp_path, monkeypatch, stale)
    assert differ.cmd_d1(Namespace(strict=True, facet=[])) == 1
    out = capsys.readouterr().out
    assert "d1: STALE DIVERGENCE: divergence entry ADD-gone (facet 'f')" in out
    assert "UNEXPECTED" not in out
    _d1_world(tmp_path, monkeypatch, NAMED_NEW.replace('"additive"', '"change"'))
    assert differ.cmd_d1(Namespace(strict=True, facet=[])) == 1
    assert "d1: BAD DIVERGENCE ENTRY: divergence entry ADD-new" in capsys.readouterr().out
