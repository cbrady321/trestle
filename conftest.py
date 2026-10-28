"""Root conftest (MC-01, MC-33). Loads the proof plugin for every session.

The packs-only session must be invoked as
`python -m pytest -q -c pyproject.toml --rootdir . packages/trestle-packs/tests`
(CSC-12): passing the root `pyproject.toml` explicitly, with `--rootdir .`,
keeps this file's directory the pytest rootdir so this conftest (and the
plugin it loads) is still picked up, instead of the packs subpackage's own
`pyproject.toml` becoming rootdir and skipping it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# The bundled packs' libraries (`packages/trestle-pack-<name>/trestle_pack_<name>`) are imported
# from this checkout, ahead of any installed copy, by this session and by the processes it starts
# (plugin validation and runs inherit PYTHONPATH), so a session needs no install of them and never
# tests a stale one. `tests` stays this checkout's package: a regular package wins over the packs'
# `tests/` folders, which have no `__init__.py`.
_PACKS = [
    str(pack)
    for pack in sorted((Path(__file__).resolve().parent / "packages").glob("trestle-pack-*"))
    if (pack / "pyproject.toml").is_file()
]
for _pack in reversed(_PACKS):
    if _pack not in sys.path:
        sys.path.insert(0, _pack)
_inherited = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
os.environ["PYTHONPATH"] = os.pathsep.join(
    [*_inherited, *(p for p in _PACKS if p not in _inherited)]
)

pytest_plugins = ("tests.proof.plugin", "pytester")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """`typed_records` nodes need K-3/K-4 (dataclass arguments and returns). On a tree where
    typed records are off, the CK-3/4 decline drill's, they are skipped: the bundled packs'
    wrappers take and return dataclasses, so they cannot run there."""
    from trestle.plugin import _codec

    if getattr(_codec, "TYPED_RECORDS", False):
        return
    skip = pytest.mark.skip(reason="typed records are off (K-3/K-4 declined)")
    for item in items:
        if item.get_closest_marker("typed_records") is not None:
            item.add_marker(skip)
