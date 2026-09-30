"""The local-process restart family's conformance cases (L.RB-8.1; B3-C3, B3-C4, B3-C5, B3-C16,
B3-C21 Local Process Supervision, WR-ENV-7:restart-mechanism-adapter).

`ResourceOwned.restart` on a local process is one ticketed repair on the same handle: stop, read
the selector observed absent (V-3.8), launch again from the handle's recorded spec under the same
run-scoped selector and bound command, in the run's group, never detached. Registered through the
suite core's `register_family` as family `local-process-restart` and run, UNMODIFIED, by
`run_family` against the fake (`FakeLocalProcess`) and the real `LocalProcessPort`, beside SL-3's
`Local Process Supervision` family (which covers create, observe, stop and the shape of a repair;
this one is what the restart of an owned app adds). A case reaches the implementation only through
the port's members and the factory's `Implementation.extras`, its fixture contract:

- `spec`: an `AGENT_LAUNCHED_PROJECT` `ResourceSpec` whose bound command declares a `PORT`;
  `lifetimes`: the creation lifetimes supported (`run`);
- `plant_found(system)`: start a process that runs the spec's command line and is not this port's
  instance, and return its found selector;
- `token_of(selector)`: a token naming the process behind a selector, or `None`; `alive(token)`:
  whether that very process is running; `in_run_group(token)`: whether it is in the run's process
  group and session (an instance the port launched); `kill(selector)`: end the instance out of
  band, as a crash would;
- `events(starts)`: the order in which the bound command started and ended (`start` / `stop`),
  once it has started `starts` times (an implementation whose command starts asynchronously waits,
  bounded, for that many starts);
- `break_launch()`: from now on the bound command can no longer be started;
- the implementation's `reach.engine_inventory`: `containers` is the set of live selectors.

No case branches on which implementation it runs against (`test_cases_unbranched`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tests.proof.suites.ports import core
from tests.proof.suites.ports.families import LINEAGE, effect_call, read, status, ticket
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Vantage
from trestle.workflow.values import ConfirmationStatus, CreatedHandle, OwnedHandle

FAMILY = "local-process-restart"
EFFECT = "up"


def _spec(built: core.Implementation) -> Any:
    return built.extras["spec"]


def _live(built: core.Implementation) -> frozenset[str]:
    assert built.reach.engine_inventory is not None
    return frozenset(built.reach.engine_inventory()["containers"])


def _observe(built: core.Implementation) -> Any:
    with read(built, "ResourceReads.observe"):
        return built.impl.observe(_spec(built), LINEAGE, EFFECT)


def _create(built: core.Implementation) -> CreatedHandle:
    made = ticket(EFFECT, EffectFacetClass.CREATE, Lifetime.RUN)
    conf = built.impl.create(_spec(built), made)
    assert status(conf) is ConfirmationStatus.APPLIED and conf.identity
    call = effect_call("create", {"spec": _spec(built)}, Lifetime.RUN, effect=EFFECT)
    descriptor = ports.as_descriptor(built.impl.release_descriptor(call))
    # a launched app has started (and can be told to end) only once it says so: every later step
    # of a case starts from there, so an app is never signalled before it can answer
    assert built.extras["events"](1) == ["start"]
    return CreatedHandle(LINEAGE, EFFECT, conf.identity, descriptor)


def _restart(
    built: core.Implementation, handle: OwnedHandle, effect: str = "restart-effect"
) -> Any:
    return built.impl.restart(handle, ticket(effect, EffectFacetClass.OWNED, Lifetime.RUN))


def _token(built: core.Implementation, selector: str) -> str:
    token = built.extras["token_of"](selector)
    assert token is not None, selector
    return str(token)


def restart_relaunches_the_same_handle_under_the_same_selector(built: core.Implementation) -> None:
    handle = _create(built)
    old = _token(built, handle.selector)
    again = _restart(built, handle)
    assert status(again) is ConfirmationStatus.APPLIED and again.identity == handle.selector
    seen = _observe(built)
    assert seen.selector_present is True and seen.selector_ref.selector == handle.selector
    assert seen.identity_proven is True and seen.code is None
    new = _token(built, handle.selector)
    assert new != old  # a new process, under the one selector
    assert _live(built) == {handle.selector}  # one instance, never two


def restart_ends_the_old_process_before_the_new_one_starts(built: core.Implementation) -> None:
    handle = _create(built)
    old = _token(built, handle.selector)
    _restart(built, handle)
    assert built.extras["alive"](old) is False  # observed absent, not merely signalled
    assert built.extras["alive"](_token(built, handle.selector)) is True
    # the old one ended before the next one began: start, stop, start (never start, start, stop)
    assert built.extras["events"](2) == ["start", "stop", "start"]


def restart_stays_in_the_run_group_and_is_never_detached(built: core.Implementation) -> None:
    handle = _create(built)
    assert built.extras["in_run_group"](_token(built, handle.selector)) is True
    _restart(built, handle)
    assert built.extras["in_run_group"](_token(built, handle.selector)) is True  # per ancestry


def restart_is_a_repair_carrying_the_handles_own_descriptor(built: core.Implementation) -> None:
    handle = _create(built)
    call = effect_call("restart", {"target": handle}, effect="restart-effect")
    form = ports.as_descriptor(built.impl.release_descriptor(call))
    assert form == handle.release  # the handle's own, unchanged (B3-C5, V-5.4 item 2)
    assert isinstance(form, ports.InRunGroup)  # never a release form of its own
    policy = built.impl.launch_policy(_spec(built))
    assert form.helpers_disclosed == (policy.helpers == ports.Helpers.DISCLOSED)
    _restart(built, handle)
    assert ports.as_descriptor(built.impl.release_descriptor(call)) == form  # equal across attempts
    assert _observe(built).selector_present is True  # a repair, not a release: still one target


def restart_identity_is_the_recorded_command_and_never_port_occupancy(
    built: core.Implementation,
) -> None:
    planted = built.extras["plant_found"](_spec(built).logical_system)
    handle = _create(built)
    _restart(built, handle)
    seen = _observe(built)
    assert seen.selector_present is True and seen.selector_ref.selector == handle.selector
    # the process that runs the same command line is still only found: never taken for the instance
    assert [f.selector for f in seen.found] == [planted]
    assert built.extras["alive"](_token(built, planted)) is True  # and never signalled
    with read(built, "ResourceReads.endpoint"):
        endpoint = built.impl.endpoint(seen.selector_ref, Vantage.HOST)
    declared = int(_spec(built).command.environment["PORT"])
    assert (endpoint.host, endpoint.port) == ("127.0.0.1", declared)  # the recorded endpoint


def restart_of_a_selector_the_port_does_not_hold_is_not_applied(built: core.Implementation) -> None:
    planted = built.extras["plant_found"](_spec(built).logical_system)
    token = _token(built, planted)
    for selector in (planted, "proc-0000000000000000"):
        stranger = OwnedHandle(LINEAGE, "x", selector, ports.InRunGroup())
        refused = _restart(built, stranger)
        assert status(refused) is ConfirmationStatus.NOT_APPLIED and refused.identity is None
    assert built.extras["alive"](token) is True  # nothing was signalled
    assert _live(built) == frozenset()  # nothing was started


def a_restart_that_cannot_relaunch_is_never_not_applied(built: core.Implementation) -> None:
    handle = _create(built)
    old = _token(built, handle.selector)
    built.extras["break_launch"]()
    result = _restart(built, handle)
    assert built.extras["alive"](old) is False  # the old process was stopped: something changed
    assert status(result) is ConfirmationStatus.UNKNOWN  # so no NOT_APPLIED (B3-C16), nor APPLIED
    assert _observe(built).selector_present is False  # and nothing runs under the selector


def a_process_that_died_on_its_own_is_restarted(built: core.Implementation) -> None:
    handle = _create(built)
    built.extras["kill"](handle.selector)
    assert _observe(built).selector_present is False  # stale: the owned process is gone
    again = _restart(built, handle)
    assert status(again) is ConfirmationStatus.APPLIED and again.identity == handle.selector
    assert _observe(built).selector_present is True
    assert built.extras["events"](2) == ["start", "start"]  # a crash logs no stop


def repeated_restarts_converge_on_one_instance(built: core.Implementation) -> None:
    handle = _create(built)
    for n in range(3):
        again = _restart(built, handle, f"restart-{n}")
        assert status(again) is ConfirmationStatus.APPLIED
        assert len(built.extras["events"](n + 2)) == 2 * (n + 1) + 1  # the new one has started
    assert _live(built) == {handle.selector}
    assert built.extras["events"](4) == ["start", "stop"] * 3 + ["start"]


def a_restarted_instance_is_released_by_stop(built: core.Implementation) -> None:
    handle = _create(built)
    _restart(built, handle)
    assert built.extras["events"](2) == ["start", "stop", "start"]
    stopped = built.impl.stop(handle, ticket("stop-effect", EffectFacetClass.OWNED, Lifetime.RUN))
    assert status(stopped) is ConfirmationStatus.APPLIED
    assert _observe(built).selector_present is False  # released means observed absent (V-3.8)
    assert built.extras["events"](2) == ["start", "stop", "start", "stop"]
    assert _live(built) == frozenset()


CASES: Sequence[core.Case] = (
    core.Case(
        "restart_relaunches_the_same_handle_under_the_same_selector",
        restart_relaunches_the_same_handle_under_the_same_selector,
    ),
    core.Case(
        "restart_ends_the_old_process_before_the_new_one_starts",
        restart_ends_the_old_process_before_the_new_one_starts,
    ),
    core.Case(
        "restart_stays_in_the_run_group_and_is_never_detached",
        restart_stays_in_the_run_group_and_is_never_detached,
    ),
    core.Case(
        "restart_is_a_repair_carrying_the_handles_own_descriptor",
        restart_is_a_repair_carrying_the_handles_own_descriptor,
    ),
    core.Case(
        "restart_identity_is_the_recorded_command_and_never_port_occupancy",
        restart_identity_is_the_recorded_command_and_never_port_occupancy,
    ),
    core.Case(
        "restart_of_a_selector_the_port_does_not_hold_is_not_applied",
        restart_of_a_selector_the_port_does_not_hold_is_not_applied,
    ),
    core.Case(
        "a_restart_that_cannot_relaunch_is_never_not_applied",
        a_restart_that_cannot_relaunch_is_never_not_applied,
    ),
    core.Case(
        "a_process_that_died_on_its_own_is_restarted", a_process_that_died_on_its_own_is_restarted
    ),
    core.Case(
        "repeated_restarts_converge_on_one_instance", repeated_restarts_converge_on_one_instance
    ),
    core.Case("a_restarted_instance_is_released_by_stop", a_restarted_instance_is_released_by_stop),
)

core.register_family(FAMILY, CASES)
