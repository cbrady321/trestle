"""L.SL-5.2: the shared read-only check suite (A4.4, V-5.1, B3-C17, B3-C20).

One family, `Read-Only Checks`, registered once through `register_family` and run UNMODIFIED
against every implementation of the read protocol `ResourceReads` -- `[fake-marker]`,
`[fake-local]` and `[real-local]`. Each case calls a read operation (`observe`, `check`,
`endpoint`) under the L.SV-5.15 read-facet watcher with the implementation's own reach
(`Implementation.reach`, never chosen here): a filesystem snapshot diff, the MC-13 process diff, the
network log and the engine inventory. A read may write only what `INCIDENTAL_WRITES[operation]`
allows (nothing, for all three operations), and it sends no signal (B3-I2): the suite records every
`os.kill` / `os.killpg` around the call and requires the instance it created to be alive still.

This file is the suite: its sha256 is recorded at registration and re-checked at every run, and
`test_one_suite_file_over_every_implementation` asserts the same hash and the same cases ran on all
three. The implementations come from `tests/proof/suites/ports/implementations.py` (their fixture
contract: `spec`, `lifetimes`, `plant_found`); the only thing added here is the base directory as
one more filesystem root, so a real adapter's stray write under it is seen too.

Executor-chosen values (the contract names none): "no signal" is `os.kill` and `os.killpg` not
called at all (signal 0 included) during a read; the filesystem root added for every implementation
is the implementation's base directory.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker
from trestle_packs.process.local import LocalProcessPort

from tests.proof.suites.ports import core, implementations
from tests.proof.suites.ports.families import LINEAGE, ticket
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Vantage
from trestle.workflow.values import ConfirmationStatus, SelectorRef

FAMILY = "Read-Only Checks"
IMPLEMENTATION_IDS = ("fake-marker", "fake-local", "real-local")
READ_OPERATIONS = ("ResourceReads.observe", "ResourceReads.check", "ResourceReads.endpoint")
NEVER_CREATED = SelectorRef(LINEAGE, "never", "no-such-selector", datetime(2026, 9, 30, tzinfo=UTC))


class SignalSent(AssertionError):
    """A read sent a signal (B3-I2)."""


@contextmanager
def _no_signal() -> Iterator[None]:
    sent: list[tuple[str, tuple[Any, ...]]] = []
    kill, killpg = os.kill, os.killpg

    def record(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def inner(*args: Any) -> Any:
            sent.append((name, args))
            return original(*args)

        return inner

    os.kill, os.killpg = record("kill", kill), record("killpg", killpg)
    try:
        yield
    finally:
        os.kill, os.killpg = kill, killpg
    if sent:
        raise SignalSent(f"a read sent {sent}")


@contextmanager
def _read(built: core.Implementation, operation: str) -> Iterator[None]:
    """One read call under the V-5.1 watcher, the port's own reach and the no-signal watcher."""
    with _no_signal(), core.watch(operation, built.reach):
        yield


def _create(built: core.Implementation) -> Any:
    lifetime = Lifetime(built.extras["lifetimes"][0])
    conf = built.impl.create(
        built.extras["spec"], ticket("up", EffectFacetClass.CREATE, lifetime, 1)
    )
    assert ConfirmationStatus(getattr(conf.status, "value", conf.status)) is (
        ConfirmationStatus.APPLIED
    )
    return conf


def _observe(built: core.Implementation) -> Any:
    with _read(built, "ResourceReads.observe"):
        return built.impl.observe(built.extras["spec"], LINEAGE, "up")


def _shape(seen: Any) -> tuple[Any, ...]:
    """What an observation says, without the instants it was taken at."""
    ref = seen.selector_ref.selector if seen.selector_ref is not None else None
    return (
        seen.selector_present,
        ref,
        seen.identity_proven,
        tuple(f.selector for f in seen.found),
        seen.code,
    )


def _alive(built: core.Implementation, selector: str) -> bool:
    return selector in built.reach.engine_inventory()["containers"]


