"""L.SL-3.3: the real local-process resource port (B3-C1..C5). The shared suite runs against it
(`[real-local]`); these tests read the processes themselves: where they run, what identifies
them, and what the port never touches."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from trestle_packs.process import identity
from trestle_packs.process.local import LocalProcessPort, found_selector, run_scoped_selector

from tests.proof.suites.ports import families
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime, RealizationKind, Vantage
from trestle.workflow.values import ConfirmationStatus, CreatedHandle, SelectorRef

HOLDER = "import signal; signal.pause()  # trestle local port test holder"
RESOLVED = ports.Resolved(sys.executable, "3.12", "pin", "adoption")
LINEAGE = families.LINEAGE


def _spec(program: str = HOLDER, **environment: str) -> ports.ResourceSpec:
    command = ports.BoundCommand(
        "app", (sys.executable, "-c", program), environment, RESOLVED, False
    )
    return ports.ResourceSpec("proc", RealizationKind.AGENT_LAUNCHED_PROJECT, "app", command)


@pytest.fixture
def port() -> Iterator[LocalProcessPort]:
    made = LocalProcessPort()
    try:
        yield made
    finally:
        made.close()


def _create(port: LocalProcessPort, spec: ports.ResourceSpec, effect: str = "up") -> str:
    made = port.create(spec, families.ticket(effect, EffectFacetClass.CREATE))
    assert made.status is ConfirmationStatus.APPLIED and made.identity is not None
    return made.identity


def _pid(port: LocalProcessPort, selector: str) -> int:
    return port._instances[selector].proc.pid  # noqa: SLF001 - the process the port recorded


def test_launch_runs_in_the_run_group_and_session(port: LocalProcessPort, tmp_path: Path) -> None:
    seen = tmp_path / "ids.txt"
    staged = tmp_path / "ids.txt.tmp"
    # the child writes the whole record to a staging file and renames it into place, so
    # `seen` never exists half-written (open() creates it empty before the write lands)
    program = (
        "import os, signal, sys; "
        f"f = open({str(staged)!r}, 'w'); f.write(f'{{os.getpgid(0)}} {{os.getsid(0)}}'); "
        f"f.close(); os.replace({str(staged)!r}, {str(seen)!r}); signal.pause()"
    )
    selector = _create(port, _spec(program))
    fields: list[str] = []
    while len(fields) != 2:  # the child writes once it is up; wait for both ids
        assert port._instances[selector].proc.poll() is None  # noqa: SLF001
        fields = seen.read_text().split() if seen.exists() else []
    pgid, sid = (int(v) for v in fields)
    assert (pgid, sid) == (os.getpgid(0), os.getsid(0))  # never detached (B3-C4, V-2.3)


def test_identity_is_the_numeric_start_time_not_ps_text(port: LocalProcessPort) -> None:
    selector = _create(port, _spec())
    pid = _pid(port, selector)
    start = identity.start_time(pid)
    assert isinstance(start, int) and start > 0
    assert identity.start_time(pid) == start  # the same integer every time
    assert identity.start_time(2**22 + 12345) is None  # no such process reads as gone
    seen = port.observe(_spec(), LINEAGE, "up")
    assert seen.selector_present and seen.identity_proven


def test_stop_ends_only_the_recorded_child_and_leaves_a_found_process_alone(
    port: LocalProcessPort,
) -> None:
    spec = _spec()
    found = subprocess.Popen(spec.command.argv, stdin=subprocess.DEVNULL)  # type: ignore[union-attr]
    try:
        start = identity.start_time(found.pid)
        assert start is not None
        selector = _create(port, spec)
        seen = port.observe(spec, LINEAGE, "up")
        assert [f.selector for f in seen.found] == [found_selector(found.pid, start)]
        handle = CreatedHandle(LINEAGE, "up", selector, ports.InRunGroup())
        stopped = port.stop(handle, families.ticket("stop", EffectFacetClass.OWNED))
        assert stopped.status is ConfirmationStatus.APPLIED
        assert found.poll() is None  # the found process was never signalled
        assert identity.start_time(found.pid) == start
        # a handle for a selector this port does not hold: nothing signalled, nothing changed
        stranger = CreatedHandle(
            LINEAGE, "ghost", found_selector(found.pid, start), ports.InRunGroup()
        )
        refused = port.stop(stranger, families.ticket("stop2", EffectFacetClass.OWNED))
        assert refused.status is ConfirmationStatus.NOT_APPLIED
        assert found.poll() is None
    finally:
        found.kill()
        found.wait()


def test_restart_relaunches_under_the_same_selector(port: LocalProcessPort) -> None:
    spec = _spec()
    selector = _create(port, spec)
    before = _pid(port, selector)
    handle = CreatedHandle(LINEAGE, "up", selector, ports.InRunGroup())
    for member in ("restart", "recreate"):
        again = getattr(port, member)(handle, families.ticket(member, EffectFacetClass.OWNED))
        assert again.status is ConfirmationStatus.APPLIED and again.identity == selector
        after = _pid(port, selector)
        assert after != before  # a new process, observed after the old one was absent
        before = after
    assert port.observe(spec, LINEAGE, "up").selector_present
    assert port.inventory()["containers"] == {selector}


def test_a_dead_process_reads_absent_and_create_launches_again(port: LocalProcessPort) -> None:
    spec = _spec()
    selector = _create(port, spec)
    port._instances[selector].proc.kill()  # noqa: SLF001 - the process dies by itself
    port._instances[selector].proc.wait()  # noqa: SLF001
    assert not port.observe(spec, LINEAGE, "up").selector_present
    gone = SelectorRef(LINEAGE, "up", selector, datetime.now(UTC))
    assert not port.check("ready", gone).satisfied
    assert _create(port, spec) == selector  # the same selector, a fresh process
    assert port.observe(spec, LINEAGE, "up").selector_present


def test_endpoint_is_the_declared_port_never_a_probe(port: LocalProcessPort) -> None:
    spec = _spec(PORT="20555")
    selector = _create(port, spec)
    ref = port.observe(spec, LINEAGE, "up").selector_ref
    assert ref is not None and ref.selector == selector
    assert port.endpoint(ref, Vantage.HOST) == ports.Endpoint("http", "127.0.0.1", 20555)
    refused = port.endpoint(ref, Vantage.CONTAINER)
    assert isinstance(refused, ports.RouteRefused)
    undeclared = _spec()
    other = _create(port, undeclared, "other")
    ref2 = port.observe(undeclared, LINEAGE, "other").selector_ref
    assert ref2 is not None and ref2.selector == other
    assert isinstance(port.endpoint(ref2, Vantage.HOST), ports.RouteRefused)


def test_launch_preconditions_raise_before_any_change(port: LocalProcessPort) -> None:
    ticket = families.ticket("up", EffectFacetClass.CREATE)
    no_command = ports.ResourceSpec("p", RealizationKind.AGENT_LAUNCHED_PROJECT, "app", None)
    provisioned = ports.ResourceSpec("p", RealizationKind.PROVISIONED, "app", _spec().command)
    wrong = ports.BoundCommand("app", ("/bin/echo",), {}, RESOLVED, False)
    mismatch = ports.ResourceSpec("p", RealizationKind.AGENT_LAUNCHED_PROJECT, "app", wrong)
    for bad in (no_command, provisioned, mismatch):
        with pytest.raises(ValueError):
            port.create(bad, ticket)
    durable = families.ticket("up", EffectFacetClass.CREATE, Lifetime.DURABLE)
    with pytest.raises(ValueError, match="DURABLE"):
        port.create(_spec(), durable)
    assert port.inventory()["containers"] == frozenset()
    assert run_scoped_selector(LINEAGE, "up").startswith("proc-")
