"""Process-ancestry snapshot tool (L.P0-0b.4; MC-13 ancestry).

`snapshot()` enumerates every process the current user can see. `pid`,
`ppid` and `argv` come from one `ps` call (`ps` gives no reliable session
id on every platform this runs on — its macOS `sess` column reads back 0
for a non-root user on this host, so `pgid`/`sid` are read directly via
the stdlib `os.getpgid`/`os.getsid` instead, one call per pid, still no
model-client or third-party dependency).

`start` is a numeric, zone-free process start time, read with the stdlib
(plus `ctypes` against a public libc/libproc symbol — never a subprocess
whose text output could be zone- or locale-dependent):

- Linux: `/proc/<pid>/stat` field 22, clock ticks since boot.
- Darwin: `libproc`'s `proc_pidinfo(PROC_PIDTBSDINFO)`, whose
  `proc_bsdinfo.pbi_start_tvsec`/`pbi_start_tvusec` give microseconds
  since the epoch (the same numeric quantity `kinfo_proc.kp_proc.p_starttime`
  holds; `proc_pidinfo` is the documented, ABI-stable public accessor for
  it, so this reads that field via libproc rather than hand-rolling the
  `sysctl(CTL_KERN, KERN_PROC, KERN_PROC_PID, pid)` struct layout — noted
  as the plan-gap it is in the delivery return).

`start` is never parsed from `ps`'s own start-time text (`lstart`/`etime`
are zone- and locale-dependent); `ps` is asked for none of those columns.

Attribution (V-2.3) closes over three independent signals plus a caller
argv marker: a candidate is attributed to a root process if it shares the
root's pgid, shares the root's sid, its argv contains the marker, or it is
a ppid-descendant (recursively) of an already-attributed process —
whatever session or group it later moved to.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

PS_FIELDS = "pid=,ppid=,args="


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    pgid: int | None
    sid: int | None
    argv: str
    start: int | None


def _safe_getpgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None


def _safe_getsid(pid: int) -> int | None:
    try:
        return os.getsid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None


def parse_linux_stat_start(raw: str) -> int | None:
    """Field 22 of a `/proc/<pid>/stat` line (clock ticks since boot).
    `comm` (field 2) can contain spaces or parentheses, so skip to the
    last `)` before splitting on whitespace; `state` (field 3) is then
    `fields[0]`, making field 22 `fields[19]`."""
    close = raw.rfind(")")
    if close == -1:
        return None
    fields = raw[close + 1 :].split()
    if len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def parse_linux_cmdline(raw: bytes) -> str:
    """A `/proc/<pid>/cmdline` blob (NUL-separated, NUL-terminated) as one
    space-joined string, the shape `ps args=` prints. Empty for a kernel
    thread or a zombie."""
    return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


def _start_time_linux(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    return parse_linux_stat_start(raw)


def _argv_linux(pid: int) -> str:
    """The full, untruncated argv from `/proc/<pid>/cmdline`, or `""` when
    it cannot be read (gone, permission denied, kernel thread)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return parse_linux_cmdline(fh.read())
    except OSError:
        return ""


_MAXCOMLEN = 16
_PROC_PIDTBSDINFO = 3


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


_libproc: ctypes.CDLL | None = None


def _get_libproc() -> ctypes.CDLL | None:
    global _libproc
    if _libproc is None:
        path = ctypes.util.find_library("proc")
        if path is None:
            return None
        _libproc = ctypes.CDLL(path)
    return _libproc


def _start_time_darwin(pid: int) -> int | None:
    lib = _get_libproc()
    if lib is None:
        return None
    info = _ProcBsdInfo()
    size = lib.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size != ctypes.sizeof(info):
        return None
    return info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec


def start_time(pid: int) -> int | None:
    """A numeric, zone-free start time for `pid`, or `None` if it cannot
    be read (the process is gone, or this platform is unsupported)."""
    if sys.platform == "darwin":
        return _start_time_darwin(pid)
    if sys.platform.startswith("linux"):
        return _start_time_linux(pid)
    return None


def _ps_rows() -> list[tuple[int, int, str]]:
    proc = subprocess.run(
        # `-ww`: never truncate `args` to the terminal width. Without it a
        # non-tty procps `ps` cuts each line at 80 columns, which hides a
        # trailing argv marker on Linux.
        ["ps", "-ww", "-eo", PS_FIELDS],
        capture_output=True,
        text=True,
        check=False,
    )
    rows: list[tuple[int, int, str]] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        argv = parts[2] if len(parts) > 2 else ""
        rows.append((pid, ppid, argv))
    return rows


def snapshot() -> set[ProcInfo]:
    """A snapshot of every process the current user can see, taken now
    (call again after spawn, at each poll, and immediately before sending
    a signal — V-2.3)."""
    result: set[ProcInfo] = set()
    linux = sys.platform.startswith("linux")
    for pid, ppid, argv in _ps_rows():
        if linux:
            argv = _argv_linux(pid) or argv
        result.add(
            ProcInfo(
                pid=pid,
                ppid=ppid,
                pgid=_safe_getpgid(pid),
                sid=_safe_getsid(pid),
                argv=argv,
                start=start_time(pid),
            )
        )
    return result


def attribute(
    root: ProcInfo, candidates: set[ProcInfo], *, marker: str | None = None
) -> set[ProcInfo]:
    """Every candidate attributed to `root` by descent, pgid, sid, or a
    caller argv marker (V-2.3), closed over ppid-descendants of an
    already-attributed process regardless of the session or group it
    later moved to."""
    by_pid = {p.pid: p for p in candidates}
    by_pid[root.pid] = root

    attributed: dict[int, ProcInfo] = {root.pid: root}
    changed = True
    while changed:
        changed = False
        for pid, proc in by_pid.items():
            if pid in attributed:
                continue
            direct = (
                (root.pgid is not None and proc.pgid == root.pgid)
                or (root.sid is not None and proc.sid == root.sid)
                or (marker is not None and marker in proc.argv)
            )
            descended = proc.ppid in attributed
            if direct or descended:
                attributed[pid] = proc
                changed = True

    attributed.pop(root.pid, None)
    return {p for p in attributed.values() if p in candidates}


def survivors(before: set[ProcInfo], after: set[ProcInfo]) -> set[ProcInfo]:
    """The subset of `before` still alive in `after` (matched by pid and
    start, so a reused pid is never mistaken for a survivor)."""
    after_keys = {(p.pid, p.start) for p in after}
    return {p for p in before if (p.pid, p.start) in after_keys}


def reap(procs: set[ProcInfo]) -> None:
    """SIGKILL every process in `procs`, verifying its start time first so
    a reused pid is never signalled by mistake."""
    for proc in procs:
        current = start_time(proc.pid)
        if current is not None and proc.start is not None and current != proc.start:
            continue  # pid was reused; not the process we meant to reap
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _cli_survivors(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tests.proof.ancestry survivors")
    parser.add_argument("--marker", default=None)
    args = parser.parse_args(argv)

    procs = snapshot()
    if args.marker is not None:
        procs = {p for p in procs if args.marker in p.argv}
    else:
        procs = set()

    for proc in sorted(procs, key=lambda p: p.pid):
        print(f"{proc.pid}\t{proc.argv}")
    return 0 if not procs else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "survivors":
        print("usage: python -m tests.proof.ancestry survivors [--marker <m>]", file=sys.stderr)
        return 2
    return _cli_survivors(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
