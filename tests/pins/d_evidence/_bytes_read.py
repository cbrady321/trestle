"""Operation-count oracle for lane D: how many bytes a call reads from one
file, counted at the Python I/O seams a reader can use (no wall clock).

Counts `builtins.open`/`io.open` in a read mode (which `Path.read_text`,
`Path.read_bytes` and `Path.open` all route through), for reads of the watched path only.
A reader that goes through `os.read`/`os.pread` directly is counted too.
"""

from __future__ import annotations

import builtins
import io
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class _Counter:
    def __init__(self) -> None:
        self.bytes_read = 0

    def add(self, data: object) -> None:
        if isinstance(data, (bytes, bytearray, str)):
            self.bytes_read += len(data.encode("utf-8") if isinstance(data, str) else data)


class _CountingFile:
    def __init__(self, fh: object, counter: _Counter) -> None:
        self._fh = fh
        self._counter = counter

    def read(self, *a: object, **k: object) -> object:
        data = self._fh.read(*a, **k)  # type: ignore[attr-defined]
        self._counter.add(data)
        return data

    def readline(self, *a: object, **k: object) -> object:
        data = self._fh.readline(*a, **k)  # type: ignore[attr-defined]
        self._counter.add(data)
        return data

    def readlines(self, *a: object, **k: object) -> object:
        lines = self._fh.readlines(*a, **k)  # type: ignore[attr-defined]
        for line in lines:
            self._counter.add(line)
        return lines

    def __iter__(self) -> Iterator[object]:
        for line in self._fh:  # type: ignore[attr-defined]
            self._counter.add(line)
            yield line

    def __enter__(self) -> _CountingFile:
        self._fh.__enter__()  # type: ignore[attr-defined]
        return self

    def __exit__(self, *exc: object) -> object:
        return self._fh.__exit__(*exc)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._fh, name)


def _is_read_mode(mode: str) -> bool:
    return "r" in mode or "+" in mode


@contextmanager
def count_bytes_read(watched: Path) -> Iterator[_Counter]:
    """Yield a counter of bytes read from `watched` while the block runs."""
    counter = _Counter()
    target = os.fspath(watched)
    real_open = builtins.open
    real_pread = os.pread
    real_read = os.read
    fd_paths: dict[int, str] = {}

    def is_watched(p: object) -> bool:
        try:
            return os.fspath(p) == target  # type: ignore[arg-type]
        except TypeError:
            return False

    def open_(file: object, mode: str = "r", *a: object, **k: object) -> object:
        fh = real_open(file, mode, *a, **k)  # type: ignore[call-overload]
        if is_watched(file) and _is_read_mode(mode):
            return _CountingFile(fh, counter)
        return fh

    def pread(fd: int, n: int, offset: int) -> bytes:
        data = real_pread(fd, n, offset)
        if fd_paths.get(fd) == target:
            counter.add(data)
        return data

    def read(fd: int, n: int) -> bytes:
        data = real_read(fd, n)
        if fd_paths.get(fd) == target:
            counter.add(data)
        return data

    real_os_open = os.open

    def os_open(path: object, *a: object, **k: object) -> int:
        fd = real_os_open(path, *a, **k)  # type: ignore[arg-type]
        if is_watched(path):
            fd_paths[fd] = target
        return fd

    builtins.open = open_  # type: ignore[assignment]
    io.open = open_  # type: ignore[assignment]
    os.pread = pread
    os.read = read
    os.open = os_open  # type: ignore[assignment]
    try:
        yield counter
    finally:
        builtins.open = real_open
        io.open = real_open  # type: ignore[assignment]
        os.pread = real_pread
        os.read = real_read
        os.open = real_os_open  # type: ignore[assignment]

