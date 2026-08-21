#!/usr/bin/env python3
"""M0.5 — quiet supervisor + fork spike.

Proves or falsifies: single-threaded poll reactor can fork concurrent children
after importing native deps, capture stdout via nonblocking pipes, and cancel
a process group. Run on macOS and Linux.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, asdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / f"m05-{sys.platform}-{sys.version_info.major}.{sys.version_info.minor}.json"


def os_thread_count() -> int:
    if sys.platform == "darwin":
        raw = subprocess.check_output(["ps", "-M", str(os.getpid())], text=True)
        # header + one line per thread
        return max(0, len([ln for ln in raw.splitlines() if ln.strip()]) - 1)
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("Threads:"):
                return int(line.split()[1])
    return threading.active_count()


def py_thread_count() -> int:
    return threading.active_count()


@dataclass
class Case:
    name: str
    ok: bool
    detail: str
    ms: float = 0.0


def try_imports() -> tuple[list[str], list[str], dict[str, str]]:
    loaded, failed, versions = [], [], {}
    for mod in ("numpy", "pandas"):
        t0 = time.perf_counter()
        try:
            m = __import__(mod)
            loaded.append(mod)
            versions[mod] = getattr(m, "__version__", "?")
            versions[f"{mod}_import_ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
        except Exception as e:  # noqa: BLE001
            failed.append(f"{mod}: {e}")
    return loaded, failed, versions


def child_work(kind: str, stdout_w: int) -> None:
    os.dup2(stdout_w, 1)
    os.dup2(stdout_w, 2)
    if stdout_w not in (1, 2):
        os.close(stdout_w)
    if kind == "hello":
        print("hello from child", flush=True)
        print("numpy ok" if "numpy" in sys.modules else "numpy absent", flush=True)
        sys.exit(0)
    if kind == "burst":
        for i in range(10_000):
            print(f"line {i}", flush=True)
        sys.exit(0)
    if kind == "async":
        import asyncio

        async def main() -> None:
            await asyncio.sleep(0.05)
            print("async-ok", flush=True)

        asyncio.run(main())
        sys.exit(0)
    if kind == "subproc":
        subprocess.run([sys.executable, "-c", "print('grandchild')"], check=True)
        sys.exit(0)
    if kind == "hang":
        time.sleep(30)
        sys.exit(0)
    sys.exit(2)


def fork_and_capture(kind: str, timeout_s: float = 5.0, kill: bool = False) -> tuple[int, bytes, float]:
    r, w = os.pipe()
    os.set_blocking(r, False)
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            os.setpgrp()
        except Exception:
            pass
        child_work(kind, w)
        os._exit(2)
    os.close(w)
    sel = selectors.DefaultSelector()
    sel.register(r, selectors.EVENT_READ)
    buf = bytearray()
    deadline = time.monotonic() + timeout_s
    status = 0
    reaped = False
    if kill:
        time.sleep(0.05)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        events = sel.select(timeout=min(0.1, remaining))
        if events:
            try:
                chunk = os.read(r, 65536)
            except BlockingIOError:
                chunk = b""
            if chunk:
                buf.extend(chunk)
        try:
            waited, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            reaped = True
            break
        if waited == pid:
            reaped = True
            break
    sel.unregister(r)
    sel.close()
    os.set_blocking(r, True)
    while True:
        chunk = os.read(r, 65536)
        if not chunk:
            break
        buf.extend(chunk)
    try:
        os.close(r)
    except OSError:
        pass
    if not reaped:
        try:
            _, status = os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    elapsed = (time.perf_counter() - t0) * 1000
    return status, bytes(buf), elapsed


def fork_two_concurrent() -> Case:
    t0 = time.perf_counter()
    pids = []
    pipes = []
    for _ in range(2):
        r, w = os.pipe()
        os.set_blocking(r, False)
        pid = os.fork()
        if pid == 0:
            os.close(r)
            os.setpgrp()
            child_work("hello", w)
            os._exit(2)
        os.close(w)
        pids.append(pid)
        pipes.append(r)
    sel = selectors.DefaultSelector()
    for r in pipes:
        sel.register(r, selectors.EVENT_READ)
    got = {pid: bytearray() for pid in pids}
    live = set(pids)
    deadline = time.monotonic() + 5
    while live and time.monotonic() < deadline:
        for key, _ in sel.select(timeout=0.1):
            chunk = os.read(key.fd, 65536)
            for pid, r in zip(pids, pipes):
                if r == key.fd:
                    if chunk:
                        got[pid].extend(chunk)
        for pid in list(live):
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                live.discard(pid)
    for r in pipes:
        os.close(r)
    sel.close()
    texts = b"".join(got.values())
    ok = b"hello from child" in texts and not live
    return Case("concurrent_children", ok, f"live={len(live)} bytes={len(texts)}", (time.perf_counter() - t0) * 1000)


def main() -> int:
    cases: list[Case] = []
    meta: dict = {
        "platform": sys.platform,
        "python": sys.version,
        "executable": sys.executable,
        "pid": os.getpid(),
        "threads_before_import": {"os": os_thread_count(), "py": py_thread_count()},
    }

    t0 = time.perf_counter()
    loaded, failed, versions = try_imports()
    meta["imports"] = {"loaded": loaded, "failed": failed, "versions": versions}
    meta["threads_after_import"] = {"os": os_thread_count(), "py": py_thread_count()}
    meta["import_total_ms"] = (time.perf_counter() - t0) * 1000

    # Touch BLAS; import alone may not start native pools.
    if "numpy" in sys.modules:
        import numpy as np

        a = np.ones((256, 256))
        _ = a @ a
        meta["threads_after_numpy_compute"] = {"os": os_thread_count(), "py": py_thread_count()}
    else:
        meta["threads_after_numpy_compute"] = meta["threads_after_import"]

    py_ok = meta["threads_after_numpy_compute"]["py"] == 1
    cases.append(
        Case(
            "supervisor_python_single_threaded",
            py_ok,
            f"py={meta['threads_after_numpy_compute']['py']} os={meta['threads_after_numpy_compute']['os']}",
        )
    )
    os_threads = meta["threads_after_numpy_compute"]["os"]
    cases.append(
        Case(
            "supervisor_os_single_threaded",
            os_threads == 1,
            f"os_threads={os_threads} after numpy compute; imports {loaded}",
        )
    )

    # Cold spawn import cost (child re-imports)
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, "-c", "import numpy, pandas; print('cold')"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    cases.append(
        Case(
            "cold_spawn_import",
            r.returncode == 0 and "cold" in r.stdout,
            r.stderr[-200:] if r.returncode else "ok",
            (time.perf_counter() - t0) * 1000,
        )
    )

    for kind, expect in (
        ("hello", b"hello from child"),
        ("burst", b"line 9999"),
        ("async", b"async-ok"),
        ("subproc", b"grandchild"),
    ):
        try:
            status, buf, ms = fork_and_capture(kind)
            ok = expect in buf
            cases.append(Case(f"fork_{kind}", ok, f"status={status} bytes={len(buf)} snippet={buf[-80:]!r}", ms))
        except Exception as e:  # noqa: BLE001
            cases.append(Case(f"fork_{kind}", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}"))

    # fork again after previous children — the load-bearing sequence
    try:
        _, buf1, ms1 = fork_and_capture("hello")
        _, buf2, ms2 = fork_and_capture("hello")
        ok = b"hello from child" in buf1 and b"hello from child" in buf2
        cases.append(Case("fork_after_previous_child", ok, f"ms=({ms1:.1f},{ms2:.1f})", ms1 + ms2))
    except Exception as e:  # noqa: BLE001
        cases.append(Case("fork_after_previous_child", False, str(e)))

    try:
        cases.append(fork_two_concurrent())
    except Exception as e:  # noqa: BLE001
        cases.append(Case("concurrent_children", False, str(e)))

    try:
        t0 = time.perf_counter()
        status, buf, ms = fork_and_capture("hang", timeout_s=2.0, kill=True)
        cases.append(Case("cancel_process_group", True, f"killed status={status} ms={ms:.1f}", (time.perf_counter() - t0) * 1000))
    except Exception as e:  # noqa: BLE001
        cases.append(Case("cancel_process_group", False, str(e)))

    report = {
        "meta": meta,
        "cases": [asdict(c) for c in cases],
        "all_ok": all(c.ok for c in cases),
        "fork_path_viable": all(
            next(c.ok for c in cases if c.name == n)
            for n in (
                "supervisor_python_single_threaded",
                "supervisor_os_single_threaded",
                "fork_hello",
                "fork_after_previous_child",
                "concurrent_children",
            )
            if any(c.name == n for c in cases)
        ),
    }
    # fork_path_viable requires OS single-threaded; native pools fail that.
    try:
        report["fork_path_viable"] = (
            next(c.ok for c in cases if c.name == "supervisor_os_single_threaded")
            and next(c.ok for c in cases if c.name == "fork_hello")
            and next(c.ok for c in cases if c.name == "fork_after_previous_child")
            and next(c.ok for c in cases if c.name == "concurrent_children")
        )
    except StopIteration:
        report["fork_path_viable"] = False

    report["recommendation"] = (
        "warm-fork" if report["fork_path_viable"] else "spawn-per-run"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
