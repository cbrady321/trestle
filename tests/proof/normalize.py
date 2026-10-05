"""Structural normalizer for facet extracts (L.P0-0c.4, SA-07).

A facet extractor returns a JSON-safe value (dict/list/scalar) describing
one public observable (e.g. `tools/list`'s shape). `normalize()` puts that
value into one canonical, idempotent, total form so two structurally equal
values compare equal regardless of dict key insertion order — and nothing
else. It never renames or drops a key, never reorders a list (list order is
part of the contract for some facets, e.g. `tools_list`), and never
collapses two different values to the same normal form.
"""

from __future__ import annotations

from typing import Any


def normalize(value: Any) -> Any:
    """Idempotent, total: `normalize(normalize(x)) == normalize(x)` for
    every JSON-safe `x`, and `normalize` is defined for every such `x`
    (dict, list, str, int, float, bool, None)."""
    if isinstance(value, dict):
        return {k: normalize(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def structural_diff(
    golden: Any, current: Any, *, policy: str = "named"
) -> tuple[list[str], list[str]]:
    """Bidirectional structural diff between a facet's golden value and its
    current extract, both already `normalize()`d.

    Returns `(missing, unexpected)`: `missing` names every golden key/path
    absent or changed in `current` (always a failure, on both policies —
    a public observable never silently loses a value); `unexpected` names
    every key/path in `current` not present in `golden` (a failure only
    under `policy="named"` — a "free" facet, e.g. an additive ledger-kind
    enumeration, may grow without a divergence entry; a "named" facet, e.g.
    `tools_list`, may not).
    """
    missing: list[str] = []
    unexpected: list[str] = []
    _diff(golden, current, "$", missing, unexpected)
    if policy == "free":
        unexpected = []
    return missing, unexpected


def _diff(golden: Any, current: Any, path: str, missing: list[str], unexpected: list[str]) -> None:
    if isinstance(golden, dict) and isinstance(current, dict):
        for key in golden:
            child_path = f"{path}.{key}"
            if key not in current:
                missing.append(child_path)
            else:
                _diff(golden[key], current[key], child_path, missing, unexpected)
        for key in current:
            if key not in golden:
                unexpected.append(f"{path}.{key}")
        return
    if isinstance(golden, list) and isinstance(current, list):
        for i, item in enumerate(golden):
            child_path = f"{path}[{i}]"
            if i >= len(current):
                missing.append(child_path)
            else:
                _diff(item, current[i], child_path, missing, unexpected)
        for i in range(len(golden), len(current)):
            unexpected.append(f"{path}[{i}]")
        return
    if golden != current:
        missing.append(path)
