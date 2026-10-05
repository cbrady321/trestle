"""Operator health — registry reachability."""

from __future__ import annotations

from trestle.common.types import RequestOutcome
from trestle.server.main import Kernel


def read_health(kernel: Kernel) -> dict[str, object] | RequestOutcome:
    kernel.registry.maybe_refresh()
    catalog = kernel.control.list_plugins()
    return {
        "reachable": True,
        "registry_version": catalog.get("registry_version"),
        "plugin_count": len(catalog.get("items", [])),
    }
