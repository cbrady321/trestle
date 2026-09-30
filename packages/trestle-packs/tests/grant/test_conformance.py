"""L.RB-9.1 / L.RB-9.2: the Demo Credential Serving family's suites, run against the stdlib
`FakeGrant` and the demo adapters over the stub issuer: reads and refresh (`test_grant_suite`) and
delivery through a mounted refreshable file (`test_grant_delivery_suite`). One suite file (`tests/
conformance/grant_cases.py`), registered through `register_family` and run by `run_family`,
UNMODIFIED, against every implementation (WR-PROOF-4, SA-14). Planted defects prove the suites are
not vacuous. AWS is DEMO ONLY (D-9): the issuer is `tests/fixtures/stubs/stub_issuer.py` on
loopback, in this process; the one `docker_host` binding (`[real]`) mounts a channel into a
consumer container that calls it through `host.docker.internal`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conformance import container_cases, grant_cases
from tests.proof import tolerances
from tests.proof.host.docker_gate import inventory
from tests.proof.suites.ports import core
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, RealizationKind
from trestle.workflow.values import CreatedHandle

from grant.conftest import STUBS, load_stub
from trestle_packs.container import ContainerDefinition, bind
from trestle_packs.container.effects import BindMount
from trestle_packs.fakes.command import Confirmation, ConfirmationStatus
from trestle_packs.fakes.grant import FakeGrant
from trestle_packs.grant import (
    CHANNEL_FILE,
    ArgvRunner,
    ChannelDelivery,
    ContainerExecProbe,
    DemoGrant,
    LocalAppProbe,
    channel_mount,
    provision_channel,
    write_channel,
)
from trestle_packs.grant.demo import ProbeReading
from trestle_packs.process.command import CommandPort

IDENTITY = "demo-user"


class Consumers:
    """The demo binding's consumers: each is a credential file the consumer would read (the token
    is the stub issuer's stand-in), and `read` makes one authenticated call the way an app would."""

    def __init__(self, directory: Path, issuer: Any) -> None:
        self.directory, self._issuer, self._url = directory, issuer, issuer.url

    def plant(self, selector: str, generation: str | None = None) -> None:
        (self.directory / f"{selector}.credentials").write_text(
            self._issuer.state.token(generation), encoding="utf-8"
        )

    def read(self, selector: str) -> ProbeReading | None:
        path = self.directory / f"{selector}.credentials"
        if not path.exists():
            return None
        token = path.read_text(encoding="utf-8").strip()
        seen = token.split(":")[1] if token.count(":") == 2 else None
        request = urllib.request.Request(
            f"{self._url}/whoami", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=tolerances.JOIN_WAIT_S) as reply:  # noqa: S310
                return ProbeReading(reply.status == 200, seen)
        except urllib.error.HTTPError:
            return ProbeReading(False, seen)
        except urllib.error.URLError:
            return ProbeReading(False, seen)  # the issuer does not answer: not authenticated


# ------------------------------------------------------------------------------- the fake


def fake_grant(
    base: Path, world_class: type[FakeGrant] = FakeGrant
) -> Callable[[], core.Implementation]:
    counter = iter(range(10_000))

    def build() -> core.Implementation:
        world = world_class(IDENTITY)
        ephemeral = base / f"consumer-ephemeral-{next(counter)}"
        ephemeral.mkdir()
        return core.Implementation(
            world,
            core.Reach(issuer_generation=world.current_generation, consumer_ephemeral=ephemeral),
            name="fake",
            extras={
                "identity": IDENTITY,
                "current_generation": world.current_generation,
                "expires_at": world.expires_at,
                "advance": world.advance,
                "set_interactive": world.set_interactive,
                "set_reachable": world.set_reachable,
                "plant_consumer": world.plant_consumer,
                "secret_values": world.secret_values,
            },
        )

    return build


# ------------------------------------------------------------------------------- the demo binding


def local_app_grant(base: Path) -> Callable[[], core.Implementation]:
    """The same binding with the shipped `LocalAppProbe`: every consumer read runs the demo client
    `stub_cloud sts get-caller-identity` as a real process through the real `CommandPort`."""
    return demo_stub_grant(base, local_app=True)