# ------------------------------------------------------------------------------- the cases


def read_protocol_declares_no_write(built: core.Implementation) -> None:
    assert core.read_protocol_violations(ports.ResourceReads) == []
    for operation in READ_OPERATIONS:
        assert ports.INCIDENTAL_WRITES[operation] == frozenset()  # B3-C20: nothing is allowed
        assert core.allowed_patterns(operation, built.reach) == ()  # ...and no region opened up


def observe_absent_writes_nothing(built: core.Implementation) -> None:
    seen = _observe(built)
    assert seen.selector_present is False and seen.selector_ref is None


def observe_present_leaves_the_instance_running(built: core.Implementation) -> None:
    conf = _create(built)
    first, second = _observe(built), _observe(built)
    assert first.selector_present and first.selector_ref.selector == conf.identity
    assert _shape(first) == _shape(second)
    assert _alive(built, conf.identity)  # observing it did not end it (no signal, B3-I2)


def observe_found_instance_touches_nothing(built: core.Implementation) -> None:
    planted = built.extras["plant_found"](built.extras["spec"].logical_system)
    first, second = _observe(built), _observe(built)
    assert [f.selector for f in first.found] == [planted]
    assert _shape(first) == _shape(second)  # a found instance is read, never repaired or ended


def check_and_endpoint_write_nothing(built: core.Implementation) -> None:
    conf = _create(built)
    ref = _observe(built).selector_ref
    for _ in range(2):
        with _read(built, "ResourceReads.check"):
            assert built.impl.check("ready", ref).satisfied is True
        for vantage in Vantage:
            with _read(built, "ResourceReads.endpoint"):
                answer = built.impl.endpoint(ref, vantage)
            assert answer is not None
    assert _alive(built, conf.identity)


def check_on_a_target_that_never_existed(built: core.Implementation) -> None:
    with _read(built, "ResourceReads.check"):
        assert built.impl.check("ready", NEVER_CREATED).satisfied is False
    with _read(built, "ResourceReads.endpoint"):
        built.impl.endpoint(NEVER_CREATED, Vantage.HOST)


def reads_are_repeatable(built: core.Implementation) -> None:
    _create(built)
    shapes = {_shape(_observe(built)) for _ in range(3)}
    assert len(shapes) == 1  # three reads, one answer: a read has no effect on what it reads


CASES = (
    core.Case("read_protocol_declares_no_write", read_protocol_declares_no_write),
    core.Case("observe_absent", observe_absent_writes_nothing),
    core.Case("observe_present_keeps_instance", observe_present_leaves_the_instance_running),
    core.Case("observe_found_untouched", observe_found_instance_touches_nothing),
    core.Case("check_endpoint_write_nothing", check_and_endpoint_write_nothing),
    core.Case("check_never_existed", check_on_a_target_that_never_existed),
    core.Case("reads_repeatable", reads_are_repeatable),
)
core.register_family(FAMILY, CASES)

# ------------------------------------------------------------------------------- implementations


def _with_base(built: core.Implementation, base: Path) -> core.Implementation:
    """The implementation's own reach, plus its base directory as a watched filesystem root."""
    reach = dataclasses.replace(built.reach, fs_roots=(*built.reach.fs_roots, base))
    return dataclasses.replace(built, reach=reach)


# The real local process is found by its command line, host-wide: this suite's holder carries a
# tag of its own (this process's pid), so a parallel worker running the ports suite's holder is not
# one of its found instances, nor it one of theirs.
HOLDER_TAG = f"read-only-checks-{os.getpid()}"


def implementation(impl_id: str, base: Path) -> core.Implementation:
    if impl_id == "real-local":
        return _with_base(implementations.real_local(base, tag=HOLDER_TAG), base)
    _, factory = implementations.IMPLEMENTATIONS[impl_id]
    return _with_base(factory(base), base)


