"""SA-09 in core (L.CL-B1.2): the one-vertex hundred-service fixture stands in for a
hundred-node answer only while it is as large as one. This drift check measures the fixture
itself, without running a child: it holds a hundred services, about ten MiB across console, events
and artifacts, and a result several times larger than any summary budget, so the answer it
produces must be cut down to its decisive fields. SA-09 retires when the tree band (TR-6) replaces
this fixture with a real hundred-node tree."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.core.answer.plugins import hundred_services
from trestle.server.project import DEFAULT_SUMMARY_BUDGET

MIB = 1024 * 1024


class _Ctx:
    """The parts of `Context` the fixture touches, counting what it writes."""

    def __init__(self, root: Path) -> None:
        self.tmp = root / "tmp"
        self.outputs = root / "outputs"
        self.tmp.mkdir()
        self.outputs.mkdir()
        self.cancelled = False
        self.deadline = datetime.now(UTC)
        self.event_bytes = 0

    def log(self, message: str) -> None:
        self.event_bytes += len(message.encode("utf-8"))

    def progress(self, message: str, *, fraction: float | None = None) -> None:
        self.event_bytes += len(message.encode("utf-8"))

    def artifact(self, name: str) -> Path:
        return self.tmp / name

    def attach(self, path: Path, *, name: str) -> str:
        return name


@pytest.mark.parametrize("sa", ["SA-09"])
def test_fixture_is_as_large_as_a_hundred_service_answer(sa: str, tmp_path: Path) -> None:
    ctx = _Ctx(tmp_path)
    console = io.StringIO()
    with redirect_stdout(console):
        result = hundred_services.hundred_services(ctx, mode="pass")  # type: ignore[arg-type]
    console_bytes = len(console.getvalue().encode("utf-8"))
    artifact_bytes = sum(p.stat().st_size for p in ctx.outputs.iterdir())

    assert hundred_services.SERVICE_COUNT == 100
    assert len(result["services"]) == 100
    assert len({service["name"] for service in result["services"]}) == 100
    # about ten MiB of logs, spread across all three streams
    assert min(console_bytes, ctx.event_bytes, artifact_bytes) > MIB
    assert 9 * MIB <= console_bytes + ctx.event_bytes + artifact_bytes <= 12 * MIB
    # the result cannot fit any summary budget whole, but its decisive fields fit beside each other
    result_bytes = len(json.dumps(result, separators=(",", ":")).encode("utf-8"))
    decisive = {k: result[k] for k in ("verdict", "failed_services", "service_count")}
    decisive_bytes = len(json.dumps(decisive, separators=(",", ":")).encode("utf-8"))
    assert result_bytes > 2 * DEFAULT_SUMMARY_BUDGET
    assert decisive_bytes < DEFAULT_SUMMARY_BUDGET // 8
