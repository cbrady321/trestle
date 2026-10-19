"""MCP progress notifications while a call waits on runs (T2).

A waiting `run` (`wait_ms > 0` or `completion="terminal"`) or `await_runs` hands each poll's run
views to a `ProgressRelay`. The relay sends one notification for each new progress line it sees
(the newest at each poll: lines written between two polls are not each sent), and a heartbeat
after `HEARTBEAT_S` with nothing sent, so a client's stream stays alive through a long silent
stretch. The notification's `progress` is a counter, since MCP requires it to increase; the line
itself is the `message`. The server builds a relay only for a request that carried a progress
token: without one, a wait sends nothing."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from trestle.common.types import RunView

HEARTBEAT_S = 15.0

Send = Callable[[float, str], Awaitable[None]]


class ProgressRelay:
    def __init__(
        self,
        send: Send,
        *,
        heartbeat_s: float = HEARTBEAT_S,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._send = send
        self._heartbeat_s = heartbeat_s
        self._now = now
        self._seen: dict[str, tuple[Any, ...]] = {}
        self._sent = 0
        self._last_sent = now()

    async def __call__(self, views: list[RunView]) -> None:
        for view in views:
            progress = view.progress
            if progress is None:
                continue
            key = (progress.get("message"), progress.get("fraction"), progress.get("at"))
            if self._seen.get(view.run_id) == key:
                continue
            self._seen[view.run_id] = key
            await self._notify(_progress_line(view.run_id, progress))
        if self._now() - self._last_sent >= self._heartbeat_s:
            waiting = ", ".join(f"{view.run_id} {view.state}" for view in views)
            await self._notify(f"waiting: {waiting}")

    async def _notify(self, message: str) -> None:
        self._sent += 1
        self._last_sent = self._now()
        try:
            await self._send(float(self._sent), message)
        except Exception:  # noqa: BLE001 - a notification lost never ends the wait or its answer
            pass


def _progress_line(run_id: str, progress: dict[str, Any]) -> str:
    fraction = progress.get("fraction")
    suffix = f" ({fraction:.0%})" if isinstance(fraction, int | float) else ""
    return f"{run_id}: {progress.get('message', '')}{suffix}"