@pytest.mark.parametrize("impl_id", IMPLEMENTATION_IDS)
def test_readonly_suite(impl_id: str, tmp_path: Path) -> None:
    """The unmodified suite passes: no write outside INCIDENTAL_WRITES, no process, no signal."""
    run = core.run_family(FAMILY, lambda: implementation(impl_id, tmp_path))
    assert run.implementation == impl_id
    assert run.suite_sha256 == core.REGISTRY.families[FAMILY].suite_sha256
    assert set(run.cases_run) == {c.name for c in CASES}


def test_one_suite_file_over_every_implementation(tmp_path: Path) -> None:
    """MJ.SL-5: the read-only suite file's sha256 is the same for `[fake-marker]`, `[fake-local]`
    and `[real-local]`, and the same cases ran on each."""
    suite = core.sha256_of(Path(__file__))
    runs = [
        core.run_family(FAMILY, lambda i=impl_id: implementation(i, tmp_path))
        for impl_id in IMPLEMENTATION_IDS
    ]
    assert [r.implementation for r in runs] == list(IMPLEMENTATION_IDS)
    assert {r.suite_sha256 for r in runs} == {suite}
    assert len({r.cases_run for r in runs}) == 1
    assert core.REGISTRY.families[FAMILY].suite_file == Path(__file__)


def test_every_read_operation_is_exercised_and_watched() -> None:
    """The suite reaches all three read operations and none is allowed a write."""
    source = Path(__file__).read_text(encoding="utf-8")
    for operation in READ_OPERATIONS:
        assert f'"{operation}"' in source
        assert ports.INCIDENTAL_WRITES[operation] == frozenset()


# ------------------------------------------------------------------------------- planted defects


class _WritingObserve(FakeMarker):
    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        (self.root / "cache.tmp").write_text("a read that writes", encoding="utf-8")
        return super().observe(spec, lineage, effect)


class _SignallingCheck(FakeMarker):
    def check(self, check: Any, target: Any) -> Any:
        os.kill(os.getpid(), 0)
        return super().check(check, target)


class _EndpointLeavesAProcess(FakeMarker):
    children: list[subprocess.Popen[bytes]] = []

    def endpoint(self, target: Any, vantage: Any) -> Any:
        self.children.append(
            subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"])  # noqa: S603
        )
        return super().endpoint(target, vantage)


class _ObserveEndsTheInstance(LocalProcessPort):
    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        seen = super().observe(spec, lineage, effect)
        for instance in list(self._instances.values()):
            self._end(instance)
        return seen


def _fake_marker_with(cls: type[FakeMarker]) -> Callable[[Path], core.Implementation]:
    return lambda base: _with_base(implementations.fake_marker(base, cls), base)


@pytest.mark.parametrize(
    ("factory", "case", "needle"),
    [
        (_fake_marker_with(_WritingObserve), "observe_absent", "filesystem change"),
        (_fake_marker_with(_SignallingCheck), "check_endpoint_write_nothing", "SignalSent"),
        (
            _fake_marker_with(_EndpointLeavesAProcess),
            "check_endpoint_write_nothing",
            "process left",
        ),
        (
            lambda base: _with_base(
                implementations.real_local(base, _ObserveEndsTheInstance, HOLDER_TAG), base
            ),
            "observe_present_keeps_instance",
            "engine inventory changed",
        ),
    ],
    ids=["read-writes", "read-signals", "read-leaves-process", "read-ends-instance"],
)
def test_suite_catches_planted_defects(
    factory: Callable[[Path], core.Implementation], case: str, needle: str, tmp_path: Path
) -> None:
    """The suite is not vacuous: one planted defect fails exactly its case."""
    try:
        with pytest.raises(core.SuiteFailure) as failed:
            core.run_family(FAMILY, lambda: factory(tmp_path))
        message = str(failed.value)
        assert f"{case}:" in message and needle in message
    finally:
        for child in _EndpointLeavesAProcess.children:
            child.kill()
            child.wait()
        _EndpointLeavesAProcess.children.clear()
