"""SA-07 drift proof: the structural normalizer is idempotent and total
over every JSON-safe value a facet extractor can return (L.P0-0c.4)."""

from __future__ import annotations

import pytest

from tests.proof import normalize


@pytest.mark.parametrize("sa", ["SA-07"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        1.5,
        "s",
        [],
        {},
        {"b": 1, "a": 2},
        {"z": [3, 2, 1], "a": {"y": 1, "x": 2}},
        [{"b": 1, "a": 2}, {"d": 1, "c": 2}],
    ],
)
def test_normalizer_idempotent_and_total(sa: str, value: object) -> None:
    once = normalize.normalize(value)
    twice = normalize.normalize(once)
    assert once == twice
