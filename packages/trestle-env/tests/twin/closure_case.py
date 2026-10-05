"""The started set against the Compose closure (L.RB-1.4; WR-ENV-1), shared by the HOST nodes in
`host/test_closure_host.py` and their twins in `twin/test_closure_host_twin.py`.

Both sides publish the unmodified reference plugin, name the SAME Compose definition through the
operator variable the composition root reads (`TRESTLE_ENV_COMPOSE_FILE`), make the same one MCP
call and read the same facts: which services the run STARTED (a vertex answered `started` whose
create the lane confirms applied, with the run's own selector) against the closure the binding's
own resolver derives for the same selection. Only the binding differs (`harness`, `fake_binding`).

The definition (`fixtures/closure-compose/compose.json`) is the reference stack's services and
edges written in JSON syntax, which both Docker Compose and the fake resolver read; it is a
definition only (nothing is ever started from it) and every object in it carries the fixture
label all the same.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.proof import mcp_host, tolerances

from trestle_env import tree
from trestle_env.plugins import _bind
from twin import harness

COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "closure-compose" / "compose.json"
PROJECT = _bind.REFERENCE_COMPOSE_PROJECT
SELECTED = [tree.POSTGRES_SERVICE]


def service_of(path: str) -> str:
    """The logical service a service node's path names: the service child is named by its catalog
    id (`tree.build_entry`), so the path IS the service (`postgres`)."""
    assert path and "/" not in path, f"{path!r} is not a service node's path"
    return path


def environ(base: dict[str, str | None]) -> dict[str, str | None]:
    return {**base, _bind.COMPOSE_ENV: str(COMPOSE)}


def call(host: mcp_host.McpHost, env: str, services: list[str] | None) -> dict[str, Any]:
    """The one call; `services=None` gives only the environment (a Compose-file-only request)."""
    args: dict[str, Any] = {"env": env}
    if services is not None:
        args["services"] = services
    sent = host.request_count()
    answer = host.call(
        "run",
        {
            "plugin": harness.PLUGIN_NAME,
            "args": args,
            "wait_ms": tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert host.request_count() == sent + 1, "one request, counted once"
    assert isinstance(answer, dict), answer
    return answer


def started(answer: dict[str, Any], entries: list[dict[str, Any]], run_id: str) -> frozenset:
    """The logical services this run started: answered `started`, and created by this run (the
    lane confirms the create applied, under the run's selector prefix)."""
    prefix = harness.selector_prefix(run_id)
    created = {
        str(e["path"]).split("/", 1)[0]  # a CHOICE's alternative stands for its service
        for e in entries
        if e["class"] == "confirmation"
        and e.get("effect") == tree.UP
        and e.get("status") == "applied"
        and str(e.get("identity", "")).startswith(prefix)
    }
    answered = {p for p, d in harness.dispositions(answer).items() if d == "started"}
    assert answered == created, (answered, created)
    return frozenset(service_of(p) for p in created)


def every_service() -> list[str]:
    return sorted(str(s.id) for s in tree.CATALOG.services)
