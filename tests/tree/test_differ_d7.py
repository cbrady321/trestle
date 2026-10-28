"""L.TR-4.7: differ mode d7 (permutation and one-level-deeper invariance) and its planted-negative
self-test. The mode judges answers, so every case here plants an answer set: no product roll-up is
needed (L.TR-4.5 makes d7 exit 0 over the product's own answers)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from tests.fixtures.trees import generators
from tests.proof import differ
from tests.proof.differ_modes import d7_permutation_depth as d7

# One logical answer per base fixture: a failed root whose primary is the same logical node.
BASE_PRIMARY = {
    "two_branch_barrier": ["left"],
    "race_two_trigger": ["right"],
    "three_level": ["mid", "leaf"],
    "three_step_retry_remedy": ["deploy"],
    "root_eligible_both": ["work"],
}
PLANTED_ANSWERS = "tests.tree.test_differ_d7:invariant_answer_of"

AnswerSet = dict[str, dict[str, Any]]


def _invariant_set() -> AnswerSet:
    """Every base and every GENERATED permutation/wrapping answers alike; a wrapped variant's
    primary path carries the wrapper's segment (one physical level deeper)."""
    answers: AnswerSet = {
        base: {"class": "FAILED", "primary": list(path), "noise": {"run": base}}
        for base, path in BASE_PRIMARY.items()
    }
    for variant in d7.variants():
        primary = list(BASE_PRIMARY[variant.base])
        if variant.wrapper is not None:
            primary.insert(len(primary) - 1, variant.wrapper)
        answers[variant.name] = {"class": "FAILED", "primary": primary, "noise": variant.name}
    return answers


def invariant_answer_of(name: str) -> Mapping[str, Any]:
    return _invariant_set()[name]


def _run(answers: AnswerSet, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    rc = d7.run(answers.__getitem__)
    return rc, capsys.readouterr().out


def test_variants_are_exactly_the_generated_permutations_and_wrappings() -> None:
    found = d7.variants()
    assert [v.name for v in found if v.kind == "permutation"] == [
        f"permute_{seed}" for seed in generators.PERMUTE_SEEDS
    ]
    assert [(v.base, v.wrapper) for v in found if v.kind == "wrapping"] == [
        (fixture, name) for fixture, _node, name in generators.WRAPPINGS
    ]
    assert [t.name for t in generators.GENERATED if t.name.startswith("hundred_node")]
    assert len(found) == len(generators.GENERATED) - 2  # the two hundred_node trees are scale-only
    assert {v.base for v in found} <= set(BASE_PRIMARY)


def test_d7_fails_on_planted_order_dependent_primary(capsys: pytest.CaptureFixture[str]) -> None:
    answers = _invariant_set()
    seed = generators.PERMUTE_SEEDS[2]
    answers[f"permute_{seed}"]["primary"] = ["right"]  # another declaration order, another primary
    rc, out = _run(answers, capsys)
    assert rc == 1
    assert f"permutation seed {seed}" in out
    assert "logical primary path" in out
    assert out.count("d7: DIFF") == 1  # only the dependent seed is named


def test_d7_fails_on_planted_depth_dependent_class(capsys: pytest.CaptureFixture[str]) -> None:
    answers = _invariant_set()
    fixture, _node, wrapper = generators.WRAPPINGS[1]
    answers[f"{fixture}__{wrapper}"]["class"] = "BLOCKED"  # one level deeper, another class
    rc, out = _run(answers, capsys)
    assert rc == 1
    assert f"wrapper {wrapper!r}" in out
    assert "root class" in out
    assert out.count("d7: DIFF") == 1


def test_d7_passes_on_planted_invariant_answers(capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = _run(_invariant_set(), capsys)
    assert rc == 0, out
    assert "0 diffs" in out


@pytest.mark.parametrize("field", ["class", "primary"])
def test_d7_names_a_wrapper_whose_answer_field_moves(
    field: str, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = _invariant_set()
    fixture, _node, wrapper = generators.WRAPPINGS[0]
    answers[f"{fixture}__{wrapper}"][field] = "REPAIRED" if field == "class" else ["elsewhere"]
    rc, out = _run(answers, capsys)
    assert rc == 1 and f"wrapper {wrapper!r}" in out


def test_d7_a_missing_answer_is_a_difference(capsys: pytest.CaptureFixture[str]) -> None:
    answers = _invariant_set()
    del answers["permute_1"]
    rc, out = _run(answers, capsys)
    assert rc == 1 and "permutation seed 1" in out and "no answer" in out
    base = d7.variants()[0].base
    answers = _invariant_set()
    del answers[base]
    rc, out = _run(answers, capsys)
    assert rc == 1 and "the base fixture has no answer" in out


def test_d7_only_class_and_primary_decide(capsys: pytest.CaptureFixture[str]) -> None:
    answers = _invariant_set()
    for answer in answers.values():
        answer["noise"] = object.__hash__  # anything else in the answer is ignored
    rc, _out = _run(answers, capsys)
    assert rc == 0


def test_mode_is_built_and_reached_from_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert "d7" not in differ.UNBUILT_MODES
    assert differ.main(["d7", "--answers", PLANTED_ANSWERS]) == 0
    out = capsys.readouterr().out
    assert "not built" not in out and "d7:" in out


def test_cli_without_the_product_answers_is_not_reported_as_not_built(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = differ.main(["d7", "--answers", "tests.tree.no_such_module:answer_of"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no answer source" in out and "not built" not in out


def test_cli_plants_a_dependent_source(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def dependent(name: str) -> Mapping[str, Any]:
        answer = dict(_invariant_set()[name])
        if name == "permute_4":
            answer["class"] = "PASSED"
        return answer

    module = __import__("tests.tree.test_differ_d7", fromlist=["x"])
    monkeypatch.setattr(module, "dependent_answer_of", dependent, raising=False)
    rc = differ.main(["d7", "--answers", "tests.tree.test_differ_d7:dependent_answer_of"])
    assert rc == 1 and "permutation seed 4" in capsys.readouterr().out
