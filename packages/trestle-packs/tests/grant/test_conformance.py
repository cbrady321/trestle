"""L.RB-9.1: the Demo Credential Serving family's read and refresh suite, run against the stdlib
`FakeGrant` and the demo adapter over the stub issuer. One suite file (`tests/conformance/
grant_cases.py`), registered through `register_family` and run by `run_family`, UNMODIFIED, against
every implementation (WR-PROOF-4, SA-14). Planted defects prove the suite is not vacuous. AWS is
DEMO ONLY (D-9): the issuer is `tests/fixtures/stubs/stub_issuer.py` on loopback, in this process.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conformance import grant_cases
from conftest import load_stub
from tests.proof.suites.ports import core

from trestle_packs.fakes.grant import FakeGrant
from trestle_packs.grant.demo import DemoGrant, ProbeReading

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
            with urllib.request.urlopen(request, timeout=tolerances_join()) as reply:  # noqa: S310
                return ProbeReading(reply.status == 200, seen)
        except urllib.error.HTTPError:
            return ProbeReading(False, seen)
        except urllib.error.URLError:
            return ProbeReading(False, seen)  # the issuer does not answer: not authenticated


def tolerances_join() -> float:
    from tests.proof import tolerances

    return tolerances.JOIN_WAIT_S


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


def demo_stub_grant(base: Path) -> Callable[[], core.Implementation]:
    """The demo adapter over a fresh in-process stub issuer per case (loopback, an ephemeral
    port); the probe is the test's own consumer reading a credential file."""
    stub = load_stub("stub_issuer")
    counter = iter(range(10_000))

    def build() -> core.Implementation:
        directory = base / f"demo-{next(counter)}"
        directory.mkdir()
        issuer = stub.StubIssuer(identity=IDENTITY)
        issuer.start()
        port = int(issuer.url.rsplit(":", 1)[1])
        consumers = Consumers(directory, issuer)
        port_impl = DemoGrant(issuer.url, probe=consumers)

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
            name="demo-stub",
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
]


def grant_factory(binding: str, base: Path) -> Callable[[], core.Implementation]:
    factories: dict[str, Callable[[Path], Callable[[], core.Implementation]]] = {
        "fake": fake_grant,
        "demo-stub": demo_stub_grant,
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
