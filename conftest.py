"""Root conftest (MC-01, MC-33). Loads the proof plugin for every session.

The packs-only session must be invoked as
`python -m pytest -q -c pyproject.toml --rootdir . packages/trestle-packs/tests`
(CSC-12): passing the root `pyproject.toml` explicitly, with `--rootdir .`,
keeps this file's directory the pytest rootdir so this conftest (and the
plugin it loads) is still picked up, instead of the packs subpackage's own
`pyproject.toml` becoming rootdir and skipping it.
"""

from __future__ import annotations

pytest_plugins = ("tests.proof.plugin",)
