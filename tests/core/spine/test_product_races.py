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

import asyncio
import hashlib
import os
import shutil
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import tolerances
from trestle.common import fsutil
from trestle.common.types import PublishView, RunView
from trestle.server import registry as registry_mod
from trestle.server import snapshots
from trestle.server.plugin_schema import schemas_from_source
from trestle.server.plugin_validate import PluginValidationError
from trestle.server.registry import Registry

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


# P2 ------------------------------------------------------------------------------------------


def _plugin(tmp_path: Path, source: str = SOURCE_A) -> Path:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(exist_ok=True)
    path = plugin_dir / "racer.py"
    path.write_text(source, encoding="utf-8")
    return path


def _gate_plugin_copy(monkeypatch, on_gate: Callable[[], None]) -> None:
    """Call `on_gate` at the moment plugin.py is being put in place: half-way through an in-place
    copy (`shutil.copyfileobj`, forced off the platform's one-call copy), or just before the rename
    of a whole temporary onto plugin.py."""
    monkeypatch.setattr(shutil, "_HAS_FCOPYFILE", False, raising=False)
    monkeypatch.setattr(shutil, "_USE_CP_SENDFILE", False, raising=False)

    def copy_half_then_gate(fsrc: Any, fdst: Any, length: int = 0) -> None:
        data = fsrc.read()
        fdst.write(data[: len(data) // 2])
        fdst.flush()
        on_gate()
        fdst.write(data[len(data) // 2 :])

    monkeypatch.setattr(shutil, "copyfileobj", copy_half_then_gate)
    monkeypatch.setattr(fsutil, "os", _GatedOs(lambda dst: dst.name == "plugin.py", on_gate))


def test_p2_plugin_copy_is_whole_or_absent_to_a_reader(tmp_path: Path, monkeypatch) -> None:
    """While one materialization is putting plugin.py in place, a reader (a run's child, or a
    second materialization deciding whether to copy) sees it absent or whole, never part-written."""
    source = _plugin(tmp_path)
    raw = source.read_bytes()
    home = tmp_path / "home"
    reached, go = threading.Event(), threading.Event()
    _gate_plugin_copy(monkeypatch, _gate(reached, go))
    made: list[Any] = []

    def materialize() -> None:
        try:
            made.append(snapshots.materialize_snapshot(source, "racer", home=home))
        finally:
            reached.set()  # a path that never gates still lets the reader look

    worker = threading.Thread(target=materialize)
    worker.start()
    assert reached.wait(WAIT_S)
    seen = [p.read_bytes() for p in (home / "snapshots").glob("*/plugin.py")]
    go.set()
    worker.join(WAIT_S)
    assert all(data == raw for data in seen), "a reader saw a part-written plugin.py"
    (snap,) = made
    assert Path(snap.source_path).read_bytes() == raw


def test_p2_interrupted_copy_is_not_kept(tmp_path: Path, monkeypatch) -> None:
    """A materialization that dies while putting plugin.py in place leaves nothing that a later one
    would take for the finished copy (it skips the copy when plugin.py exists)."""
    source = _plugin(tmp_path)
    raw = source.read_bytes()
    home = tmp_path / "home"

    def crash() -> None:
        raise OSError("interrupted while copying")

    with monkeypatch.context() as patch:
        _gate_plugin_copy(patch, crash)
        with pytest.raises(OSError, match="interrupted"):
            snapshots.materialize_snapshot(source, "racer", home=home)
    snap = snapshots.materialize_snapshot(source, "racer", home=home)
    assert Path(snap.source_path).read_bytes() == raw
    assert not list(Path(snap.source_path).parent.glob("*.tmp"))


def test_p2_source_edited_mid_publication_never_mixes(tmp_path: Path, monkeypatch) -> None:
    """The plugin file is edited while its snapshot is being made (after the read, during the
    validation child). The snapshot must not pair one source's bytes with the other's schema; the
    next refresh publishes the edited source, consistently."""
    source = _plugin(tmp_path)
    reg = Registry(home=tmp_path / "home", plugin_dirs=[source.parent])
    real_validate = snapshots.validate_and_extract
    edits = [SOURCE_B]

    def validate_then_edit(*args: Any, **kwargs: Any) -> Any:
        outcome = real_validate(*args, **kwargs)
        if edits:
            source.write_text(edits.pop(), encoding="utf-8")
        return outcome

    monkeypatch.setattr(snapshots, "validate_and_extract", validate_then_edit)
    reg.maybe_refresh()  # the edit lands during this refresh
    mixed = reg.get("racer")
    assert mixed is None or _consistent(mixed), "one snapshot holds two sources"
    reg.maybe_refresh()
    snap = reg.get("racer")
    assert snap is not None and _consistent(snap)
    assert Path(snap.source_path).read_bytes() == SOURCE_B.encode("utf-8")


def _consistent(snap: Any) -> bool:
    """plugin.py is the source the snapshot's hash and schema were made from."""
    kept = Path(snap.source_path).read_bytes()
    input_schema, _return = schemas_from_source(kept.decode("utf-8"))
    return _sha(kept) == snap.source_sha256 and snapshots.load_snapshot_schema(snap) == input_schema


def test_p2_moving_source_is_refused_not_published(tmp_path: Path, monkeypatch) -> None:
    source = _plugin(tmp_path)
    real_validate = snapshots.validate_and_extract

    def validate_then_edit(*args: Any, **kwargs: Any) -> Any:
        outcome = real_validate(*args, **kwargs)
        source.write_text(SOURCE_B, encoding="utf-8")
        return outcome

    monkeypatch.setattr(snapshots, "validate_and_extract", validate_then_edit)
    with pytest.raises(PluginValidationError, match="changed while"):
        snapshots.materialize_snapshot(source, "racer", home=tmp_path / "home")


# P3 ------------------------------------------------------------------------------------------


class _LockWatch:
    """The registry's lock, reporting each time the `publisher` thread asks for it."""

    def __init__(self, inner: Any, asked: threading.Event) -> None:
        self._inner = inner
        self._asked = asked

    def __enter__(self) -> Any:
        if threading.current_thread().name == "publisher":
            self._asked.set()
        return self._inner.__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._inner.__exit__(*exc)


def test_p3_older_refresh_never_overwrites_a_newer_publish(tmp_path: Path, monkeypatch) -> None:
    """Refresh R has made its snapshot of source A and not yet installed it; meanwhile a publish
    of source B runs (or, serialized, asks to). Whatever the order, once both are done the registry
    serves B, and the publish reported B."""
    source = _plugin(tmp_path)
    reg = Registry(home=tmp_path / "home", plugin_dirs=[source.parent])
    real_materialize = registry_mod.materialize_snapshot
    r_holds, release = threading.Event(), threading.Event()
    first = [True]

    def materialize_then_hold(*args: Any, **kwargs: Any) -> Any:
        snap = real_materialize(*args, **kwargs)
        if first:
            first.clear()
            r_holds.set()
            assert release.wait(WAIT_S), "refresh R was never released"
        return snap

    monkeypatch.setattr(registry_mod, "materialize_snapshot", materialize_then_hold)
    if hasattr(reg, "_lock"):  # serialized: R is released once the publish asks for the lock
        reg._lock = _LockWatch(reg._lock, release)  # type: ignore[assignment]
    published: list[Any] = []

    def publish() -> None:
        try:
            published.append(reg.publish_source(SOURCE_B))
        finally:
            release.set()

    refresher = threading.Thread(target=reg.refresh, name="refresher")
    refresher.start()
    assert r_holds.wait(WAIT_S), "refresh R never made its snapshot"
    publisher = threading.Thread(target=publish, name="publisher")
    publisher.start()
    publisher.join(WAIT_S)
    refresher.join(WAIT_S)
    (view,) = published
    assert isinstance(view, PublishView), view
    assert view.source_sha256 == _sha(SOURCE_B.encode("utf-8"))
    snap = reg.get("racer")
    assert snap is not None and snap.source_sha256 == view.source_sha256, "a stale refresh won"


@pytest.mark.parametrize("hook", ["discover_plugin_name", "materialize_snapshot"])
def test_p3_plugin_removed_mid_refresh_is_dropped(tmp_path: Path, monkeypatch, hook: str) -> None:
    """A plugin file is removed after the refresh listed the directory (before its name is read,
    or before its snapshot is made). The refresh completes, without it, and serves the others."""
    gone = _plugin(tmp_path)
    (gone.parent / "echo.py").write_text(SOURCE_A.replace("racer", "echo"), encoding="utf-8")
    reg = Registry(home=tmp_path / "home", plugin_dirs=[gone.parent])
    reg.refresh()
    assert set(reg.snapshots) == {"racer", "echo"}
    gone.write_text(SOURCE_A + "\n# edited\n", encoding="utf-8")  # the next refresh rereads it
    real = getattr(registry_mod, hook)

    def remove_then(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == gone:
            path.unlink(missing_ok=True)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(registry_mod, hook, remove_then)
    reg.maybe_refresh()
    assert set(reg.snapshots) == {"echo"}


# Sever: an admitted run is always driven ------------------------------------------------------


def _terminal_state(kernel: Any, run_id: str) -> str | None:
    view = kernel.control.project.status(run_id)
    if isinstance(view, RunView) and view.state not in ("queued", "running"):
        return view.state
    return None


def _cancel_run_call_at(kernel: Any, reached: threading.Event, go: threading.Event) -> None:
    """Start a held terminal `run` call, cancel it (as an MCP cancel notification does) once
    `reached` is set, then open `go`."""

    async def scenario() -> None:
        call = asyncio.create_task(
            kernel.control.run_async(
                plugin="echo", args={"message": "sever"}, wait_ms=10_000, completion="terminal"
            )
        )
        assert await asyncio.to_thread(reached.wait, WAIT_S), "the call never got there"
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        go.set()

    asyncio.run(scenario())


def test_sever_mid_admit_still_drives_the_run(kernel, monkeypatch) -> None:
    """The cancel lands while the admit is under way (the run is created, the admit has not
    returned). The run must still be started and reach its own terminal state."""
    admission = kernel.control.admission
    real_admit = admission.admit
    reached, go = threading.Event(), threading.Event()
    admitted: list[str] = []

    def admit_then_hold(request: Any) -> Any:
        result = real_admit(request)
        admitted.append(result.run_id)
        reached.set()
        assert go.wait(WAIT_S)
        return result

    monkeypatch.setattr(admission, "admit", admit_then_hold)
    _cancel_run_call_at(kernel, reached, go)
    (run_id,) = admitted
    assert support.wait_until(lambda: _terminal_state(kernel, run_id) is not None, RUN_WAIT_S), (
        "the admitted run was never driven"
    )
    assert _terminal_state(kernel, run_id) == "succeeded"


def test_sever_mid_hand_off_still_drives_the_run(kernel, monkeypatch) -> None:
    """The cancel lands after the admit returned, while the run is being handed to the dispatcher.
    The run must still be started and reach its own terminal state."""
    control = kernel.control
    real_work_order = control._work_order
    reached, go = threading.Event(), threading.Event()
    handed: list[str] = []

    def work_order_held(run_id: str, *args: Any) -> Any:
        handed.append(run_id)
        reached.set()
        assert go.wait(WAIT_S)
        return real_work_order(run_id, *args)

    monkeypatch.setattr(control, "_work_order", work_order_held)
    _cancel_run_call_at(kernel, reached, go)
    (run_id,) = handed
    assert support.wait_until(lambda: _terminal_state(kernel, run_id) is not None, RUN_WAIT_S), (
        "the admitted run was never driven"
    )
    assert _terminal_state(kernel, run_id) == "succeeded"
