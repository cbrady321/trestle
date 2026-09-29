"""Selftest for the review record loader (L.P0-0d.8): planted records."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof import reviews as reviews_mod

GOOD = """\
id = "RV-5"
mode = "stage-critic review"
outcome = "pass"
sha = "3e82d1f"
transcribe_log = "transcribe --check all: exit 0"

[criteria]
verbatim_fragments = "pass"
row_set = "pass"
owner = "pass"
k_docs = "pass"
"""


def _write(tmp_path: Path, text: str, name: str = "RV-5-j0.toml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_valid_record_loads(tmp_path):
    record = reviews_mod.load(_write(tmp_path, GOOD))
    assert record["id"] == "RV-5" and record["outcome"] == "pass"
    assert set(record["criteria"]) == set(reviews_mod.CRITERIA_KEYS)


@pytest.mark.parametrize(
    "old,new",
    [
        ('outcome = "pass"', 'outcome = "maybe"'),
        ('mode = "stage-critic review"', 'mode = "human review"'),
        ('sha = "3e82d1f"', 'sha = "HEAD"'),
        ('transcribe_log = "transcribe --check all: exit 0"', 'transcribe_log = " "'),
        ('id = "RV-5"', 'id = "RV-5"\nextra = 1'),
        ('owner = "pass"\n', ""),
        ('k_docs = "pass"', 'k_docs = "fail"'),
    ],
)
def test_planted_malformed_record_rejected(tmp_path, old, new):
    with pytest.raises(reviews_mod.ReviewSchemaError):
        reviews_mod.load(_write(tmp_path, GOOD.replace(old, new)))


def test_file_name_must_match_id(tmp_path):
    with pytest.raises(reviews_mod.ReviewSchemaError):
        reviews_mod.load(_write(tmp_path, GOOD, name="RV-4-j0.toml"))


def test_failing_record_is_valid_when_consistent(tmp_path):
    text = GOOD.replace('outcome = "pass"', 'outcome = "fail"').replace(
        'row_set = "pass"', 'row_set = "fail"'
    )
    assert reviews_mod.load(_write(tmp_path, text))["outcome"] == "fail"


def test_load_valid_skips_malformed(tmp_path):
    _write(tmp_path, GOOD)
    _write(tmp_path, "id = 1", name="RV-9-j0.toml")
    assert [r["id"] for r in reviews_mod.load_valid(tmp_path)] == ["RV-5"]
    assert reviews_mod.load_valid(tmp_path / "absent") == []