def demo_stub_grant(base: Path, local_app: bool = False) -> Callable[[], core.Implementation]:
    """The demo adapter over a fresh in-process stub issuer per case (loopback, an ephemeral
    port); the probe is the test's own consumer reading a credential file (or, `local_app`, the
    shipped `LocalAppProbe` running `stub_cloud`)."""
    stub = load_stub("stub_issuer")
    counter = iter(range(10_000))

    def build() -> core.Implementation:
        directory = base / f"demo-{next(counter)}"
        directory.mkdir()
        issuer = stub.StubIssuer(identity=IDENTITY)
        issuer.start()
        port = int(issuer.url.rsplit(":", 1)[1])
        consumers = Consumers(directory, issuer)
        probe: Any = consumers
        if local_app:
            probe = LocalAppProbe(
                sys.executable,
                str(STUBS / "stub_cloud.py"),
                issuer.url,
                lambda selector: directory / f"{selector}.credentials",
                ArgvRunner(CommandPort()),
            )
        port_impl = DemoGrant(issuer.url, probe=probe)

        def set_reachable(up: bool) -> None:
            if up and issuer._server is None:
                issuer.start(port)
            elif not up:
                issuer.stop()

        def set_interactive(value: bool) -> None:
            issuer.state.set_interactive(value)

        return core.Implementation(
            port_impl,
            core.Reach(issuer_generation=issuer.state.current, consumer_ephemeral=directory),
            name="demo-stub-local-app" if local_app else "demo-stub",
            extras={
                "identity": IDENTITY,
                "current_generation": issuer.state.current,
                "expires_at": issuer.state.expires_at,
                "advance": issuer.state.advance,
                "set_interactive": set_interactive,
                "set_reachable": set_reachable,
                "plant_consumer": consumers.plant,
                "secret_values": issuer.state.secret_values,
            },
            close=issuer.stop,
        )

    return build


GRANT_BINDINGS = [
    pytest.param(
        "fake",
        id="fake",
        marks=[
            pytest.mark.proves(
                "WR-EVID-12", "WR-EVID-12:no-secret-representable", "B", "B", "STUB", "CI"
            ),
        ],
    ),
    pytest.param(
        "demo-stub",
        id="demo-stub",
        marks=[
            pytest.mark.proves("WR-PROOF-4", "WR-PROOF-4:b-grant-suite", "B", "B", "STUB", "CI"),
            pytest.mark.proves(
                "WR-VERIFY-8", "WR-VERIFY-8:b-stub-read-facets-grant", "B", "B", "STUB", "CI"
            ),
        ],
    ),
    pytest.param("demo-stub-local-app", id="demo-stub-local-app"),
]


def grant_factory(binding: str, base: Path) -> Callable[[], core.Implementation]:
    factories: dict[str, Callable[[Path], Callable[[], core.Implementation]]] = {
        "fake": fake_grant,
        "demo-stub": demo_stub_grant,
        "demo-stub-local-app": local_app_grant,
    }
    return factories[binding](base)


@pytest.mark.parametrize("binding", GRANT_BINDINGS)
def test_grant_suite(binding: str, tmp_path: Path) -> None:
    run = core.run_family(grant_cases.FAMILY, grant_factory(binding, tmp_path))
    assert run.cases_run == tuple(c.name for c in grant_cases.CASES)
    assert run.suite_sha256 == core.sha256_of(Path(grant_cases.__file__))


# planted defects: each fake has one, and the suite (unmodified) names the case that catches it


class _StaleByInequality(FakeGrant):
    def observe_in_consumer(self, consumer: Any) -> Any:
        seen = super().observe_in_consumer(consumer)
        differs = seen.generation_seen not in (None, self.current_generation())
        return type(seen)(
            seen.authenticated,
            seen.generation_seen,
            grant_cases.CREDENTIAL_STALE if differs else seen.code,
        )


