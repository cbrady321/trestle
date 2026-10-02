"""Default capture and scan limits (R-LIM-1, R-FET-9)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureLimits:
    max_result_bytes: int = 256 * 1024 * 1024
    max_console_bytes: int = 64 * 1024 * 1024
    max_event_count: int = 100_000
    max_event_bytes: int = 64 * 1024 * 1024
    max_events_per_second: int = 5_000
    max_single_event_bytes: int = 64 * 1024
    max_artifact_count: int = 1_000
    max_artifact_bytes: int = 2 * 1024 * 1024 * 1024
    max_scan_bytes: int = 64 * 1024 * 1024
    max_scan_time_ms: int = 2_000

    @classmethod
    def from_env(cls) -> CaptureLimits:
        if os.environ.get("TRESTLE_TEST_LIMITS") == "1":
            return cls(
                max_result_bytes=512 * 1024,
                max_console_bytes=4096,
                max_event_count=50,
                max_event_bytes=4096,
                max_events_per_second=100,
                max_single_event_bytes=512,
                max_artifact_count=10,
                max_artifact_bytes=64 * 1024,
                max_scan_bytes=256 * 1024,
                max_scan_time_ms=500,
            )
        return cls()


def capture_limits() -> CaptureLimits:
    return CaptureLimits.from_env()
