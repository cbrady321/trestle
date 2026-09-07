"""Child runtime Context implementation."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from trestle.common.fsutil import append_ndjson
from trestle.common.limits import CaptureLimits, capture_limits


class RuntimeContext:
    def __init__(
        self,
        *,
        work: Path,
        evidence: Path,
        deadline: datetime,
        events_path: Path,
        limits: CaptureLimits | None = None,
    ) -> None:
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
        from trestle.common.ids import generate_artifact_id

        if not path.is_relative_to(self._work):
            raise ValueError("attach path must be under work/")
        art_id = generate_artifact_id()
        dest = self._evidence / "artifacts" / art_id
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        append_ndjson(
            self._events_path,
            {"kind": "artifact_available", "artifact_id": art_id, "name": name, "at": _now()},
        )
        return art_id

    def limits_markers(self) -> list[dict[str, object]]:
        if not self._limits_path.exists():
            return []
        markers: list[dict[str, object]] = []
        for line in self._limits_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            markers.append(json.loads(line))
        return markers

    def _emit(self, kind: str, payload: dict[str, object]) -> None:
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
        append_ndjson(
            self._limits_path,
            {
                "stream": stream,
                "limit": limit,
                "bytes_recorded": bytes_recorded,
                "bytes_suppressed": bytes_suppressed,
            },
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
