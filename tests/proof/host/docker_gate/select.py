"""The host-docker run set (L.NW-2.10; MC-B-02 / MC-B-03 selection half).

A pytest plugin ONLY `docker_gate run` loads (`-p tests.proof.host.docker_gate.select`); it is not a
deselection hook (the root proof plugin's one CSC-9 hook deselects `docker_host` nodes outside the
gate) and no conftest loads it. The runner hands it three environment values:

* `TRESTLE_GATE_SELECT`   JSON list of node ids passed with `--select`;
* `TRESTLE_GATE_MARKEXPR` a `-m` marker expression taken from the `--` pytest args (the runner
  never passes it to pytest's own `-m`, which would drop an explicitly selected id);

and the plugin keeps an item iff it carries `docker_host`, or its node id (or a prefix of it: a
file, a function, all its parametrizations) was selected, or it matches the marker expression:
the run set is every `docker_host` node UNION every `--select` id (CSC-5).

Inside the gate a skip means FAILED (`TRESTLE_HOST_GATE=docker`): a skipped `docker_host` node is
not a pass. The exception is a node the registry allows to skip (`gated_on` / `na`, CM-6's pass
set). The conversion happens in `pytest_runtest_makereport`, innermost, so every later reader
(the audit plugin, the terminal, the exit status) sees `failed`.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

SELECT_ENV = "TRESTLE_GATE_SELECT"
MARKEXPR_ENV = "TRESTLE_GATE_MARKEXPR"
GATE_ENV = "TRESTLE_HOST_GATE"

SKIP_IN_GATE = "a skip inside the docker gate is FAILED (docker_gate run)"


def selected_ids() -> list[str]:
    raw = os.environ.get(SELECT_ENV)
    return [str(x) for x in json.loads(raw)] if raw else []


def _id_selected(nodeid: str, selected: list[str]) -> bool:
    for sel in selected:
        if nodeid == sel or nodeid.startswith((sel + "::", sel + "[")):
            return True
    return False


class _Matcher:
    def __init__(self, item: pytest.Item) -> None:
        self.names = {m.name for m in item.iter_markers()}

    def __call__(self, name: str, /, **kwargs: Any) -> bool:
        return name in self.names


def _compile(expr: str | None) -> Any:
    if not expr:
        return None
    from _pytest.mark.expression import Expression

    return Expression.compile(expr)


def keep_item(item: pytest.Item, selected: list[str], expression: Any) -> bool:
    if item.get_closest_marker("docker_host") is not None:
        return True
    if _id_selected(item.nodeid, selected):
        return True
    return bool(expression is not None and expression.evaluate(_Matcher(item)))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    selected = selected_ids()
    expression = _compile(os.environ.get(MARKEXPR_ENV))
    kept = [item for item in items if keep_item(item, selected, expression)]
    dropped = [item for item in items if item not in kept]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    outcome = yield
    if os.environ.get(GATE_ENV) != "docker":
        return
    report = outcome.get_result()
    if not report.skipped or hasattr(report, "wasxfail"):
        return  # not a skip (an xfail is reported as skipped by pytest, and is not one)
    if item.get_closest_marker("gated_on") or item.get_closest_marker("na"):
        return
    report.outcome = "failed"
    report.longrepr = SKIP_IN_GATE
