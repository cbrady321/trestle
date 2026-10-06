"""L.SL-8.1: the environment lease key at admission, and the lease defined over the ledger.

The key is the canonical JSON of the request argument `declared.env_arg` names (opaque to the
host), recorded in the `created` row and in the plan's `lease_set`; a run holds the lease from its
`created` row until a terminal row or its admitted deadline; there is no lease store file; a
restart rebuilds the same holder set from the ledgers (WR-OWN-8, L.SL-8.2 queues on it)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support as spine
from tests.core.spine.support import FakeHost
from tests.proof import harness, tolerances
from tests.single.control import support
from trestle.common import clock, codes
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.types import (
    AdmitRequest,
    AdmitResultAdmitted,
    RequestOutcome,
    RunView,
    WorkOrder,
)
from trestle.server import lease, procident
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import Kernel, create_kernel
from trestle.server.procident import Attribution, GroupStop, Identity
from trestle.server.reaper import reap_home
from trestle.server.scheduler import Scheduler

PLUGINS = Path(__file__).resolve().parent / "plugins"
HOLD_S = tolerances.JOIN_WAIT_S * 6

ENV_PLAIN = """
from __future__ import annotations

from trestle.plugin import Context, trestle


@trestle(env_arg="env")
def envplain(ctx: Context, env: str = "dev", note: str = "") -> dict[str, str]:
    return {"env": env}
