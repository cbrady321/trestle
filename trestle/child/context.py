"""Child runtime Context implementation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trestle.common import redact
from trestle.common.fsutil import append_ndjson, atomic_write
from trestle.common.limits import CaptureLimits, capture_limits


@dataclass
class _Tally:
    """What one (stream, limit) has dropped so far."""

    bytes_recorded: int = 0
    bytes_suppressed: int = 0
    drops: int = 0
    dirty: bool = False


class RuntimeContext:
    def __init__(
        self,
        *,
        work: Path,
        evidence: Path,
        deadline: datetime,
        events_path: Path,
        limits: CaptureLimits | None = None,
        scrubber: redact.Scrubber = redact.NO_SCRUB,
    ) -> None:
        self._scrubber = scrubber
        self._work = work
        self._evidence = evidence
        self.deadline = deadline
        self._events_path = events_path
        self._limits = limits or capture_limits()
        self._limits_path = evidence / "capture_limits.ndjson"
        self.tmp = work / "tmp"
        self.outputs = work / "outputs"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.outputs.mkdir(parents=True, exist_ok=True)
        (work / "artifact-staging").mkdir(parents=True, exist_ok=True)
        self._event_count = 0
        self._event_bytes = 0
        self._window_start = time.monotonic()
        self._window_count = 0
        self._artifact_count = 0
        self._artifact_bytes = 0
        # one marker line per (stream, limit) (WR-EVID-4): later drops of the same kind are
        # tallied here and folded into that line, so marker volume never grows with the drops
        self._tallies: dict[tuple[str, str], _Tally] = {}

    @property
    def cancelled(self) -> bool:
        return (self._work / "cancel.flag").exists()

    def log(self, message: str) -> None:
        self._emit("log", {"message": message})

    def progress(self, message: str, *, fraction: float | None = None) -> None:
        payload: dict[str, object] = {"message": message}
        if fraction is not None:
            payload["fraction"] = fraction
        self._emit("progress", payload)

    def artifact(self, name: str) -> Path:
        if ".." in name or name.startswith("/"):
            raise ValueError("artifact name traversal rejected")
        staging = self._work / "artifact-staging" / f"{name}.partial"
        staging.parent.mkdir(parents=True, exist_ok=True)
        return staging

    def attach(self, path: Path, *, name: str) -> str:
        """Copy `path` (under `work/`) into the run's evidence as an artifact and return its
        handle. The run's count and byte caps hold here (WR-EVID-6): an attachment that would
        pass either is not written, one marker is recorded for the limit, and the return is
        the empty string, so no handle is ever returned that does not fetch. A staged file
        (`ctx.artifact`) that is attached is moved, not left to be promoted a second time."""
        from trestle.common.ids import generate_artifact_id

        if not path.is_relative_to(self._work):
            raise ValueError("attach path must be under work/")
        data = path.read_bytes()
        size = len(data)
        data, refused = self._scrubber.data(data)
        if refused:  # MC-CORE-13: a binary holding a declared secret is never written
            self._record_limit("artifacts", redact.BINARY_LIMIT, 0, size)
            raise ValueError("attachment holds a declared secret and is binary: refused")
        if self._artifact_count >= self._limits.max_artifact_count:
            self._record_limit("artifacts", "max_artifact_count", 0, size)
            return ""
        if self._artifact_bytes + len(data) > self._limits.max_artifact_bytes:
            self._record_limit("artifacts", "max_artifact_bytes", 0, size)
            return ""
        art_id = generate_artifact_id()
        dest = self._evidence / "artifacts" / art_id
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self._artifact_count += 1
        self._artifact_bytes += len(data)
        append_ndjson(
            self._events_path,
            {
                "kind": "artifact_available",
                "artifact_id": art_id,
                "name": self._scrubber.text(name),
                "at": _now(),
            },
        )
        if path.is_relative_to(self._work / "artifact-staging"):
            path.unlink(missing_ok=True)
        return art_id

    def limits_markers(self) -> list[dict[str, object]]:
        self.flush_limits()
        if not self._limits_path.exists():
            return []
        markers: list[dict[str, object]] = []
        for line in self._limits_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            markers.append(json.loads(line))
        return markers

    def flush_limits(self) -> None:
        """Write the marker tallies into `capture_limits.ndjson`, one line per (stream, limit).
        The child's entry point calls it when the run ends, so the file carries the totals."""
        if not any(t.dirty for t in self._tallies.values()):
            return
        lines = [
            json.dumps(
                {
                    "stream": stream,
                    "limit": limit,
                    "bytes_recorded": tally.bytes_recorded,
                    "bytes_suppressed": tally.bytes_suppressed,
                },
                separators=(",", ":"),
            )
            for (stream, limit), tally in self._tallies.items()
        ]
        atomic_write(self._limits_path, ("\n".join(lines) + "\n").encode("utf-8"))
        for tally in self._tallies.values():
            tally.dirty = False

    def _emit(self, kind: str, payload: dict[str, object]) -> None:
        payload = self._scrubber.json(payload)  # before it is encoded or measured
        encoded = json.dumps({"kind": kind, "payload": payload}, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded) > self._limits.max_single_event_bytes:
            self._record_limit("events", "max_single_event_bytes", 0, len(encoded))
            return

        now = time.monotonic()
        if now - self._window_start >= 1.0:
            self._window_start = now
            self._window_count = 0
        if self._window_count >= self._limits.max_events_per_second:
            self._record_limit("events", "max_events_per_second", 0, len(encoded))
            return

        if self._event_count >= self._limits.max_event_count:
            self._record_limit("events", "max_event_count", 0, len(encoded))
            return
        if self._event_bytes + len(encoded) > self._limits.max_event_bytes:
            self._record_limit("events", "max_event_bytes", 0, len(encoded))
            return

        append_ndjson(
            self._events_path,
            {"kind": kind, "payload": payload, "at": _now()},
        )
        self._event_count += 1
        self._event_bytes += len(encoded)
        self._window_count += 1

    def _record_limit(
        self,
        stream: str,
        limit: str,
        bytes_recorded: int,
        bytes_suppressed: int,
    ) -> None:
        """Record that `limit` on `stream` dropped something. The first drop of a kind writes its
        marker line at once (a run killed after it still shows the limit); later drops only add to
        the tally, which is written back when the drop count doubles and when the run ends."""
        tally = self._tallies.setdefault((stream, limit), _Tally())
        tally.bytes_recorded += bytes_recorded
        tally.bytes_suppressed += bytes_suppressed
        tally.drops += 1
        tally.dirty = True
        if tally.drops & (tally.drops - 1) == 0:  # 1, 2, 4, 8, ...: a bounded number of rewrites
            self.flush_limits()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