class _StaleByStringOrder(FakeGrant):
    def observe_in_consumer(self, consumer: Any) -> Any:
        seen = super().observe_in_consumer(consumer)
        older = (
            seen.generation_seen is not None and seen.generation_seen < self.current_generation()
        )
        return type(seen)(
            seen.authenticated,
            seen.generation_seen,
            grant_cases.CREDENTIAL_STALE if older else None,
        )


class _InteractiveStillRefreshes(FakeGrant):
    def refresh(self, grant: Any, ticket: Any) -> Any:
        self._interactive, kept = False, self._interactive
        try:
            return super().refresh(grant, ticket)
        finally:
            self._interactive = kept


class _RefreshLosesTheIdentity(FakeGrant):
    def refresh(self, grant: Any, ticket: Any) -> Any:
        confirmation = super().refresh(grant, ticket)
        return type(confirmation)(confirmation.status, confirmation.code, None)


class _HostReadRotates(FakeGrant):
    def observe_host(self) -> Any:
        seen = super().observe_host()
        self.advance()  # a read that changes what the issuer holds
        return seen


class _TokenInTheIdentity(FakeGrant):
    def observe_host(self) -> Any:
        seen = super().observe_host()
        return type(seen)(
            self.secret_values()[-1],
            seen.expires_at,
            seen.generation,
            seen.interactive_required,
            seen.found,
            seen.code,
        )


class _AnyoneAuthenticates(FakeGrant):
    def observe_in_consumer(self, consumer: Any) -> Any:
        seen = super().observe_in_consumer(consumer)
        return type(seen)(True, seen.generation_seen, seen.code)


class _RefreshIsInRunGroup(FakeGrant):
    def release_descriptor(self, call: Any) -> Any:
        return {"form": "in_run_group", "helpers_disclosed": False}


@pytest.mark.parametrize(
    ("defect", "caught_by"),
    [
        (_StaleByInequality, "stale_only_when_the_issuers_own_order_says_older"),
        (_StaleByStringOrder, "stale_only_when_the_issuers_own_order_says_older"),
        (_InteractiveStillRefreshes, "an_interactive_identity_is_not_applied"),
        (_RefreshLosesTheIdentity, "an_interactive_identity_is_not_applied"),
        (_HostReadRotates, "host_reads_leave_no_trace"),
        (_TokenInTheIdentity, "no_secret_is_representable_or_emitted"),
        (_AnyoneAuthenticates, "a_consumer_read_reaches_only_the_named_instance"),
        (_RefreshIsInRunGroup, "refresh_descriptor_is_durable_host"),
    ],
)
def test_grant_suite_catches_planted_defects(
    defect: type[FakeGrant], caught_by: str, tmp_path: Path
) -> None:
    # the named case alone (a whole-family run of every defect would repeat ~100 watched reads);
    # a case that does not exist would raise `StopIteration` and fail the test
    case = next(c for c in grant_cases.CASES if c.name.startswith(caught_by))
    built = fake_grant(tmp_path, defect)()
    with pytest.raises((AssertionError, core.ReadMutation)):
        if case.operation:
            with core.watch(case.operation, built.reach):
                case.body(built)
        else:
            case.body(built)


# ======================================================================= delivery (L.RB-9.2)
#
# `GrantDelivery` refreshes a consumer's mounted credential file in place. Three bindings run the
# ONE `grant_delivery` suite: `fake` (`FakeGrant`), `demo-stub` (`ChannelDelivery` over the stub
# issuer and a local app reading the channel file through `LocalAppProbe`, CI) and `real`
# (`docker_host`: a consumer CONTAINER created through the container adapter with the channel
# bind-mounted read-only, read from inside by `docker exec`, calling the issuer through
# `host.docker.internal`).

SUITE_RUN = container_cases.ROOT_RUN


def _handle(name: str, release: Any = None) -> CreatedHandle:
    lineage = container_cases.lineage(name)
    return CreatedHandle(
        lineage,
        "up",
        container_cases.selector_of(lineage),
        ports.InRunGroup() if release is None else release,
    )


