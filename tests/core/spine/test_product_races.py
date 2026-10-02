"""Product races P1, P2, P3, P5 and the severed-admission race (delivery-close RACES-REPORT).

Each test forces the one interleaving that broke the product, with events, not sleeps: a thread
is held at a named point while another actor does its whole step, then released. Before the
fixes each test fails on that interleaving every time; after them it passes every time.

- P1: `atomic_write` gave every writer of a path the same temporary, so two writers collided.
- P2: a snapshot's plugin.py was copied in place (seen part-written, and an interrupted copy was
  kept for good), and copied after the bytes it is named by were hashed.
- P3: registry refreshes ran unserialized: an older refresh could overwrite a newer one, and a
  plugin file removed mid-refresh raised out of it.
- P5: under `TRESTLE_TEST_LIMITS` a scan was cut by 500 ms of wall time, so what it returned
  depended on the machine's speed.
- Sever: a `run` call cancelled after its admit started left an admitted run nothing drove.
"""

from __future__ import annotations

import hashlib
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.proof import tolerances
from trestle.common import fsutil

WAIT_S = tolerances.JOIN_WAIT_S
# a plugin run's admission, first process and terminal row, as the sever tests elsewhere allow
RUN_WAIT_S = tolerances.JOIN_WAIT_S * 3

SOURCE_A = """\
from trestle.plugin.surface import Context, trestle


@trestle
def racer(ctx: Context, n: int = 1) -> dict[str, int]:
    return {"n": n}
"""

SOURCE_B = """\
from trestle.plugin.surface import Context, trestle


@trestle
def racer(ctx: Context, word: str = "b") -> dict[str, str]:
    return {"word": word}
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _GatedOs:
    """`os` for the module under test, except that `replace` onto a matching path first calls
    `on_replace` (which may wait at a gate, or raise as a crash would)."""

    def __init__(self, matches: Callable[[Path], bool], on_replace: Callable[[], None]) -> None:
        self._matches = matches
        self._on_replace = on_replace

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def replace(self, src: Any, dst: Any) -> None:
        if self._matches(Path(dst)):
            self._on_replace()
        os.replace(src, dst)


def _gate(reached: threading.Event, go: threading.Event) -> Callable[[], None]:
    def hold() -> None:
        reached.set()
        assert go.wait(WAIT_S), "the gate was never opened"

    return hold


# P1 ------------------------------------------------------------------------------------------


def test_p1_two_writers_of_one_path_both_land_whole(tmp_path: Path, monkeypatch) -> None:
    """Writer A has written and synced its temporary and not yet renamed it; writer B writes the
    same path start to finish. A's rename must still publish A's bytes, whole."""
    target = tmp_path / "state.json"
    reached, go = threading.Event(), threading.Event()
    held = _gate(reached, go)
    monkeypatch.setattr(
        fsutil,
        "os",
        _GatedOs(lambda _dst: threading.current_thread().name == "writer-a", held),
    )
    errors: list[BaseException] = []

    def write_a() -> None:
        try:
            fsutil.atomic_write(target, b"A" * 4096)
        except BaseException as exc:  # noqa: BLE001 - reported by the assert below
            errors.append(exc)

    writer_a = threading.Thread(target=write_a, name="writer-a")
    writer_a.start()
    assert reached.wait(WAIT_S), "writer A never reached its rename"
    fsutil.atomic_write(target, b"B" * 10)
    go.set()
    writer_a.join(WAIT_S)
    assert errors == []
    assert target.read_bytes() == b"A" * 4096  # the last rename wins, and it is whole
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]  # no temporary left


def test_p1_atomic_write_keeps_the_mode_an_open_gives(tmp_path: Path) -> None:
    """Unchanged contract: the file is created as `open(path, "wb")` would create it."""
    mask = os.umask(0o022)
    os.umask(mask)
    target = tmp_path / "f.json"
    fsutil.atomic_write(target, b"{}")
    assert stat.S_IMODE(target.stat().st_mode) == 0o666 & ~mask
