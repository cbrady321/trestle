"""The shared home (v0.4 Problem A): N server processes on one `TRESTLE_HOME`.

What lives here: the home's format and local-mount checks every entry point runs first; the
admission lock (`locks/admission.lock`, taken non-blocking for at most 2 s, its holder named in
`locks/admission.holder`) and the process-wide `admission_mutex` around it; the owner lock of each
run (`evidence/owner.lock`, held by the server that admitted it until its terminal row); the live
markers (`home/live/<run_id>`, the reaper's index); the server lock files
(`home/servers/<server_id>.lock`); and `Ownership`, a server's own set of owner locks.

Every lock is a `flock` on a descriptor this module opens fresh for that acquisition or probe,
never `fcntl` (a per-process lock that any close drops). A probe closes its descriptor at once, so
it never mistakes the caller's own lock for a free one. Lock order: `admission_mutex`, then
`locks/admission.lock`, then owner and server locks; owner and server locks are only ever taken
non-blocking, so holding one while waiting for the admission lock cannot deadlock.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import fcntl
import json
import os
import shutil
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trestle.common.fsutil import atomic_write, atomic_write_json, fsync_dir
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, run_dir_for

HOME_FORMAT = 2
FORMAT_FILE = "format"
LIVE_DIR = "live"
LOCKS_DIR = "locks"
SERVERS_DIR = "servers"
ADMISSION_LOCK = "admission.lock"
ADMISSION_HOLDER = "admission.holder"
GC_LOCK = "gc.lock"
PINS_LOCK = "pins.lock"
REGISTRY_LOCK = "registry.lock"
OWNER_LOCK = "owner.lock"
# A run being admitted is built as `runs/<month>/.adm-<run_id>` and renamed into place once its
# owner lock is held (rule 1); every walker of runs/ skips names starting with a dot.
ADMITTING_PREFIX = ".adm-"

# Rule 7: the admission lock is taken non-blocking and retried for at most this long; an admission
# that times out is refused `admission.home_busy` (retryable).
ADMISSION_WAIT_S = 2.0
_RETRY_S = 0.01

# The file systems on which flock is not reliable on macOS: every entry point refuses them.
NETWORK_FS_TYPES = frozenset(
    {"nfs", "nfs4", "smbfs", "smb", "smb2", "smb3", "cifs", "afpfs", "webdav", "ncpfs"}
)
# What a v0.3.0 process leaves in a home it served; a home holding any of them and no
# `home/format` is refused until `trestle init --upgrade`.
_LEGACY_ENTRIES = ("service_epoch", "runs", "idempotency.json")

# The one admission lock per process (rule 10): the admission thread, conductor threads, the
# reaper (and, in 1b, the 250 ms pass) all take it around the admission flock.
admission_mutex = threading.Lock()


class HomeRefused(Exception):
    """The home cannot be served by this process: a network mount, a v0.3.0 home not yet
    upgraded, or a format this version does not know. The message says what to do."""


class HomeBusy(Exception):
    """The admission lock stayed held past its bound. `holder` is what the holder wrote."""

    def __init__(self, holder: str | None) -> None:
        super().__init__(f"the home's admission lock is held by {holder or 'another process'}")
        self.holder = holder


# --- format and mount ----------------------------------------------------------------------------


def home_format(home: Path) -> int | None:
    """The home's recorded format, or None when `home/format` is absent or unreadable."""
    try:
        return int((home / FORMAT_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def is_legacy(home: Path) -> bool:
    """A home a v0.3.0 process served: no format file, and something only a server leaves."""
    return home_format(home) is None and any((home / name).exists() for name in _LEGACY_ENTRIES)


def write_format(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    atomic_write(home / FORMAT_FILE, f"{HOME_FORMAT}\n".encode())


def check_home(home: Path, *, create: bool = True) -> None:
    """The check every entry point runs before anything else: the home is on a local file system
    and has format 2. A fresh home (nothing a server leaves) is given format 2 when `create`; a
    v0.3.0 home is refused until `trestle init --upgrade`."""
    check_local_mount(home)
    recorded = home_format(home)
    if recorded == HOME_FORMAT:
        return
    if recorded is not None or (home / FORMAT_FILE).exists():
        raise HomeRefused(
            f"{home}: home format {recorded!r} is not {HOME_FORMAT}; this trestle cannot serve it"
        )
    if is_legacy(home):
        raise HomeRefused(
            f"{home}: a v0.3.0 home; stop every v0.3.0 server, then run "
            f"`trestle init --upgrade --home {home}`"
        )
    if create:
        write_format(home)


def check_local_mount(home: Path) -> None:
    kind = fs_type(home)
    if kind is not None and kind.lower() in NETWORK_FS_TYPES:
        raise HomeRefused(
            f"{home}: TRESTLE_HOME is on a {kind} mount; it must be on a local file system "
            "(flock is not reliable on a network mount)"
        )


def fs_type(path: Path) -> str | None:
    """The file-system type name of the mount holding `path` (its nearest existing ancestor), or
    None when it cannot be read. macOS: `statfs`; Linux: the longest matching `/proc/self/mounts`
    entry."""
    probe = path.absolute()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        if sys.platform == "darwin":
            return _darwin_fs_type(probe)
        return _linux_fs_type(probe)
    except (OSError, ValueError, AttributeError):
        return None


class _StatFs(ctypes.Structure):
    _fields_ = [  # noqa: RUF012 (the ctypes layout of macOS `struct statfs`, 64-bit inodes)
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


def _darwin_fs_type(path: Path) -> str | None:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    try:
        statfs = libc["statfs$INODE64"]  # x86_64; arm64 has only the 64-bit `statfs`
    except AttributeError:
        statfs = libc.statfs
    buf = _StatFs()
    if statfs(os.fsencode(str(path)), ctypes.byref(buf)) != 0:
        return None
    return buf.f_fstypename.decode("utf-8", errors="replace") or None


def _linux_fs_type(path: Path) -> str | None:
    target = os.path.realpath(path)
    best: tuple[int, str] | None = None
    with open("/proc/self/mounts", encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 3:
                continue
            mount = parts[1].replace("\\040", " ")
            if target == mount or target.startswith(mount.rstrip("/") + "/"):
                if best is None or len(mount) > best[0]:
                    best = (len(mount), parts[2])
    return None if best is None else best[1]


# --- flock helpers -------------------------------------------------------------------------------


def try_lock(path: Path) -> int | None:
    """Take `path`'s flock exclusively, without blocking, on a fresh descriptor: the descriptor
    (the caller closes it to release), or None when another holder has it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def is_locked(path: Path) -> bool:
    """Whether some open file description holds `path`'s flock: a probe on a fresh descriptor,
    closed at once (so a probe never reads the caller's own lock as free). A missing file, or a
    directory that is gone, is not locked."""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)  # closing drops the probe's own lock, when it took one
    return False


@contextlib.contextmanager
def file_lock(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """A short critical section under `path`'s flock (pins, the registry counter, GC). Yields
    True when held; non-blocking, yields False at once when another holder has it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def locks_dir(home: Path) -> Path:
    return home / LOCKS_DIR


# --- the admission lock (rule 7) -----------------------------------------------------------------


@contextlib.contextmanager
def admission_lock(home: Path, *, wait_s: float = ADMISSION_WAIT_S) -> Iterator[None]:
    """Hold the home's admission lock: the process-wide `admission_mutex`, then
    `locks/admission.lock` taken `LOCK_NB` and retried for at most `wait_s`. Raises `HomeBusy`
    (naming the holder) when either stays held past the bound. The holder writes its pid, thread
    and time to `locks/admission.holder`, so doctor can name a stuck holder."""
    deadline = time.monotonic() + wait_s
    if not admission_mutex.acquire(timeout=max(wait_s, 0.0)):
        raise HomeBusy(f"pid {os.getpid()} (another thread of this process)")
    try:
        path = locks_dir(home) / ADMISSION_LOCK
        while True:
            fd = try_lock(path)
            if fd is not None:
                break
            if time.monotonic() >= deadline:
                raise HomeBusy(admission_holder(home))
            time.sleep(_RETRY_S)
        try:
            _write_holder(home)
            yield
        finally:
            os.close(fd)
    finally:
        admission_mutex.release()


def _write_holder(home: Path) -> None:
    """Doctor's account of the holder; written in place (no fsync), as it only names the holder."""
    text = (
        f"pid={os.getpid()} thread={threading.current_thread().name} "
        f"at={datetime.now(tz=UTC).isoformat(timespec='seconds')}\n"
    )
    try:
        (locks_dir(home) / ADMISSION_HOLDER).write_text(text, encoding="utf-8")
    except OSError:
        pass


def admission_holder(home: Path) -> str | None:
    """Who holds the admission lock now (what the holder wrote), or None when it is free."""
    if not is_locked(locks_dir(home) / ADMISSION_LOCK):
        return None
    try:
        return (locks_dir(home) / ADMISSION_HOLDER).read_text(encoding="utf-8").strip() or None
    except OSError:
        return "unknown holder"


# --- owner locks and live markers ----------------------------------------------------------------


def owner_lock_path(run_dir: Path) -> Path:
    return evidence_dir(run_dir) / OWNER_LOCK


def live_dir(home: Path) -> Path:
    return home / LIVE_DIR


def marker_path(home: Path, run_id: str) -> Path:
    return live_dir(home) / run_id


def write_marker(home: Path, run_id: str, fields: dict[str, Any]) -> None:
    """The live marker of a run that is not terminal: the reaper's index. It carries the run's
    owner (`server_id`, None for a v0.3.0 run), its month (where the run directory is), its
    arrival, environment key and deadline (what 1b's `home/sched.json` caches)."""
    atomic_write_json(marker_path(home, run_id), fields)


def read_marker(home: Path, run_id: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(marker_path(home, run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def remove_marker(home: Path, run_id: str) -> None:
    path = marker_path(home, run_id)
    if path.exists():
        path.unlink(missing_ok=True)
        fsync_dir(path.parent)


def live_run_ids(home: Path) -> list[str]:
    """The runs with a live marker, oldest id first (run ids sort by their admission time)."""
    root = live_dir(home)
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in os.scandir(root)
        if not entry.name.startswith(".") and not entry.name.endswith(".tmp")
    )


def marked_run_dir(home: Path, run_id: str, marker: dict[str, Any] | None = None) -> Path | None:
    """The run directory a live marker names, or None when it is not there (yet, or any more)."""
    month = (marker or {}).get("month")
    if isinstance(month, str) and month:
        candidate = run_dir_for(home, run_id, month=month)
        if ledger_path(candidate).exists():
            return candidate
    from trestle.server.recovery import find_run_dir

    return find_run_dir(home, run_id)


# --- a server's own owner locks ------------------------------------------------------------------


@dataclass
class Ownership:
    """The owner locks one server holds: one descriptor per run it admitted and has not finished.
    A run is live exactly while its lock is held; the kernel drops every lock when the process
    exits, even on SIGKILL, so only a dead owner's runs are ever reaped."""

    home: Path
    server_id: str
    _fds: dict[str, int] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def adopt(self, run_id: str, fd: int) -> None:
        with self._lock:
            self._fds[run_id] = fd

    def owns(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._fds

    def owned(self) -> list[str]:
        with self._lock:
            return sorted(self._fds)

    def release(self, run_id: str) -> None:
        """The run's owner is done with it (`Scheduler.complete`, after its terminal row): remove
        its live marker under the admission lock (a completion runs rule 7's steps 1 and 5 to 7;
        1b also drops its `home/sched.json` entry there), then close the lock. A run that is not
        terminal (its conductor raised) keeps its marker: with the lock closed, the reaper
        finalizes it. A busy admission lock leaves the marker to the reaper too."""
        with self._lock:
            fd = self._fds.pop(run_id, None)
        if fd is None:
            return
        try:
            marker = read_marker(self.home, run_id)
            run_dir = marked_run_dir(self.home, run_id, marker)
            terminal = (
                run_dir is not None
                and RunLedger.open(ledger_path(run_dir)).terminal_state() is not None
            )
            if terminal:
                with admission_lock(self.home):
                    remove_marker(self.home, run_id)
        except (HomeBusy, OSError):
            pass
        finally:
            os.close(fd)

    def drop_all(self) -> None:
        """Close every owner lock and touch nothing else: what the process's exit does. A test
        stands in for a dead server with it; the runs are then the reaper's."""
        with self._lock:
            fds, self._fds = list(self._fds.values()), {}
        for fd in fds:
            os.close(fd)


# --- server lock files ---------------------------------------------------------------------------


def servers_dir(home: Path) -> Path:
    return home / SERVERS_DIR


@dataclass
class ServerLock:
    """`home/servers/<server_id>.lock`, flocked for the server's life. Its content (pid, port and
    the snapshot ids it serves) is rewritten in place through the held descriptor: a rename would
    move the lock off the path. GC keeps the snapshots live servers name; doctor lists them."""

    home: Path
    server_id: str
    fd: int
    port: int | None = None

    @classmethod
    def acquire(cls, home: Path, server_id: str, *, port: int | None = None) -> ServerLock:
        fd = try_lock(servers_dir(home) / f"{server_id}.lock")
        if fd is None:  # a server id is minted per process, so this cannot be held
            raise HomeRefused(f"server lock {server_id} is held by another process")
        lock = cls(home=home, server_id=server_id, fd=fd, port=port)
        lock.update(())
        return lock

    def update(self, snapshots: Any) -> None:
        payload = {
            "server_id": self.server_id,
            "pid": os.getpid(),
            "port": self.port,
            "snapshots": sorted(snapshots),
            "at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        }
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        os.ftruncate(self.fd, 0)
        os.pwrite(self.fd, data, 0)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self.fd)
        (servers_dir(self.home) / f"{self.server_id}.lock").unlink(missing_ok=True)


def live_servers(home: Path, *, prune: bool = False) -> list[dict[str, Any]]:
    """Every server whose lock is held, with what its lock file says. With `prune`, the file of a
    server whose lock is free (it died) is removed: its server id is never minted again."""
    root = servers_dir(home)
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.lock")):
        if not is_locked(path):
            if prune:
                path.unlink(missing_ok=True)
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            loaded = {}
        row = loaded if isinstance(loaded, dict) else {}
        row.setdefault("server_id", path.stem)
        found.append(row)
    return found


def served_snapshots(home: Path) -> set[str]:
    """The snapshot ids every live server names in its lock file (GC keeps them). A dead server's
    lock file is removed on the way."""
    ids: set[str] = set()
    for row in live_servers(home, prune=True):
        snaps = row.get("snapshots")
        if isinstance(snaps, list):
            ids.update(str(item) for item in snaps)
    return ids


def raise_nofile_limit() -> None:
    """Raise the soft RLIMIT_NOFILE to the hard limit (rule 10): a server can hold about 550 lock
    descriptors, and launchd's default soft limit is 256."""
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == resource.RLIM_INFINITY:
        return
    for target in (hard, 10240):  # macOS refuses an unlimited soft limit: fall back to 10240
        if target == resource.RLIM_INFINITY:
            continue
        if target <= soft:
            return
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            return
        except (ValueError, OSError):
            continue


def remove_debris_dir(path: Path) -> None:
    """Remove an admission's debris (a `.adm-` directory). Only the reaper calls this, and only
    while it holds the admission lock."""
    shutil.rmtree(path, ignore_errors=True)
