"""The trusted catalog (MC-B-09; L.NW-3.1): `Catalog.load(path)` over closed identifier types."""

from __future__ import annotations

from pathlib import Path

from trestle_env.catalog.model import (
    CLOSED_TYPES,
    Arg,
    Catalog,
    CatalogError,
    Closed,
    EnvKey,
    Override,
    OverrideId,
    PinVersion,
    Project,
    ProjectId,
    SelectorId,
    Service,
    ServiceId,
    TaskEntry,
    TaskId,
    TestId,
    TestSpec,
    ToolName,
    ToolPin,
)

REFERENCE_PATH = Path(__file__).with_name("reference.json")


def load_reference() -> Catalog:
    """The reference catalog shipped with the package (`reference.json`)."""
    return Catalog.load(REFERENCE_PATH)


__all__ = [
    "CLOSED_TYPES",
    "REFERENCE_PATH",
    "Arg",
    "Catalog",
    "CatalogError",
    "Closed",
    "EnvKey",
    "Override",
    "OverrideId",
    "PinVersion",
    "Project",
    "ProjectId",
    "SelectorId",
    "Service",
    "ServiceId",
    "TaskEntry",
    "TaskId",
    "TestId",
    "TestSpec",
    "ToolName",
    "ToolPin",
    "load_reference",
]
