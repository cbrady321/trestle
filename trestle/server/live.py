"""What a run's evidence says while it is still in flight: its latest progress line and the
artifacts the plugin attached (T1, T3). Both are read from `events.ndjson`, which the child
appends to as the run goes, so they need no finalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# how far back from the end of `events.ndjson` the latest progress line is looked for: a run that
# has written this much since its last `ctx.progress` shows none until it reports again
PROGRESS_TAIL_BYTES = 64 * 1024


def _events_path(evidence: Path) -> Path:
    return evidence / "events.ndjson"


def latest_progress(
    evidence: Path, *, tail_bytes: int = PROGRESS_TAIL_BYTES
) -> dict[str, Any] | None:
    """The run's newest `progress` event as `{message, fraction, at}` (`fraction` None when the
    plugin gave none), or None. Only the last `tail_bytes` of the file are read."""
    path = _events_path(evidence)
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            start = max(size - tail_bytes, 0)
            handle.seek(start)
            chunk = handle.read()
    except OSError:
        return None
    lines = chunk.split(b"\n")
    if start > 0:
        lines = lines[1:]  # the first line of a tail read may be cut
    for raw in reversed(lines):
        if b'"progress"' not in raw:
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue  # a line the child is still writing
        if not isinstance(event, dict) or event.get("kind") != "progress":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        fraction = payload.get("fraction")
        return {
            "message": str(payload.get("message", "")),
            "fraction": fraction if isinstance(fraction, int | float) else None,
            "at": str(event.get("at", "")),
        }
    return None


def attached_artifacts(evidence: Path) -> list[dict[str, str]]:
    """Every `ctx.attach` the run recorded, in order: `{artifact_id, name, supersedes}`, where
    `supersedes` is the artifact id an attach under the same name replaced ("" for a first)."""
    path = _events_path(evidence)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if '"artifact_available"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "artifact_available":
            continue
        rows.append(
            {
                "artifact_id": str(event.get("artifact_id", "")),
                "name": str(event.get("name", "")),
                "supersedes": str(event.get("supersedes") or ""),
            }
        )
    return rows


def superseded_ids(evidence: Path) -> set[str]:
    """The artifact ids a later attach under the same name replaced: still stored and fetchable,
    but not a current artifact, so they hold no slot of the run's artifact count."""
    return {row["supersedes"] for row in attached_artifacts(evidence) if row["supersedes"]}
