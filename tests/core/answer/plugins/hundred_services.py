"""CL-B1 fixture plugin (SA-09): one vertex that answers about a hundred services and leaves
about ten MiB of logs behind it (console, events and artifacts), so a test can ask whether a large
one-call answer stays decisive and bounded.

`mode="pass"` returns the answer; `mode="fail"` leaves the same logs and then raises, naming the
first unhealthy service.
"""

from __future__ import annotations

import sys
from typing import TypedDict

from trestle.plugin.surface import Context, trestle

SERVICE_COUNT = 100
UNHEALTHY = 42
CONSOLE_LINES = 5 * 1024
CONSOLE_LINE_BYTES = 1024
EVENT_COUNT = 256
EVENT_BYTES = 8000
ARTIFACT_COUNT = 3
ARTIFACT_BYTES = 1024 * 1024


def service_name(index: int) -> str:
    return f"svc-{index:03d}"


class Service(TypedDict):
    name: str
    state: str
    latency_ms: int
    note: str


class Answer(TypedDict):
    verdict: str
    failed_services: list[str]
    service_count: int
    services: list[Service]


@trestle(summary_fields=("verdict", "failed_services", "service_count"))
def hundred_services(ctx: Context, mode: str = "pass") -> Answer:
    line = "c" * (CONSOLE_LINE_BYTES - 1) + "\n"
    for _ in range(CONSOLE_LINES):
        sys.stdout.write(line)
    sys.stdout.flush()
    for n in range(EVENT_COUNT):
        ctx.log(f"{n:04d} " + "e" * EVENT_BYTES)
    for n in range(ARTIFACT_COUNT):
        (ctx.outputs / f"blob{n}.bin").write_bytes(b"a" * ARTIFACT_BYTES)  # promoted at the end

    if mode == "fail":
        raise RuntimeError(f"{service_name(UNHEALTHY)} is unhealthy")
    services: list[Service] = [
        {"name": service_name(i), "state": "up", "latency_ms": 10 + i, "note": "n" * 40}
        for i in range(SERVICE_COUNT)
    ]
    return {
        "verdict": "pass",
        "failed_services": [],
        "service_count": SERVICE_COUNT,
        "services": services,
    }
