"""The `foundations` marker (L.SV-3.1; A1c2-8).

P0's `addopts` carries `--strict-markers` and registers no `foundations` marker, so this
conftest registers it (`pytest_configure`) and applies it to every item collected under
`tests/proof/foundations/`. `pytest tests/proof/foundations -m foundations` therefore selects
exactly the items an unfiltered run collects. These property suites are shape evidence (J-SINGLE
condition e): none carries a `proves` marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "foundations: pure property suites over the plan compiler, carving, ordinal and "
        "precedence modules (MC-20, MC-22, MC-23; shape evidence, no proves marker)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.path)).resolve().is_relative_to(_HERE):
            item.add_marker(pytest.mark.foundations)
