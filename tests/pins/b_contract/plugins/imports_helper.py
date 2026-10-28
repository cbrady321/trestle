"""G-B5 fixture: the plugin's behavior depends on an imported module that
lives outside plugin.py (`b5_helper`, put on PYTHONPATH by the test)."""

from __future__ import annotations

import importlib

from trestle.plugin.surface import Context, trestle


@trestle
def imports_helper(ctx: Context) -> dict[str, str]:
    helper = importlib.import_module("b5_helper")
    return {"value": str(helper.VALUE)}