def fake_delivery(base: Path) -> Callable[[], core.Implementation]:
    def build() -> core.Implementation:
        world = FakeGrant(IDENTITY)

        def own(name: str, generation: str | None = None) -> CreatedHandle:
            handle = _handle(name)
            world.plant_consumer(handle.selector, generation)
            return handle

        return core.Implementation(
            world,
            name="fake",
            extras={
                "reads": world,
                "own_consumer": own,
                "unprovisioned_consumer": _handle,
                "channel_generation": lambda h: world.channel_generation(h.selector),
                "consumer_incarnation": lambda h: str(world.incarnation(h.selector)),
                "consumer_environment": lambda h: world.environment(h.selector),
                "advance": world.advance,
                "current_generation": world.current_generation,
                "set_reachable": world.set_reachable,
                "secret_values": world.secret_values,
            },
        )

    return build


def generation_of(token: str) -> str | None:
    parts = token.strip().split(":")
    return parts[1] if len(parts) == 3 else None


def demo_stub_delivery(base: Path) -> Callable[[], core.Implementation]:
    """`ChannelDelivery` over a fresh in-process stub issuer and channels root per case; the
    consumer is a local app whose credentials file is the channel file."""
    stub = load_stub("stub_issuer")
    counter = iter(range(10_000))

    def build() -> core.Implementation:
        root = base / f"channels-{next(counter)}"
        root.mkdir()
        issuer = stub.StubIssuer(identity=IDENTITY)
        issuer.start()
        port = int(issuer.url.rsplit(":", 1)[1])

        def channel_file(selector: str) -> Path | None:
            directory = root / selector
            return directory / CHANNEL_FILE if directory.is_dir() else None

        reads = DemoGrant(
            issuer.url,
            probe=LocalAppProbe(
                sys.executable,
                str(STUBS / "stub_cloud.py"),
                issuer.url,
                channel_file,
                ArgvRunner(CommandPort()),
            ),
        )

        def own(name: str, generation: str | None = None) -> CreatedHandle:
            handle = _handle(name)
            write_channel(provision_channel(root, handle.selector), issuer.state.token(generation))
            return handle

        def channel_generation(handle: Any) -> str | None:
            path = channel_file(handle.selector)
            return generation_of(path.read_text(encoding="utf-8")) if path else None

        def set_reachable(up: bool) -> None:
            if up and issuer._server is None:
                issuer.start(port)
            elif not up:
                issuer.stop()

        return core.Implementation(
            ChannelDelivery(issuer.url, root),
            name="demo-stub",
            extras={
                "reads": reads,
                "own_consumer": own,
                "unprovisioned_consumer": _handle,
                "channel_generation": channel_generation,
                "consumer_incarnation": lambda h: str((root / h.selector).stat().st_ino),
                "consumer_environment": lambda h: {},
                "advance": issuer.state.advance,
                "current_generation": issuer.state.current,
                "set_reachable": set_reachable,
                "secret_values": issuer.state.secret_values,
            },
            close=issuer.stop,
        )

    return build


class HostDocker:
    """The docker CLI at the gate's endpoint: the suite's own infrastructure calls (never the
    adapters under test)."""

    def __init__(self, cli: str, endpoint: str | None) -> None:
        self.cli, self.endpoint = cli, endpoint

    def run(self, *args: str) -> tuple[int, str]:
        argv = inventory.docker_cmd(self.cli, self.endpoint, *args)
        done = subprocess.run(  # noqa: S603 - the operator's docker by absolute path
            argv,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=inventory.docker_env(),
            timeout=tolerances.JOIN_WAIT_S,
            check=False,
        )
        return done.returncode, done.stdout


