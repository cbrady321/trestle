"""Process identity for the local-process port (L.SL-3.3; B3-C1, MC-14, DM-66, WR-OWN-7).

A process is identified by `(pid, start)`: `start` is a numeric, zone-free start time (Linux
`/proc/<pid>/stat` field 22, clock ticks since boot; darwin `proc_pidinfo(PROC_PIDTBSDINFO)`,
microseconds since the epoch) compared as an integer. It is never `ps`'s text `lstart`, which is
zone- and locale-dependent, and never port occupancy. A live pid whose start differs from the one
recorded is a reused pid and is never treated as the recorded process.

This module reads what MC-14 reads (`trestle.server.procident`), from its own copy: a pack imports
only the standard library and `trestle.workflow` (BFD-47, N7), and `trestle.server` is host code.
The process listing (for found instances) is one `ps -axww -o pid=,args=` call: `ps` gives the
pid and the full argument text only, never a start time.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import os
import subprocess
import sys
from typing import Any

_MAXCOMLEN = 16
_PROC_PIDTBSDINFO = 3
_SZOMB = 5


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * _MAXCOMLEN),
        ("pbi_name", ctypes.c_char * (2 * _MAXCOMLEN)),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


@functools.lru_cache(maxsize=1)
def _libproc() -> Any:
    path = ctypes.util.find_library("proc")
    return ctypes.CDLL(path) if path else None


def _darwin_start(pid: int) -> int | None:
    lib = _libproc()
    if lib is None:
        return None
    info = _ProcBsdInfo()
    size = lib.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size != ctypes.sizeof(info) or info.pbi_status == _SZOMB:  # a zombie counts as gone
        return None
    return int(info.pbi_start_tvsec) * 1_000_000 + int(info.pbi_start_tvusec)


def _linux_start(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    close = raw.rfind(")")  # `comm` may hold spaces and parentheses: split after the last `)`
    fields = raw[close + 1 :].split() if close != -1 else []
    if len(fields) < 20 or fields[0] in ("Z", "X"):
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def start_time(pid: int) -> int | None:
    """The numeric start time of `pid`, or None when it is gone (or cannot be read)."""
    if sys.platform == "darwin":
        return _darwin_start(pid)
    if sys.platform.startswith("linux"):
        return _linux_start(pid)
    return None


def listing() -> list[tuple[int, str]] | None:
    """`(pid, args text)` of every process the user can see, or None when `ps` cannot be run."""
    try:
        done = subprocess.run(
            ["ps", "-axww", "-o", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    rows: list[tuple[int, str]] = []
    for line in done.stdout.splitlines():
        head, _, args = line.strip().partition(" ")
        if head.isdigit() and int(head) != os.getpid():
            rows.append((int(head), args.strip()))
    return rows
