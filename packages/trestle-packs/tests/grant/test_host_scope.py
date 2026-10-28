"""L.RB-9.3: `DemoHostScope`, `HostScopeReads` for the demo credential (B3-C18, V-9.6, V-9.7)."""

from __future__ import annotations

from datetime import UTC, datetime

from trestle.common import codes
from trestle.workflow.declarations import HostScopeRef
from trestle.workflow.ports import HostScopeUnreadable

from trestle_packs.fakes.grant import FakeGrant
from trestle_packs.grant import HOST_SCOPE_UNREADABLE, DemoHostScope

AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def test_the_reading_is_the_current_generation_at_the_time_of_the_read() -> None:
    grant = FakeGrant()
    scope = DemoHostScope(grant, now=lambda: AT)
    assert scope.read(HostScopeRef.DEMO_CREDENTIAL) == (
        HostScopeRef.DEMO_CREDENTIAL,
        grant.current_generation(),
        AT,
    )
    before = grant.current_generation()
    grant.advance()  # a host rotation: the next read names the new generation
    seen = scope.read(HostScopeRef.DEMO_CREDENTIAL)
    assert not isinstance(seen, HostScopeUnreadable) and seen[1] != before


def test_an_issuer_that_does_not_answer_gives_no_reading_never_a_partial_one() -> None:
    grant = FakeGrant()
    grant.set_reachable(False)
    scope = DemoHostScope(grant)
    seen = scope.read(HostScopeRef.DEMO_CREDENTIAL)
    assert seen == HostScopeUnreadable(HostScopeRef.DEMO_CREDENTIAL, HOST_SCOPE_UNREADABLE)
    assert scope.readings().readings == ()  # a subject with no reading is absent (V-9.7)


def test_the_toolchain_fingerprint_is_not_this_reads_to_give() -> None:
    scope = DemoHostScope(FakeGrant())
    seen = scope.read(HostScopeRef.TOOLCHAIN_INSTALLS)
    assert isinstance(seen, HostScopeUnreadable) and seen.code == HOST_SCOPE_UNREADABLE
    assert scope.readings((HostScopeRef.TOOLCHAIN_INSTALLS,)).readings == ()


def test_no_secret_is_readable_and_the_code_is_v11s() -> None:
    grant = FakeGrant()
    scope = DemoHostScope(grant)
    shown = repr(scope.readings())
    assert all(secret not in shown for secret in grant.secret_values())
    assert HOST_SCOPE_UNREADABLE == codes.HOST_SCOPE_UNREADABLE  # spelled once, mirrored here