def real_docker_delivery(base: Path) -> Callable[[], core.Implementation]:
    """The operator's docker at the gate's endpoint (`docker_host`, PX-3: pending H)."""
    cli = shutil.which("docker")  # the test names the operator's path; the adapter never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    endpoint = os.environ.get("TRESTLE_DOCKER_ENDPOINT")
    image = os.environ[
        "TRESTLE_IMAGE_ALPINE"
    ]  # `<repo>@sha256:<hex>`, exported by `docker_gate run`
    stub = load_stub("stub_issuer")
    counter = iter(range(10_000))
    keep_running = ("sh", "-c", "trap 'exit 0' TERM; while :; do sleep 1; done")

    def build() -> core.Implementation:
        root = base / f"channels-{next(counter)}"
        root.mkdir()
        issuer = stub.StubIssuer(identity=IDENTITY)
        issuer.start()
        port = int(issuer.url.rsplit(":", 1)[1])
        docker = HostDocker(cli, endpoint)
        reads = DemoGrant(
            issuer.url,
            probe=ContainerExecProbe(
                cli, endpoint, ArgvRunner(CommandPort()), f"http://host.docker.internal:{port}"
            ),
        )
        made: list[str] = []
        spec = ports.ResourceSpec(
            "consumer", RealizationKind.DOCKER_SERVICE, "consumer-entry", None
        )

        def own(name: str, generation: str | None = None) -> CreatedHandle:
            lineage = container_cases.lineage(name)
            selector = container_cases.selector_of(lineage)
            write_channel(provision_channel(root, selector), issuer.state.token(generation))
            source, target = channel_mount(root, selector)
            definitions = {
                "consumer-entry": ContainerDefinition(
                    image, keep_running, mounts=(BindMount(source, target),)
                )
            }
            containers = bind(cli, endpoint, CommandPort(), definitions=definitions).containers
            made.append(selector)
            made_it = containers.create(
                spec, container_cases.ticket_for(lineage, EffectFacetClass.CREATE)
            )
            assert made_it.identity == selector, made_it
            call = container_cases.effect_call(lineage, "create", {"spec": spec})
            release = ports.as_descriptor(containers.release_descriptor(call))
            return CreatedHandle(lineage, "up", selector, release)

        def channel_generation(handle: Any) -> str | None:
            code, out = docker.run(
                "exec", handle.selector, "cat", f"{grant_target()}/{CHANNEL_FILE}"
            )
            return generation_of(out) if code == 0 else None

        def grant_target() -> str:
            return channel_mount(root, "trwr-x")[1]

        def incarnation(handle: Any) -> str:
            code, out = docker.run("inspect", "-f", "{{.Id}} {{.State.StartedAt}}", handle.selector)
            assert code == 0, out
            return out.strip()

        def environment(handle: Any) -> dict[str, str]:
            code, out = docker.run("inspect", "-f", "{{json .Config.Env}}", handle.selector)
            assert code == 0, out
            pairs = (item.partition("=") for item in json.loads(out) or [])
            return {key: value for key, _, value in pairs}

        def set_reachable(up: bool) -> None:
            if up and issuer._server is None:
                issuer.start(port)
            elif not up:
                issuer.stop()

        def cleanup() -> None:
            issuer.stop()
            for selector in made:
                docker.run("rm", "-f", selector)  # the containers this case made: run-scoped names

        return core.Implementation(
            ChannelDelivery(issuer.url, root),
            name="real",
            extras={
                "reads": reads,
                "own_consumer": own,
                "unprovisioned_consumer": _handle,
                "channel_generation": channel_generation,
                "consumer_incarnation": incarnation,
                "consumer_environment": environment,
                "advance": issuer.state.advance,
                "current_generation": issuer.state.current,
                "set_reachable": set_reachable,
                "secret_values": issuer.state.secret_values,
            },
            close=cleanup,
        )

    return build


DELIVERY_BINDINGS = [
    pytest.param(
        "fake",
        id="fake",
        marks=[pytest.mark.stub_proven("WR-ENV-13:channel-not-env-var@host@stub-twin")],
    ),
    pytest.param("demo-stub", id="demo-stub"),
    pytest.param(
        "real",
        id="real",
        marks=[
            pytest.mark.docker_host,
            pytest.mark.proves(
                "WR-ENV-13", "WR-ENV-13:channel-not-env-var@host", "B", "B", "DOCKER", "HOST"
            ),
        ],
    ),
]


