"""Teardown policy types."""

from __future__ import annotations

from enum import StrEnum


class TeardownPolicy(StrEnum):
    """What to do with resources when a pack run finishes or fails."""

    DOWN = "down"
    STOP = "stop"
    NONE = "none"
