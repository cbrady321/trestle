"""Operator registry — ControlSurface.list_plugins / describe_plugin projection."""

from __future__ import annotations

from trestle.common.types import RequestOutcome
from trestle.server.main import Kernel


def iter_registry(kernel: Kernel) -> dict[str, object] | RequestOutcome:
    kernel.registry.maybe_refresh()
    return kernel.control.list_plugins()


def describe_registry_entry(
    kernel: Kernel,
    plugin_id: str,
) -> dict[str, object] | RequestOutcome:
    kernel.registry.maybe_refresh()
    return kernel.control.describe_plugin(plugin_id)
