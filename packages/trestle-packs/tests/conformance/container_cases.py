"""The Container Control family's conformance cases (L.NW-2.4; B3-C1..C6, B3-C17, MC-B-01).

Registered through the suite core's `register_family` (L.SV-5.15) as family `container` and run,
UNMODIFIED, by `run_family` against every implementation: the stdlib fake engine and the real
Docker adapter (WR-PROOF-4). A case reaches the implementation only through the port's members
and the implementation factory's `Implementation.extras`, its fixture contract:

- `spec`: a `DOCKER_SERVICE` `ResourceSpec` whose `logical_system` is the name a found instance runs
  under; `lifetimes`: the creation lifetimes supported (`run`, `durable`);
- `executable`, `endpoint`: the docker path and the bound endpoint the descriptors must carry;
- `plant_found(system, running=True)`: make a container the root's selector does not name (its name
  is `system`) and return its name; `seed_volume(name)`: a named volume that must survive;
- `run_argv(argv) -> (exit_status, stdout)`: run a descriptor's argv against the engine as the host
  sweep does (the engine the implementation is bound to);
- `unreachable()` and `missing_cli()`: an `Implementation` bound to an engine that does not answer,
  and to a docker executable that does not exist, each with its own `run_argv` extra;
- `cancel_root()`: the root's cancel signal goes up on the implementation (WR-CANCEL-5's adapter
  half: a docker call the adapter starts ends on a root cancel and changes nothing);
- the implementation's `reach.engine_inventory`: containers, images, volumes and networks by name
  (B3-C17 (3)), which the read-facet watcher compares around every read.

No case branches on which implementation it runs against (`test_cases_unbranched`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from tests.proof.suites.ports import core
from tests.proof.suites.ports.families import (
    RELEASE_TIMEOUT,
    read,
    status,
)
from trestle.common.plan import bounds
from trestle.workflow import ports
from trestle.workflow import services as svc
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat, Vantage
from trestle.workflow.values import (
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    Lineage,
    NodePath,
)

FAMILY = "container"
ROOT_RUN = "r_suite_0001"
DOCKER_CLI_MISSING = "adapter.docker_cli_missing"
DOCKER_ENGINE_UNREACHABLE = "adapter.docker_engine_unreachable"
EXECUTION_CANCELLED = "execution.cancelled"
ROUTE_UNSUPPORTED = "admission.route_unsupported"
VOLUME = "suite-seeded-volume"
FORBIDDEN_FLAGS = frozenset({"-v", "--volumes", "-f", "--force", "--volumes-from"})


def lineage(*segments: str) -> Lineage:
    return Lineage(ROOT_RUN, NodePath(segments or ("suite",)))


def selector_of(lin: Lineage) -> str:
    """MC-B-01, written out independently of any implementation: `trwr-<root run_id>-<path>`."""
    return f"trwr-{lin.root_run_id}-{'.'.join(lin.path.segments)}"


def effect_call(
    lin: Lineage, member: str, arguments: dict[str, Any], lifetime: Lifetime = Lifetime.RUN
) -> ports.EffectCall:
    return ports.EffectCall(member, arguments, lin, "up", lifetime, RELEASE_TIMEOUT)


def ticket_for(
    lin: Lineage,
    facet: EffectFacetClass,
    lifetime: Lifetime = Lifetime.RUN,
    attempt: int = 1,
    effect: str = "up",
) -> svc.AttemptTicket:
    return svc.AttemptTicket(
        lin, effect, facet, attempt, Repeat.SAFE, lifetime, ports.InRunGroup(), None
    )


def _lifetimes(built: core.Implementation) -> list[Lifetime]:
    return [Lifetime(v) for v in built.extras["lifetimes"]]


def _inventory(built: core.Implementation) -> dict[str, frozenset[str]]:
    assert built.reach.engine_inventory is not None
    return dict(built.reach.engine_inventory())


def _create(
    built: core.Implementation,
    lin: Lineage,
    lifetime: Lifetime = Lifetime.RUN,
    attempt: int = 1,
) -> Any:
    return built.impl.create(
        built.extras["spec"], ticket_for(lin, EffectFacetClass.CREATE, lifetime, attempt)
    )


def _handle(built: core.Implementation, lin: Lineage, identity: str) -> CreatedHandle:
    call = effect_call(lin, "create", {"spec": built.extras["spec"]})
    return CreatedHandle(
        lin, "up", identity, ports.as_descriptor(built.impl.release_descriptor(call))
    )


def _observe(built: core.Implementation, lin: Lineage, effect: str | None = "up") -> Any:
    with read(built, "ResourceReads.observe"):
        return built.impl.observe(built.extras["spec"], lin, effect)


def _owned(built: core.Implementation, lin: Lineage) -> CreatedHandle:
    made = _create(built, lin)
    assert status(made) is ConfirmationStatus.APPLIED
    return _handle(built, lin, made.identity)


def _owned_ticket(lin: Lineage, effect: str) -> svc.AttemptTicket:
    return ticket_for(lin, EffectFacetClass.OWNED, effect=effect)


# ------------------------------------------------------------------------------- the cases


def absent_before_create(built: core.Implementation) -> None:
    lin = lineage("one")
    seen = _observe(built, lin)
    assert seen.selector_present is False and seen.selector_ref is None
    assert seen.code is None and tuple(seen.found) == ()  # observed absent: only with no code
    assert _observe(built, lin, None).selector_present is False  # a node with no CREATE effect


def create_applies_and_is_observed(built: core.Implementation) -> None:
    for life in _lifetimes(built):
        lin = lineage("made", life.value)
        conf = _create(built, lin, life)
        assert status(conf) is ConfirmationStatus.APPLIED and conf.identity
        assert conf.identity == selector_of(lin)  # named exactly by the run-scoped selector
        seen = _observe(built, lin)
        assert seen.selector_present is True and seen.selector_ref is not None
        assert seen.selector_ref.selector == conf.identity
        assert seen.identity_proven is True  # by the declared proof, this root's exact name
        assert seen.code is None


def found_instance_only_in_found(built: core.Implementation) -> None:
    spec = built.extras["spec"]
    lin = lineage("one")
    planted = built.extras["plant_found"](spec.logical_system)
    seen = _observe(built, lin)
    assert seen.selector_present is False and seen.selector_ref is None
    assert [f.selector for f in seen.found] == [planted]
    assert seen.identity_proven is False  # an occupied name is not identity (WR-OWN-7)
    conf = _create(built, lin)
    seen = _observe(built, lin)
    assert seen.selector_present is True and seen.selector_ref.selector == conf.identity
    assert [f.selector for f in seen.found] == [planted]  # the created one is not "found"
    assert seen.identity_proven is True


def found_instance_is_never_touched(built: core.Implementation) -> None:
    spec = built.extras["spec"]
    planted = built.extras["plant_found"](spec.logical_system)
    before = _inventory(built)["containers"]
    assert planted in before
    handle = _owned(built, lineage("one"))
    stopped = built.impl.stop(handle, _owned_ticket(lineage("one"), "stop-one"))
    assert status(stopped) is ConfirmationStatus.APPLIED
    assert _inventory(built)["containers"] == before  # only what this root made came and went


def create_idempotent_per_selector(built: core.Implementation) -> None:
    lin = lineage("one")
    before = _inventory(built)["containers"]
    first = _create(built, lin, attempt=1)  # attempt 1 landed; its confirmation was lost (B3-7)
    again = _create(built, lin, attempt=2)
    assert status(first) is ConfirmationStatus.APPLIED is status(again)
    assert first.identity == again.identity  # the same identity from declared proof
    assert _inventory(built)["containers"] - before == {first.identity}  # one instance


def descriptor_forms_by_lifetime(built: core.Implementation) -> None:
    spec = built.extras["spec"]
    lin = lineage("one")
    durable_form = ports.as_descriptor(
        built.impl.release_descriptor(effect_call(lin, "create", {"spec": spec}, Lifetime.DURABLE))
    )
    assert isinstance(durable_form, ports.Durable)
    run_form = ports.as_descriptor(
        built.impl.release_descriptor(effect_call(lin, "create", {"spec": spec}, Lifetime.RUN))
    )
    assert not isinstance(run_form, ports.Durable)  # RUN never gets Durable
    assert isinstance(run_form, ports.ArgvRelease)  # Docker's RUN form (V-10, B3-C3)
    assert run_form.timeout <= RELEASE_TIMEOUT  # <= call.release_timeout
    assert run_form.remove_argv is not None  # a stopped container is not released


def argv_release_is_the_pinned_shape(built: core.Implementation) -> None:
    """MC-B-01: the exact-name discriminating observe, graceful stop, remove without volumes."""
    lin = lineage("one")
    call = effect_call(lin, "create", {"spec": built.extras["spec"]})
    form = ports.as_descriptor(built.impl.release_descriptor(call))
    assert isinstance(form, ports.ArgvRelease)
    exe, endpoint, name = built.extras["executable"], built.extras["endpoint"], selector_of(lin)
    host = ["--host", endpoint] if endpoint else []
    assert form.executable == exe
    assert form.observe_argv == (
        exe,
        *host,
        "ps",
        "-aq",
        "--no-trunc",
        "--filter",
        f"name=^/{name}$",
    )
    assert form.observe_ok_exit == frozenset({0})
    assert form.stop_argv == (exe, *host, "stop", name)
    assert form.remove_argv == (exe, *host, "rm", name)
    for argv in (form.observe_argv, form.stop_argv, form.remove_argv):
        assert not FORBIDDEN_FLAGS & set(argv), argv  # never a volume (X-11), never --force
        assert "inspect" not in argv  # exits 1 for absent and unreachable alike
    encoded = len(form.executable) + sum(
        len(a) for argv in (form.observe_argv, form.stop_argv, form.remove_argv) for a in argv
    )
    assert encoded <= bounds.ARGV_RELEASE_MAX


def descriptor_equal_and_before_the_effect(built: core.Implementation) -> None:
    spec = built.extras["spec"]
    before = _inventory(built)
    for life in _lifetimes(built):
        call = effect_call(lineage("one"), "create", {"spec": spec}, life)
        first = ports.as_descriptor(built.impl.release_descriptor(call))
        assert ports.as_descriptor(built.impl.release_descriptor(call)) == first
    assert _inventory(built) == before  # deriving a descriptor touched nothing


def descriptor_addresses_only_the_run_scoped_selector(built: core.Implementation) -> None:
    spec = built.extras["spec"]
    seen: set[tuple[str, ...]] = set()
    for path in (("one",), ("two", "child")):
        lin = lineage(*path)
        form = ports.as_descriptor(
            built.impl.release_descriptor(effect_call(lin, "create", {"spec": spec}))
        )
        assert isinstance(form, ports.ArgvRelease)
        assert form.stop_argv[-1] == selector_of(lin)
        assert form.remove_argv[-1] == selector_of(lin)
        seen.add(form.remove_argv)
    assert len(seen) == 2  # two lineages, two selectors


def owned_members_carry_the_handles_release(built: core.Implementation) -> None:
    lin = lineage("one")
    handle = _handle(built, lin, selector_of(lin))
    for member in ("restart", "recreate", "stop"):
        call = effect_call(lin, member, {"target": handle})
        assert ports.as_descriptor(built.impl.release_descriptor(call)) == handle.release


def safe_start_descriptor_is_durable(built: core.Implementation) -> None:
    found = FoundRef("docker_container", "pre-existing", datetime.now(UTC))
    call = effect_call(lineage("one"), "start", {"target": found})
    form = ports.as_descriptor(built.impl.release_descriptor(call))
    assert isinstance(form, ports.Durable)  # a SafeStartFacet member is always Durable


def argv_release_sweep_leaves_a_seeded_volume(built: core.Implementation) -> None:
    lin = lineage("one")
    built.extras["seed_volume"](VOLUME)
    conf = _create(built, lin)
    form = ports.as_descriptor(
        built.impl.release_descriptor(effect_call(lin, "create", {"spec": built.extras["spec"]}))
    )
    assert isinstance(form, ports.ArgvRelease)
    run_argv = built.extras["run_argv"]
    code, out = run_argv(form.observe_argv)
    assert code in form.observe_ok_exit and out.strip() != ""  # present
    assert run_argv(form.stop_argv)[0] == 0
    code, out = run_argv(form.observe_argv)
    assert code in form.observe_ok_exit and out.strip() != ""  # a stopped container still lists
    assert run_argv(form.remove_argv)[0] == 0
    code, out = run_argv(form.observe_argv)
    assert code in form.observe_ok_exit and out.strip() == ""  # released means observed absent
    assert conf.identity not in _inventory(built)["containers"]
    assert VOLUME in _inventory(built)["volumes"]  # X-11: no volume was removed


def stop_removes_the_container_and_no_volume(built: core.Implementation) -> None:
    lin = lineage("one")
    built.extras["seed_volume"](VOLUME)
    handle = _owned(built, lin)
    volumes = _inventory(built)["volumes"]
    stopped = built.impl.stop(handle, _owned_ticket(lin, "stop-effect"))
    assert status(stopped) is ConfirmationStatus.APPLIED
    assert _observe(built, lin).selector_present is False  # stopped and removed: observed absent
    assert handle.selector not in _inventory(built)["containers"]
    assert _inventory(built)["volumes"] == volumes and VOLUME in volumes
    again = built.impl.stop(handle, _owned_ticket(lin, "stop-again"))
    assert status(again) is not ConfirmationStatus.UNKNOWN  # an absent target is already stopped


def observe_argv_reads_absent_only_for_a_missing_selector(built: core.Implementation) -> None:
    lin = lineage("one")
    form = ports.as_descriptor(
        built.impl.release_descriptor(effect_call(lin, "create", {"spec": built.extras["spec"]}))
    )
    assert isinstance(form, ports.ArgvRelease)
    code, out = built.extras["run_argv"](form.observe_argv)  # a missing selector, a live engine
    assert code in form.observe_ok_exit and out.strip() == ""  # absent
    down = built.extras["unreachable"]()
    try:
        code, out = down.extras["run_argv"](form.observe_argv)  # the same argv, no engine
        assert not (code in form.observe_ok_exit and out.strip() == "")  # never read as absent
    finally:
        if down.close is not None:
            down.close()


def reads_against_an_unreachable_engine_could_not_observe(built: core.Implementation) -> None:
    for make, expected in (
        (built.extras["unreachable"], DOCKER_ENGINE_UNREACHABLE),
        (built.extras["missing_cli"], DOCKER_CLI_MISSING),
    ):
        down = make()
        try:
            lin = lineage("one")
            seen = down.impl.observe(built.extras["spec"], lin, "up")
            assert seen.code == expected  # distinct codes for the two conditions (WR-VERIFY-3)
            assert seen.selector_present is False and seen.selector_ref is None
            assert tuple(seen.found) == ()  # never a partial result
            target = FoundRef("docker_container", selector_of(lin), datetime.now(UTC))
            checked = down.impl.check("running", target)
            assert checked.satisfied is False and checked.code == expected
            refused = down.impl.endpoint(target, Vantage.HOST)
            assert getattr(refused, "code", None) == expected  # never an address
        finally:
            if down.close is not None:
                down.close()


def a_root_cancel_ends_every_call_and_changes_nothing(built: core.Implementation) -> None:
    """WR-CANCEL-5, the adapter contract-suite half: docker runs through the injected execution
    port with the root's cancel signal, so once the root is cancelled every call the adapter makes
    ends at once (the port returns its INTERRUPTED code) and the adapter reports it as such: a
    read is could-not-observe with that code, never a partial or an absent result, and a create
    is `NOT_APPLIED` with that code, having changed nothing."""
    lin = lineage("one")
    before = _inventory(built)
    built.extras["cancel_root"]()
    seen = built.impl.observe(built.extras["spec"], lin, "up")
    assert seen.code == EXECUTION_CANCELLED
    assert seen.selector_present is False and seen.selector_ref is None and tuple(seen.found) == ()
    target = FoundRef("docker_container", selector_of(lin), datetime.now(UTC))
    checked = built.impl.check("running", target)
    assert checked.satisfied is False and checked.code == EXECUTION_CANCELLED
    made = _create(built, lin)
    assert status(made) is ConfirmationStatus.NOT_APPLIED and made.code == EXECUTION_CANCELLED
    assert made.identity is None
    assert _inventory(built) == before  # a cancelled root leaves the engine as it found it


def repair_keeps_one_target(built: core.Implementation) -> None:
    lin = lineage("one")
    handle = _owned(built, lin)
    for member in ("restart", "recreate"):
        again = getattr(built.impl, member)(handle, _owned_ticket(lin, f"{member}-effect"))
        assert status(again) is ConfirmationStatus.APPLIED and again.identity == handle.selector
        seen = _observe(built, lin)
        assert seen.selector_present is True and seen.selector_ref.selector == handle.selector
    assert _inventory(built)["containers"] == {handle.selector}  # still one instance


def check_and_endpoint_reach_only_the_named_instance(built: core.Implementation) -> None:
    one, two = lineage("one"), lineage("two")
    first, second = _owned(built, one), _owned(built, two)
    ref_one = _observe(built, one).selector_ref
    ref_two = _observe(built, two).selector_ref
    assert ref_one.selector == first.selector and ref_two.selector == second.selector
    built.impl.stop(second, _owned_ticket(two, "stop-two"))
    with read(built, "ResourceReads.check"):
        running = built.impl.check("running", ref_one)
    with read(built, "ResourceReads.check"):
        stopped = built.impl.check("running", ref_two)
    assert running.satisfied is True  # the instance the ref names...
    assert stopped.satisfied is False  # ...and no other: the stopped one does not answer for it
    with read(built, "ResourceReads.endpoint"):
        host = built.impl.endpoint(ref_one, Vantage.HOST)
    assert host.host in ("127.0.0.1", "localhost") and host.port > 0  # a published port
    with read(built, "ResourceReads.endpoint"):
        container = built.impl.endpoint(ref_one, Vantage.CONTAINER)
    assert container.host == first.selector and container.port > 0  # the network name


def local_target_endpoint_is_route_refused(built: core.Implementation) -> None:
    local = FoundRef("local_process", "found-123-456", datetime.now(UTC))
    for vantage in (Vantage.HOST, Vantage.CONTAINER):
        with read(built, "ResourceReads.endpoint"):
            refused = built.impl.endpoint(local, vantage)
        assert getattr(refused, "code", None) == ROUTE_UNSUPPORTED  # never a best-effort address
        assert refused.human_action and len(refused.human_action) <= bounds.HUMAN_ACTION_MAX


def safe_start_moves_stopped_to_running_only(built: core.Implementation) -> None:
    spec = built.extras["spec"]
    planted = built.extras["plant_found"](spec.logical_system, running=False)
    target = FoundRef("docker_container", planted, datetime.now(UTC))
    before = _inventory(built)
    with read(built, "ResourceReads.check"):
        assert built.impl.check("running", target).satisfied is False
    conf = built.impl.start(target, ticket_for(lineage("one"), EffectFacetClass.SAFE_START))
    assert status(conf) is ConfirmationStatus.APPLIED
    with read(built, "ResourceReads.check"):
        assert built.impl.check("running", target).satisfied is True
    assert _inventory(built) == before  # nothing created, recreated or removed


def values_stay_within_their_bounds(built: core.Implementation) -> None:
    lin = lineage("one")
    conf = _create(built, lin)
    assert len(conf.identity) <= bounds.TOKEN_MAX
    ref = _observe(built, lin).selector_ref
    assert len(ref.selector) <= bounds.TOKEN_MAX
    with read(built, "ResourceReads.check"):
        result = built.impl.check("running", ref)
    assert len(result.detail.encode("utf-8")) <= bounds.TEXT_MAX
    assert len(_observe(built, lin).found) <= 16  # FOUND_MAX


def reads_leave_no_trace(built: core.Implementation) -> None:
    lin = lineage("one")
    conf = _create(built, lin)
    ref = _observe(built, lin).selector_ref
    before = _inventory(built)
    for _ in range(3):  # repeated reads change nothing in the reach the watcher looks at
        _observe(built, lin)
        with read(built, "ResourceReads.check"):
            built.impl.check("running", ref)
        with read(built, "ResourceReads.endpoint"):
            built.impl.endpoint(ref, Vantage.HOST)
    assert _inventory(built) == before and conf.identity in before["containers"]


CASES: Sequence[core.Case] = (
    core.Case("absent_before_create", absent_before_create),
    core.Case("create_applies_and_is_observed", create_applies_and_is_observed),
    core.Case("found_instance_only_in_found", found_instance_only_in_found),
    core.Case("found_instance_is_never_touched", found_instance_is_never_touched),
    core.Case("create_idempotent_per_selector", create_idempotent_per_selector),
    core.Case("descriptor_forms_by_lifetime", descriptor_forms_by_lifetime),
    core.Case("argv_release_is_the_pinned_shape", argv_release_is_the_pinned_shape),
    core.Case("descriptor_equal_and_before_the_effect", descriptor_equal_and_before_the_effect),
    core.Case(
        "descriptor_addresses_only_the_run_scoped_selector",
        descriptor_addresses_only_the_run_scoped_selector,
    ),
    core.Case("owned_members_carry_the_handles_release", owned_members_carry_the_handles_release),
    core.Case("safe_start_descriptor_is_durable", safe_start_descriptor_is_durable),
    core.Case(
        "argv_release_sweep_leaves_a_seeded_volume", argv_release_sweep_leaves_a_seeded_volume
    ),
    core.Case("stop_removes_the_container_and_no_volume", stop_removes_the_container_and_no_volume),
    core.Case(
        "observe_argv_reads_absent_only_for_a_missing_selector",
        observe_argv_reads_absent_only_for_a_missing_selector,
    ),
    core.Case(
        "reads_against_an_unreachable_engine_could_not_observe",
        reads_against_an_unreachable_engine_could_not_observe,
    ),
    core.Case(
        "a_root_cancel_ends_every_call_and_changes_nothing",
        a_root_cancel_ends_every_call_and_changes_nothing,
    ),
    core.Case("repair_keeps_one_target", repair_keeps_one_target),
    core.Case(
        "check_and_endpoint_reach_only_the_named_instance",
        check_and_endpoint_reach_only_the_named_instance,
    ),
    core.Case("local_target_endpoint_is_route_refused", local_target_endpoint_is_route_refused),
    core.Case("safe_start_moves_stopped_to_running_only", safe_start_moves_stopped_to_running_only),
    core.Case("values_stay_within_their_bounds", values_stay_within_their_bounds),
    core.Case("reads_leave_no_trace", reads_leave_no_trace),
)

core.register_family(FAMILY, CASES)
