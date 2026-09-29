"""Per-node audit plugin: `pytest -p tests.proof.audit_plugin` writes one
JSON document to the path in `TRESTLE_AUDIT_OUT` (no new dependency).

`nodes` (every collected item, written at collection finish): nodeid,
the label ids its `proves()`/`stub_proven()`/compat markers name (as the
root plugin resolved them), whether it carries `host_only`, the gap of its
`target()` marker, the gap of its `pin()` marker (`pin_gap`), whether it
carries a strict xfail and whether it carries a `gated_on`/`na` reason.
`outcomes` (call-phase, written at session finish): nodeid, outcome, the
raised exception's class name and, for `TargetUnmet`, the gap it names.
Used by J0-2 (red-reason audit; L.P0-0d.8) and by CM-7's register rule
(`register.py`: is a label's registrant a strict-xfail target?).
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tests.proof import plugin as plugin_mod

_NODES: list[dict[str, Any]] = []
_OUTCOMES: dict[str, dict[str, Any]] = {}


def _target_gap(item: pytest.Item) -> str | None:
    mark = item.get_closest_marker("target")
    if mark is None:
        return None
    return mark.args[0] if mark.args else mark.kwargs.get("gap")


def _pin_gap(item: pytest.Item) -> str | None:
    mark = item.get_closest_marker("pin")
    if mark is None:
        return None
    return mark.args[0] if mark.args else mark.kwargs.get("gap")


def pytest_collection_finish(session: pytest.Session) -> None:
    _NODES.clear()
    for item in session.items:
        _NODES.append(
            {
                "nodeid": item.nodeid,
                "labels": list(plugin_mod._ITEM_LABELS.get(item.nodeid, [])),
                "host_only": item.get_closest_marker("host_only") is not None,
                "gap": _target_gap(item),
                "pin_gap": _pin_gap(item),
                "strict_xfail": any(
                    m.kwargs.get("strict") for m in item.iter_markers(name="xfail")
                ),
                "has_reason": bool(
                    list(item.iter_markers(name="gated_on")) or list(item.iter_markers(name="na"))
                ),
            }
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    outcome = yield
    report = outcome.get_result()
    if call.when == "call" or (call.when == "setup" and not report.passed):
        entry: dict[str, Any] = {"outcome": report.outcome, "exc_type": None, "unmet_gap": None}
        if hasattr(report, "wasxfail"):
            # pytest reports an xfail as `skipped` and a non-strict xpass as
            # `passed`; name them, so a HOST record never reads a red target
            # as a skip (CM-6's pass set allows XFAIL, refuses XPASS).
            entry["outcome"] = "xfailed" if report.skipped else "xpassed"
        if call.excinfo is not None:
            entry["exc_type"] = call.excinfo.type.__name__
            entry["unmet_gap"] = getattr(call.excinfo.value, "gap", None)
        _OUTCOMES[item.nodeid] = entry


def pytest_sessionfinish(session: pytest.Session) -> None:
    out = os.environ.get("TRESTLE_AUDIT_OUT")
    if not out:
        return
    with open(out, "w") as fh:
        json.dump({"nodes": _NODES, "outcomes": _OUTCOMES}, fh)