"""


def _env_workflow() -> str:
    """The one-leaf workflow plugin, its root keyed on the argument `name` (`env_key_field` equal
    to `env_arg`, the D-b rule)."""
    return (
        support.workflow_source("envwf")
        .replace("@trestle(deadline=", '@trestle(env_arg="name", deadline=')
        .replace("max_attempts=1,", 'max_attempts=1,\n            env_key_field="name",')
    )


def _kernel(tmp_path: Path) -> Kernel:
    return support.make_kernel(
        tmp_path,
        {
            "echo": support.ECHO.read_text(encoding="utf-8"),
            "envplain": ENV_PLAIN,
            "envwf": _env_workflow(),
        },
    )


def _admit(kernel: Kernel, plugin: str, args: dict[str, object] | None = None) -> str:
    result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args=args or {}))
    assert isinstance(result, AdmitResultAdmitted), result
    return result.run_id


def _created(kernel: Kernel, run_id: str) -> dict[str, object]:
    run_dir = support.run_dir_of(kernel, run_id)
    row = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert row is not None
    return row


def _plan(kernel: Kernel, run_id: str) -> AdmittedPlan:
    raw = support.read_spec(support.run_dir_of(kernel, run_id))["plan"]
    return AdmittedPlan.from_json(json.dumps(raw))


def _end(kernel: Kernel, run_id: str, kind: str = "failed") -> None:
    ledger = RunLedger.open(ledger_path(support.run_dir_of(kernel, run_id)))
    ledger.append(kind, run_id=run_id)


def test_key_from_env_arg_opaque(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    plain = _admit(kernel, "envplain", {"env": "prod", "note": "a"})
    same = _admit(kernel, "envplain", {"env": "prod", "note": "b"})
    other = _admit(kernel, "envplain", {"env": "staging"})
    # (its own environment: a 30 s tree behind a same-deadline `prod` holder is refused busy)
    tree = _admit(kernel, "envwf", {"name": "wfenv"})

    # opaque bytes: the canonical JSON of the argument value, whatever else the request carries
    assert _created(kernel, plain)["lease_key"] == '"prod"'
    assert _created(kernel, same)["lease_key"] == '"prod"'
    assert _created(kernel, other)["lease_key"] == '"staging"'
    assert _plan(kernel, plain).lease_set == ('"prod"',)
    assert _plan(kernel, other).lease_set == ('"staging"',)
    # a declared tree's compiled lease_set and the created row agree
    assert _plan(kernel, tree).lease_set == ('"wfenv"',)
    assert _created(kernel, tree)["lease_key"] == '"wfenv"'

    # a plugin that declares no environment admits as before: no key, no lease
    echo = _admit(kernel, "echo", {"message": "hi"})
    assert "lease_key" not in _created(kernel, echo)
    assert _plan(kernel, echo).lease_set == ()
    assert echo not in {h.run_id for h in kernel.control.admission.holders.snapshot()}

    # an environment plugin whose request names no environment holds nothing
    unnamed = _admit(kernel, "envplain", {})
    assert "lease_key" not in _created(kernel, unnamed)
    assert _plan(kernel, unnamed).lease_set == ()

    # no lease store file: the lease is defined over the ledgers
    assert not [p for p in kernel.home.rglob("*") if "lease" in p.name.lower()]


def test_lease_definition_over_ledger(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    holders = kernel.control.admission.holders
    run_id = _admit(kernel, "envplain", {"env": "prod"})
    run_dir = support.run_dir_of(kernel, run_id)

    held = lease.holder_of(run_dir)
    assert held is not None and held.run_id == run_id and held.key == '"prod"'
    assert holders.held('"prod"') == (held,)
    assert holders.held('"staging"') == ()

    # the deadline ends the lease (wall clock, the admitted deadline)
    assert lease.holder_of(run_dir, now=held.deadline_epoch - 1) == held
    assert lease.holder_of(run_dir, now=held.deadline_epoch) is None
    assert holders.held('"prod"', now=held.deadline_epoch + 1) == ()

    # a terminal row ends it, before anything else is recorded (OQ-34 fixes the row, not the sweep)
    second = _admit(kernel, "envplain", {"env": "prod"})
    assert {h.run_id for h in holders.held('"prod"')} == {run_id, second}
    _end(kernel, run_id, "timed_out")
    assert lease.holder_of(run_dir) is None
    assert [h.run_id for h in holders.held('"prod"')] == [second]

    # no created row, no lease
    empty = tmp_path / "empty-run"
    (empty / "evidence").mkdir(parents=True)
    assert lease.holder_of(empty) is None


def test_lease_rebuilt_after_restart(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    first = _admit(kernel, "envplain", {"env": "prod"})
    second = _admit(kernel, "envwf", {"name": "wfenv"})
    third = _admit(kernel, "envplain", {"env": "staging"})
    _admit(kernel, "echo", {"message": "hi"})
    ended = _admit(kernel, "envplain", {"env": "prod", "note": "ended"})
    _end(kernel, ended, "cancelled")
    before = kernel.control.admission.holders.snapshot()
    assert {h.run_id for h in before} == {first, second, third}

    # a restart without recovery: the ledgers alone give back the identical holder set, in order
    restarted = create_kernel(
        home=kernel.home, plugin_dirs=[tmp_path / "plugin-src"], skip_recovery=True
    )
    after = restarted.control.admission.holders
    assert after.snapshot() == before
    assert [h.run_id for h in after.held()] == sorted([first, second, third])
    assert [h.run_id for h in after.held('"prod"')] == [first]
    assert lease.rebuild_holders(kernel.home).snapshot() == before

    # the first server dies (its owner locks go with it): a restart's reaper pass ends every
    # unfinished run at a terminal row, so nothing is held
    kernel.ownership.drop_all()
    recovered = create_kernel(home=kernel.home, plugin_dirs=[tmp_path / "plugin-src"])
    assert recovered.control.admission.holders.snapshot() == frozenset()


# -- L.SL-8.2: per-key capacity 1 behind the MC-30 queue, the busy refusal, the lease's end ------


@pytest.fixture
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", spine.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", spine.TEST_KILL_S)


def _proc_kernel(*extra: Path) -> Kernel:
    return harness.fresh_kernel(plugin_dirs=[PLUGINS, *extra])


def _start(kernel: Kernel, plugin: str, **args: Any) -> tuple[str, Path]:
    """Run `plugin` through the real control surface without waiting; its run id and directory."""
    view = kernel.control.run(plugin, dict(args), wait_ms=0)
    assert isinstance(view, RunView), view
    return view.run_id, spine.run_dir_of(kernel, view.run_id)


def _kinds(run_dir: Path) -> list[str]:
    return [str(row["kind"]) for row in spine.rows(run_dir)]


def _started(run_dir: Path) -> bool:
    return "started" in _kinds(run_dir)


def _ended(run_dir: Path) -> bool:
    kinds = _kinds(run_dir)
    return bool(kinds) and kinds[-1] in (
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "interrupted",
    )


def _release(run_dir: Path) -> None:
    (run_dir / "work" / "tmp" / "go").write_text("1", encoding="utf-8")


@contextmanager
def _reaped(*run_ids: str) -> Iterator[None]:
    try:
        yield
    finally:
        for run_id in run_ids:
            spine.ancestry.reap(spine.marked(run_id))


def test_second_run_same_env_waits_fifo_within_deadline() -> None:
    kernel = _proc_kernel()
    holder, holder_dir = _start(kernel, "env_hold", env="prod")
    with _reaped(holder):
        spine.wait_ready(holder_dir)
        first, first_dir = _start(kernel, "env_hold", env="prod")
        second, second_dir = _start(kernel, "env_hold", env="prod")
        other, other_dir = _start(kernel, "env_hold", env="staging")
        with _reaped(first, second, other):
            # another environment is not held back; the two runs of `prod` queue behind the holder
            spine.wait_ready(other_dir)
            assert not _started(first_dir) and not _started(second_dir)
            assert kernel.control.scheduler.running_keys[holder] == '"prod"'

            # the holder ends: the oldest waiter starts, the younger one still waits (FIFO)
            _release(holder_dir)
            assert spine.wait_until(lambda: _ended(holder_dir), tolerances.JOIN_WAIT_S)
            spine.wait_ready(first_dir)
            assert _ended(holder_dir) and not _started(second_dir)
            _release(first_dir)
            assert spine.wait_until(lambda: _ended(first_dir), tolerances.JOIN_WAIT_S)
            spine.wait_ready(second_dir)
            for run_dir in (second_dir, other_dir):
                _release(run_dir)
            assert spine.wait_until(
                lambda: _ended(second_dir) and _ended(other_dir), tolerances.JOIN_WAIT_S
            )
            assert [_kinds(d)[-1] for d in (holder_dir, first_dir, second_dir, other_dir)] == [
                "succeeded"
            ] * 4
            assert kernel.control.scheduler.running_keys == {}


def test_waiting_run_past_its_deadline_is_finalized_not_started() -> None:
    """The lease wait is bounded by the waiter's own deadline: a run still waiting on a held key
    when it passes is finalized through the queue's expiry path (B2-C12), never dispatched, and
    the holder and the run behind it are left alone. (Admission refuses such a waiter busy unless
    the holders' deadlines leave it room, so the bound is exercised on the scheduler.)"""
    scheduler = Scheduler()
    started: list[str] = []
    expired: list[str] = []
    scheduler.on_dispatch = lambda order: started.append(order.run_id)
    scheduler.on_expire = lambda order: expired.append(order.run_id)
    now = time.monotonic()
    orders = {name: WorkOrder(run_id=name, snapshot_id="s", spec_hash="h") for name in "abc"}
    key = '"prod"'
    scheduler.enqueue(orders["a"], now + HOLD_S, key=key)  # the holder
    scheduler.enqueue(orders["b"], now + tolerances.SETTLE_S, key=key)  # waits, then expires
    scheduler.enqueue(orders["c"], now + HOLD_S, key=key)  # behind b, and behind the holder
    assert started == ["a"]
    assert spine.wait_until(lambda: expired == ["b"], tolerances.JOIN_WAIT_S)
    assert started == ["a"] and [w.order.run_id for w in scheduler.waiting] == ["c"]
    scheduler.complete("a")  # the holder ends: the waiter behind the expired one starts
    assert started == ["a", "c"] and scheduler.running_keys == {"c": key}
    scheduler.complete("c")


def test_refused_busy_when_holder_deadline_leaves_too_little(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "envwf.py").write_text(_env_workflow(), encoding="utf-8")
    kernel = _proc_kernel(extra)
    admission = kernel.control.admission

    def admit(plugin: str, **args: object) -> AdmitResultAdmitted | RequestOutcome:
        result = admission.admit(AdmitRequest(plugin=plugin, args=dict(args)))
        return result if isinstance(result, AdmitResultAdmitted) else result.outcome

    runs = kernel.home / "runs"

    def run_dirs() -> int:
        return len(list(runs.glob("*/*"))) if runs.exists() else 0

    # a holder admitted with the default deadline; a request whose deadline ends before the
    # holder's can never run after it: refused busy, retryable, with no run id and no run dir
    assert isinstance(admit("env_hold", env="prod"), AdmitResultAdmitted)
    before = run_dirs()
    busy = admit("env_short", env="prod")
    assert isinstance(busy, RequestOutcome)
    assert (busy.code, busy.retryable) == (codes.ADMISSION_ENVIRONMENT_BUSY, True)
    assert busy.code == "admission.environment_busy" and busy.origin == "admission"
    assert run_dirs() == before
    # another environment is not busy, and neither is a same-deadline request behind the holder
    assert isinstance(admit("env_short", env="staging"), AdmitResultAdmitted)
    assert isinstance(admit("env_quick", env="prod"), AdmitResultAdmitted)

    # the plan's worst case counts: a tree that needs its 30 s budget after the holder's deadline
    # is refused when the holder ends at the same instant, admitted when the holder's is earlier
    tree_busy = admit("envwf", name="dev")
    assert isinstance(tree_busy, AdmitResultAdmitted)  # nothing holds `dev` yet
    tree_after = admit("envwf", name="dev")
    assert isinstance(tree_after, RequestOutcome) and tree_after.code == busy.code
    assert isinstance(admit("env_short", env="qa"), AdmitResultAdmitted)  # 60 s holder of `qa`
    assert isinstance(admit("envwf", name="qa"), AdmitResultAdmitted)  # 300 s tree behind it


def _unconfirmed(attribution: Attribution) -> GroupStop:
    """The real stopper runs (nothing outlives the test), then its confirmation is withheld."""
    real = procident.stop_group(attribution)
    return GroupStop(confirmed_gone=False, signalled=real.signalled)


VARIANTS = ("restart", "cancel", "deadline")


class _Ended:
    """What a holder's unconfirmed end left: the kernel to admit on afterwards, the holder's run
    directory, and its answer."""

    def __init__(self, kernel: Kernel, holder_dir: Path, waiter_dir: Path | None) -> None:
        self.kernel = kernel
        self.holder_dir = holder_dir
        self.waiter_dir = waiter_dir

    def view(self) -> RunView:
        view = self.kernel.control.project.status(self.holder_dir.name)
        assert isinstance(view, RunView)
        return view

    def cleanup(self) -> dict[str, Any]:
        answer = self.view().to_dict()["answer"]
        assert isinstance(answer, dict)
        cleanup = answer["cleanup"]
        assert isinstance(cleanup, dict)
        return cleanup


@contextmanager
def _unconfirmed_end(variant: str, tmp_path: Path) -> Iterator[_Ended]:
    """A holder of `prod` stopped by `variant` with process confirmation forced to fail, a
    same-environment run queued behind it (restart: the waiter died with the server too)."""
    if variant == "restart":
        with _restarted(tmp_path) as ended:
            yield ended
        return
    kernel = _proc_kernel()
    kernel.control.conductor.stopper = _unconfirmed
    if variant == "deadline":
        with harness.patch_snapshot(kernel, "env_hold", timeout_s=spine.SHORT_DEADLINE_S):
            holder, holder_dir = _start(kernel, "env_hold", env="prod", leave=True)
    else:
        holder, holder_dir = _start(kernel, "env_hold", env="prod", leave=True)
    with _reaped(holder):
        spine.wait_ready(holder_dir)
        waiter, waiter_dir = _start(kernel, "env_hold", env="prod")
        with _reaped(waiter):
            assert not _started(waiter_dir)
            if variant == "cancel":
                kernel.control.cancel(holder)
            bound = spine.SHORT_DEADLINE_S + tolerances.JOIN_WAIT_S + clock.stop_bound
            assert spine.wait_until(lambda: _ended(holder_dir), bound)
            yield _Ended(kernel, holder_dir, waiter_dir)
            _release(waiter_dir)
            assert spine.wait_until(lambda: _ended(waiter_dir), tolerances.JOIN_WAIT_S)


@contextmanager
def _restarted(tmp_path: Path) -> Iterator[_Ended]:
    """The server dies with a holder in flight and its queued waiter; the restart's recovery
    cannot confirm the holder's processes gone (nothing signalled, members it did not record)."""
    kernel = _proc_kernel()
    holder = spine.admit_order(kernel, "env_hold", {"env": "prod"})
    holder_dir = spine.run_dir_of(kernel, holder.run_id)
    waiter = spine.admit_order(kernel, "env_hold", {"env": "prod"})
    ledger = RunLedger.open(ledger_path(holder_dir))
    ledger.append("admitted", run_id=holder.run_id, snapshot_id=holder.snapshot_id)
    ledger.append("started", run_id=holder.run_id)
    recorded = Identity(pid=100, boot="boot-a", start=spine.START, group=100, leader=True)
    ledger.append("process_identity", run_id=holder.run_id, **recorded.fields())
    host = FakeHost()
    host.add(104, 1, 100, start=spine.START + 600)  # in the group, but started after the record
    kernel.ownership.drop_all()  # the server died: the kernel dropped its owner locks
    reap_home(kernel.home, source=host, signaller=host)
    assert host.sent == []
    restarted = create_kernel(home=kernel.home, plugin_dirs=[PLUGINS], skip_recovery=True)
    yield _Ended(restarted, holder_dir, spine.run_dir_of(kernel, waiter.run_id))


@pytest.mark.parametrize("variant", VARIANTS)
def test_lease_ends_at_terminal_row_even_unconfirmed(
    variant: str, tmp_path: Path, short_stop: None
) -> None:
    with _unconfirmed_end(variant, tmp_path) as ended:
        kinds = _kinds(ended.holder_dir)
        assert (
            kinds[-1]
            == {"restart": "interrupted", "cancel": "cancelled", "deadline": "timed_out"}[variant]
        )
        (group_stop,) = spine.rows_of(ended.holder_dir, "group_stop")
        assert group_stop["confirmed_gone"] is False  # the confirmation failed in every variant
        # the answer reports the stop unconfirmed, never clean, and says the lease ended anyway
        cleanup = ended.cleanup()
        assert cleanup["lease_ended_unconfirmed"] is True
        assert cleanup["clean"] is False and cleanup["group_confirmed_gone"] is False
        assert ended.view().cleanup is not None and ended.view().cleanup.processes == "unknown"
        # the lease ends at the terminal row (B2-C10, OQ-34): defined over the ledger, so no run
        # of this environment is held, whatever the stop's confirmation said
        assert lease.holder_of(ended.holder_dir) is None
        assert ended.holder_dir.name not in {
            h.run_id for h in ended.kernel.control.admission.holders.held('"prod"')
        }


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:later-run-admitted-after-answer", "core", "single", "PROC", "CI"
)
@pytest.mark.parametrize("variant", VARIANTS)
def test_later_same_env_run_admitted_after_answer(
    variant: str, tmp_path: Path, short_stop: None
) -> None:
    with _unconfirmed_end(variant, tmp_path) as ended:
        kernel = ended.kernel
        answer_file = ended.holder_dir / "evidence" / "answer.json"
        if variant != "restart":
            # the queued waiter starts once, and only after the holder's answer and terminal row
            # are durable: when its `started` row exists, the holder's terminal row is there
            waiter_dir = ended.waiter_dir
            assert waiter_dir is not None
            assert spine.wait_until(lambda: _started(waiter_dir), tolerances.JOIN_WAIT_S)
            assert _ended(ended.holder_dir) and answer_file.exists()
        else:
            # a run of the same environment after the restart is admitted (not busy) and runs
            later = kernel.control.run(
                "env_quick",
                {"env": "prod"},
                wait_ms=tolerances.HARNESS_WAIT_MS,
                completion="bounded",
            )
            assert isinstance(later, RunView) and later.state == "succeeded", later
            assert answer_file.exists()
        # and a fresh admission of the environment is not refused busy either
        result = kernel.control.admission.admit(
            AdmitRequest(plugin="env_quick", args={"env": "prod"})
        )
        assert isinstance(result, AdmitResultAdmitted), result


def test_key_released_in_finally() -> None:
    """A drive that raises still frees the environment key: the run behind it starts."""
    kernel = _proc_kernel()
    scheduler = kernel.control.scheduler
    conductor = kernel.control.conductor
    first = spine.admit_order(kernel, "env_quick", {"env": "prod"})
    second = spine.admit_order(kernel, "env_quick", {"env": "prod"})
    started: list[str] = []
    scheduler.on_dispatch = lambda order: started.append(order.run_id)
    scheduler.enqueue(first, conductor.admitted_deadline(first), key='"prod"')
    scheduler.enqueue(second, conductor.admitted_deadline(second), key='"prod"')
    assert started == [first.run_id] and scheduler.running_keys == {first.run_id: '"prod"'}

    def boom(order: object) -> str:
        raise RuntimeError("the drive failed before any terminal row")

    conductor._drive = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        conductor.drive(first)  # the finally releases the slot and the key
    assert started == [first.run_id, second.run_id]
    assert scheduler.running_keys == {second.run_id: '"prod"'}
    # the failed run no longer holds the lease in the index either
    assert scheduler.holders is not None
    assert first.run_id not in {h.run_id for h in scheduler.holders.held()}
