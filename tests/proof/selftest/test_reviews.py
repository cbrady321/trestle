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


def _paired(rid: str, criteria: tuple[str, ...], log: str = "paired_check_log") -> str:
    lines = [
        f'id = "{rid}"',
        'mode = "stage-critic review"',
        'outcome = "pass"',
        'sha = "6cef746"',
        f'{log} = "pytest -q <paired check>: passed"',
        "",
        "[criteria]",
        *(f'{name} = "pass"' for name in criteria),
    ]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("rid", ["RV-1", "RV-2", "RV-3", "RV-4", "RV-5"])
def test_each_review_valid_with_its_own_criteria(tmp_path, rid):
    criteria, log = reviews_mod.CRITERIA[rid]
    record = reviews_mod.load(_write(tmp_path, _paired(rid, criteria, log), f"{rid}-core.toml"))
    assert set(record["criteria"]) == set(criteria)


@pytest.mark.parametrize("rid", ["RV-1", "RV-2", "RV-3", "RV-4"])
def test_planted_missing_or_extra_criterion_rejected(tmp_path, rid):
    criteria, log = reviews_mod.CRITERIA[rid]
    for planted in (criteria[:-1], (*criteria, "unnamed_extra")):
        with pytest.raises(reviews_mod.ReviewSchemaError):
            reviews_mod.load(_write(tmp_path, _paired(rid, planted, log), f"{rid}-core.toml"))


def test_another_reviews_criteria_rejected(tmp_path):
    """RV-5's transcription criteria on RV-1 (with either log key) is not an RV-1 record."""
    for log in ("transcribe_log", "paired_check_log"):
        text = _paired("RV-1", reviews_mod.CRITERIA_KEYS, log)
        with pytest.raises(reviews_mod.ReviewSchemaError):
            reviews_mod.load(_write(tmp_path, text, "RV-1-core.toml"))


def test_paired_review_needs_its_log_and_rv_without_criteria_rejected(tmp_path):
    criteria, _log = reviews_mod.CRITERIA["RV-3"]
    no_log = _paired("RV-3", criteria).replace(
        'paired_check_log = "pytest -q <paired check>: passed"\n', ""
    )
    with pytest.raises(reviews_mod.ReviewSchemaError):
        reviews_mod.load(_write(tmp_path, no_log, "RV-3-core.toml"))
    with pytest.raises(reviews_mod.ReviewSchemaError):
        reviews_mod.load(_write(tmp_path, _paired("RV-9", criteria), "RV-9-core.toml"))