def delivery_factory(binding: str, base: Path) -> Callable[[], core.Implementation]:
    factories: dict[str, Callable[[Path], Callable[[], core.Implementation]]] = {
        "fake": fake_delivery,
        "demo-stub": demo_stub_delivery,
        "real": real_docker_delivery,
    }
    return factories[binding](base)


@pytest.mark.parametrize("binding", DELIVERY_BINDINGS)
def test_grant_delivery_suite(binding: str, tmp_path: Path) -> None:
    run = core.run_family(grant_cases.DELIVERY_FAMILY, delivery_factory(binding, tmp_path))
    assert run.cases_run == tuple(c.name for c in grant_cases.DELIVERY_CASES)
    assert run.suite_sha256 == core.sha256_of(Path(grant_cases.__file__))


class _DeliverRecreates(FakeGrant):
    def deliver(self, consumer: Any, ticket: Any) -> Any:
        confirmation = super().deliver(consumer, ticket)
        self.recreate(consumer.selector)  # a "refresh" that is really a recreate
        return confirmation


class _DeliverToEveryone(FakeGrant):
    def deliver(self, consumer: Any, ticket: Any) -> Any:
        confirmation = super().deliver(consumer, ticket)
        for selector in list(self._consumers):
            self._consumers[selector] = self.current_generation()
        return confirmation


class _DeliverDoesNothing(FakeGrant):
    def deliver(self, consumer: Any, ticket: Any) -> Any:
        return _applied_without_change(consumer)  # says APPLIED and refreshes nothing


def _applied_without_change(consumer: Any) -> Any:
    return Confirmation(ConfirmationStatus.APPLIED, None, consumer.selector)


class _EnvironmentHoldsTheToken(FakeGrant):
    def environment(self, selector: str) -> dict[str, str]:
        return {"DEMO_TOKEN": self.secret_values()[-1]}


class _DeliverAcceptsAnything(FakeGrant):
    def deliver(self, consumer: Any, ticket: Any) -> Any:
        return _applied_without_change(consumer)


class _DeliveryDescriptorIsDurable(FakeGrant):
    def release_descriptor(self, call: Any) -> Any:
        return {"form": "durable", "owner": "host"}


def fake_delivery_of(base: Path, world_class: type[FakeGrant]) -> Callable[[], core.Implementation]:
    def build() -> core.Implementation:
        built = fake_delivery(base)()
        world = world_class(IDENTITY)
        extras = dict(built.extras)

        def own(name: str, generation: str | None = None) -> CreatedHandle:
            handle = _handle(name)
            world.plant_consumer(handle.selector, generation)
            return handle

        extras.update(
            reads=world,
            own_consumer=own,
            channel_generation=lambda h: world.channel_generation(h.selector),
            consumer_incarnation=lambda h: str(world.incarnation(h.selector)),
            consumer_environment=lambda h: world.environment(h.selector),
            advance=world.advance,
            current_generation=world.current_generation,
            set_reachable=world.set_reachable,
            secret_values=world.secret_values,
        )
        return core.Implementation(world, name="fake", extras=extras)

    return build


@pytest.mark.parametrize(
    ("defect", "caught_by"),
    [
        (_DeliverRecreates, "delivery_refreshes_the_channel_in_place"),
        (_DeliverToEveryone, "delivery_is_repeatable_and_reaches_only_its_consumer"),
        (_DeliverDoesNothing, "delivery_refreshes_the_channel_in_place"),
        (_EnvironmentHoldsTheToken, "the_channel_is_a_file_and_no_credential_variable"),
        (_DeliverAcceptsAnything, "delivery_takes_only_an_owned_handle_under_its_own_ticket"),
        (_DeliveryDescriptorIsDurable, "delivery_carries_the_handles_recorded_descriptor"),
    ],
)
def test_grant_delivery_suite_catches_planted_defects(
    defect: type[FakeGrant], caught_by: str, tmp_path: Path
) -> None:
    case = next(c for c in grant_cases.DELIVERY_CASES if c.name.startswith(caught_by))
    built = fake_delivery_of(tmp_path, defect)()
    with pytest.raises(AssertionError):
        case.body(built)
