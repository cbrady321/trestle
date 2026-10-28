"""R7b: `RuntimeContext` is safe to call from several threads. Every write path holds one lock,
so concurrent `log`, `event` and `attach` (including a same-name replace, T3) leave an
`events.ndjson` whose every line parses, with no row lost or merged, and counters that agree."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import trestle.child.context as context_mod
from trestle.child.context import RuntimeContext
from trestle.common.limits import CaptureLimits

THREADS = 8
ROUNDS = 40


def _make(tmp_path: Path) -> RuntimeContext:
    evidence = tmp_path / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        work=tmp_path / "work",
        evidence=evidence,
        deadline=datetime.now(UTC) + timedelta(seconds=60),
        events_path=evidence / "events.ndjson",
        limits=CaptureLimits(max_events_per_second=1_000_000),
    )


def _split_append(path: Path, record: dict[str, Any]) -> None:
    """An append that writes its line in two parts with a pause between them, as a slow or
    partial write would: without the context lock another thread's line lands in the gap."""
    line = json.dumps(record, separators=(",", ":")).encode() + b"\n"
    half = len(line) // 2
    with path.open("ab") as fh:
        fh.write(line[:half])
        fh.flush()
        time.sleep(0.0005)
        fh.write(line[half:])


def test_eight_threads_logging_and_attaching_leave_every_line_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context_mod, "append_ndjson", _split_append)
    ctx = _make(tmp_path)
    start = threading.Barrier(THREADS)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            start.wait()
            for i in range(ROUNDS):
                ctx.log(f"t{n} line {i} " + "x" * 200)
                ctx.event("tick", thread=n, i=i)
                if i % 10 == 0:
                    staged = ctx.tmp / f"t{n}-{i}.txt"
                    staged.write_text(f"{n}:{i}\n", encoding="utf-8")
                    ctx.attach(staged, name=f"status-{n}")  # same name each round: T3 replace
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []

    lines = (tmp_path / "evidence" / "events.ndjson").read_bytes().split(b"\n")
    assert lines[-1] == b""
    rows = [json.loads(line) for line in lines[:-1]]  # every line parses: nothing merged
    logs = [r for r in rows if r["kind"] == "log"]
    ticks = [r for r in rows if r["kind"] == "tick"]
    attached = [r for r in rows if r["kind"] == "artifact_available"]
    assert len(logs) == THREADS * ROUNDS
    assert len(ticks) == THREADS * ROUNDS
    assert len(attached) == THREADS * (ROUNDS // 10)
    seen = {(r["payload"]["thread"], r["payload"]["i"]) for r in ticks}
    assert len(seen) == THREADS * ROUNDS  # nothing lost

    # T3 under contention: one count slot per name, each replace naming the id it superseded
    assert ctx._artifact_count == THREADS
    for n in range(THREADS):
        chain = [r for r in attached if r["name"] == f"status-{n}"]
        assert "supersedes" not in chain[0]
        for prev, row in zip(chain, chain[1:], strict=False):
            assert row["supersedes"] == prev["artifact_id"]
