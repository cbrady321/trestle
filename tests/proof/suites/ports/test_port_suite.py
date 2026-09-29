"""L.SV-5.15: the port families as B3-C21 maps them, and the conformance suite core (B3-C17,
B3-C20): family registration, the unmodified run, and the read-facet watcher with each port's
own reach. Planted defects prove each detector; the real families register in later leaves."""

from __future__ import annotations

import socket
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.proof.suites.ports import core
from trestle.workflow import codes, ports
from trestle.workflow.declarations import HostScopeRef
from trestle.workflow.values import CurrencyFact, FoundRef

# B3-C21, written out here independently of `ports.FAMILIES` (from interface-machine-ports.md).
B3_C21 = {
    "Command Execution": {"ExecutionPort"},
    "Toolchain Resolution & Provisioning": {
        "ToolchainResolver",
        "ToolchainProvisioning",
        "HostScopeReads",
    },
    "Container Control": {"ResourceReads", "ResourceCreate", "ResourceOwned", "ResourceSafeStart"},
    "Local Process Supervision": {"ResourceReads", "ResourceCreate", "ResourceOwned"},
    "Demo Credential Serving": {"GrantReads", "GrantRefresh", "GrantDelivery", "HostScopeReads"},
    "Provisioning & Testing": {"ResourceCreate", "ResourceReads", "ExecutionPort"},
    "Read-Only Probes": {"ResourceReads", "ComposeResolver"},
}
MARKERS = {
    "ReadFacet",
    "EffectFacet",
    "CreateFacet",
    "OwnedEffectFacet",
    "SafeStartFacet",
    "EventFacet",
    "HasRecordedResult",
}
READ_PROTOCOLS = {
    "ResourceReads",
    "GrantReads",
    "HostScopeReads",
    "ToolchainResolver",
    "ComposeResolver",
}
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def protocols() -> dict[str, type]:
    return {
        name: obj
        for name, obj in vars(ports).items()
        if isinstance(obj, type)
        and getattr(obj, "_is_protocol", False)
        and obj.__module__ == ports.__name__
        and name not in MARKERS
    }


# ------------------------------------------------------------------------------ B3-C21


def test_every_b3_c21_family_mapped() -> None:
    mapped = {family: {p.__name__ for p in members} for family, members in ports.FAMILIES.items()}
    assert mapped == B3_C21
    declared = protocols()
    union = set().union(*B3_C21.values())
    assert set(declared) == union  # every protocol is in a family, every family member exists
    for members in ports.FAMILIES.values():
        for member in members:
            assert declared[member.__name__] is member
    # each protocol is exactly one of a read facet or an effect facet (B1-I3, V-5)
    for name, protocol in declared.items():
        is_read = ports.ReadFacet in protocol.__mro__
        is_effect = ports.EffectFacet in protocol.__mro__
        assert is_read != is_effect, name
        assert is_read == (name in READ_PROTOCOLS), name
    # each effect family derives from the marker for its verb class
    assert ports.CreateFacet in ports.ResourceCreate.__mro__
    assert ports.OwnedEffectFacet in ports.ResourceOwned.__mro__
    assert ports.OwnedEffectFacet in ports.GrantDelivery.__mro__
    for safe in (
        ports.ResourceSafeStart,
        ports.GrantRefresh,
        ports.ToolchainProvisioning,
    ):
        assert ports.SafeStartFacet in safe.__mro__
    assert ports.EventFacet in ports.ExecutionPort.__mro__
    # only ExecutionPort.run takes cancel and a deadline (B3-C22)
    takes_cancel = [
        f"{n}.{m}"
        for n, p in declared.items()
        for m in vars(p)
        if not m.startswith("_")
        and callable(getattr(p, m))
        and {"cancel", "until"} & set(_params(getattr(p, m)))
    ]
    assert takes_cancel == ["ExecutionPort.run"]


def _params(member: Callable[..., Any]) -> list[str]:
    import inspect

    return list(inspect.signature(member).parameters)


def test_no_testrun_or_submit_port() -> None:
    names = {n.lower() for n in vars(ports)}
    for forbidden in ("testrun", "testrunner", "provisionsubmit", "provisioningsubmit", "submit"):
        assert not any(forbidden in n for n in names), forbidden
    # TestRun is ExecutionPort; a submit is ResourceCreate.create with PROVISIONED (B3-C21)
    assert {"Provisioning & Testing"} <= set(ports.FAMILIES)
    assert ports.ExecutionPort in ports.FAMILIES["Provisioning & Testing"]
    assert [m for m in vars(ports.ResourceCreate) if m in ("create", "launch_policy")] == [
        "launch_policy",
        "create",
    ]
    assert "submit" not in "".join(vars(ports.ResourceCreate))


def test_read_protocols_have_no_effect_member() -> None:
    for name in sorted(READ_PROTOCOLS):
        assert core.read_protocol_violations(protocols()[name]) == [], name

    # planted defects: a set_*, a ticket parameter, an effect base, a release descriptor hook
    class PlantedSetter(ports.ReadFacet, ports.Protocol):  # type: ignore[misc,name-defined]
        def set_generation(self, value: str) -> None: ...

    class PlantedTicket(ports.ReadFacet, ports.Protocol):  # type: ignore[misc,name-defined]
        def observe(self, ticket: object) -> None: ...

    class PlantedBase(ports.ResourceCreate, ports.ReadFacet, ports.Protocol):  # type: ignore[misc,name-defined]
        pass

    class PlantedHook(ports.ReadFacet, ports.Protocol):  # type: ignore[misc,name-defined]
        def release_descriptor(self, call: object) -> None: ...

    class PlantedVerb(ports.ReadFacet, ports.Protocol):  # type: ignore[misc,name-defined]
        def restart(self) -> None: ...

    for planted in (PlantedSetter, PlantedTicket, PlantedBase, PlantedHook, PlantedVerb):
        assert core.read_protocol_violations(planted), planted.__name__
    assert any("set_" in p for p in core.read_protocol_violations(PlantedSetter))
    # and an effect protocol is not a read protocol
    assert core.read_protocol_violations(ports.ResourceCreate)


def test_incidental_writes_are_the_contract_value() -> None:
    expected = {
        "ResourceReads.observe": set(),
        "ResourceReads.check": set(),
        "ResourceReads.endpoint": set(),
        "ComposeResolver.closure": set(),
        "GrantReads.observe_host": set(),
        "GrantReads.observe_in_consumer": {"${CONSUMER_EPHEMERAL}/**"},
        "ToolchainResolver.resolve": {"${ENVELOPE}/cache/**", "${ENVELOPE}/state/**"},
        "HostScopeReads.read": {"${ENVELOPE}/cache/**", "${ENVELOPE}/state/**"},
    }
    assert {k: set(v) for k, v in ports.INCIDENTAL_WRITES.items()} == expected
    # every read operation of every read protocol has an entry, and only those
    operations = {
        f"{n}.{m}"
        for n in READ_PROTOCOLS
        for m in vars(protocols()[n])
        if not m.startswith("_") and callable(getattr(protocols()[n], m))
    }
    assert set(ports.INCIDENTAL_WRITES) == operations


def test_contract_conversions_are_fixed_by_the_contract() -> None:
    found = FoundRef("credential", "demo", NOW)
    host = ports.GrantObservation("id", NOW, "g2", False, found, None)
    seen = ports.ConsumerCurrency(True, "g1", None)
    assert ports.grant_currency(seen, host) == CurrencyFact(
        HostScopeRef.DEMO_CREDENTIAL, "g1", NOW, None
    )
    stale = ports.ConsumerCurrency(True, "g1", codes.CREDENTIAL_STALE)
    assert ports.grant_currency(stale, host) == CurrencyFact(
        HostScopeRef.DEMO_CREDENTIAL, "g1", NOW, codes.CREDENTIAL_STALE
    )
    assert ports.grant_currency(ports.ConsumerCurrency(False, "g1", None), host) is None
    assert ports.grant_currency(ports.ConsumerCurrency(True, None, None), host) is None
    unreadable = ports.GrantObservation("", NOW, "", False, found, "grant_issuer_unreachable")
    assert ports.grant_currency(seen, unreadable) is None
    resolved = ports.Resolved("/x/java", "21", "pin", "adopt-7")
    assert ports.toolchain_currency(resolved) == CurrencyFact(
        HostScopeRef.TOOLCHAIN_INSTALLS, "adopt-7", None, None
    )


# ------------------------------------------------------------------------------ registration


class Recorder:
    """A trivial implementation: the suite core, not the port, is under test."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    def read(self) -> str:
        self.calls.append("read")
        return self.name


def _case_ok(impl: Recorder) -> None:
    assert impl.read() == impl.name


def _case_second(impl: Recorder) -> None:
    impl.read()
    assert impl.calls == ["read"]  # a fresh implementation per case


def test_register_family_runs_unmodified(tmp_path: Path) -> None:
    suite = tmp_path / "suite_file.py"
    suite.write_text("CASES = 2\n", encoding="utf-8")
    registry = core.Registry()
    cases = [core.Case("ok", _case_ok), core.Case("second", _case_second)]
    family = registry.register_family("fam", cases, suite_file=suite)
    assert family.suite_sha256 == core.sha256_of(suite)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_family("fam", cases, suite_file=suite)
    with pytest.raises(ValueError, match="no cases"):
        registry.register_family("empty", [], suite_file=suite)
    with pytest.raises(ValueError, match="repeats"):
        registry.register_family("dup", [cases[0], cases[0]], suite_file=suite)

    # the same cases run against every implementation, and the suite's hash is recorded per run
    runs = [
        registry.run_family("fam", lambda n=n: core.Implementation(Recorder(n), name=n))
        for n in ("fake", "real")
    ]
    assert [r.implementation for r in runs] == ["fake", "real"]
    assert {r.suite_sha256 for r in runs} == {family.suite_sha256}
    assert all(r.cases_run == ("ok", "second") for r in runs)
    assert registry.runs == runs
    with pytest.raises(KeyError):
        registry.run_family("unregistered", lambda: core.Implementation(Recorder("x")))

    # a failing case is named, the rest still run
    def _fails(impl: Recorder) -> None:
        raise AssertionError("boom")

    registry.register_family(
        "bad", [core.Case("a", _fails), core.Case("b", _case_ok)], suite_file=suite
    )
    with pytest.raises(core.SuiteFailure, match=r"a: AssertionError: boom") as failed:
        registry.run_family("bad", lambda: core.Implementation(Recorder("r"), name="r"))
    assert "b:" not in str(failed.value)

    # editing the suite file after registration is refused: it is no longer the same suite
    suite.write_text("CASES = 3\n", encoding="utf-8")
    with pytest.raises(core.SuiteModified):
        registry.run_family("fam", lambda: core.Implementation(Recorder("x")))


# ------------------------------------------------------------------------------ the watcher


class Envelope:
    """A read port with a planted write: `writes` maps a relative path to file content."""

    def __init__(self, root: Path, writes: dict[str, str]) -> None:
        self.root = root
        self.writes = writes

    def touch(self) -> None:
        for rel, text in self.writes.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")


def _watched(
    tmp_path: Path, operation: str, body: Callable[[Any], None], reach: core.Reach
) -> core.FamilyRun:
    registry = core.Registry()
    registry.register_family(
        "watched", [core.Case("case", body, operation)], suite_file=Path(__file__)
    )
    return registry.run_family("watched", lambda: core.Implementation(object(), reach))


def test_watcher_catches_planted_mutating_read(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    reach = core.Reach(fs_roots=(root,), engine_inventory=lambda: {})

    def write(rel: str) -> Callable[[Any], None]:
        return lambda impl: Envelope(root, {rel: "x"}).touch()

    # a read that writes where the operation allows no write fails; clean reads pass
    _watched(tmp_path, "ResourceReads.check", lambda impl: None, reach)
    with pytest.raises(core.SuiteFailure, match="ResourceReads.observe: filesystem change"):
        _watched(tmp_path, "ResourceReads.observe", write("planted.txt"), reach)

    # a modification and a deletion are mutations too
    (root / "seed.txt").write_text("one", encoding="utf-8")
    with pytest.raises(core.SuiteFailure, match="filesystem change"):
        _watched(tmp_path, "ResourceReads.observe", write("seed.txt"), reach)
    with pytest.raises(core.SuiteFailure, match="filesystem change"):
        _watched(
            tmp_path, "ResourceReads.observe", lambda impl: (root / "seed.txt").unlink(), reach
        )

    # a write inside the operation's INCIDENTAL_WRITES entry is allowed, one outside is not
    env = tmp_path / "env"
    env.mkdir()
    envelope_reach = core.Reach(envelope=env)
    for op in ("ToolchainResolver.resolve", "HostScopeReads.read"):
        for allowed in ("cache/a.bin", "state/b.json", "cache/deep/er/c"):
            _watched(
                tmp_path,
                op,
                lambda impl, r=allowed: Envelope(env, {r: "x"}).touch(),
                envelope_reach,
            )
        for outside in ("bin/tool", "adoption/tree", "cachefile"):
            with pytest.raises(core.SuiteFailure, match="INCIDENTAL_WRITES"):
                _watched(
                    tmp_path,
                    op,
                    lambda impl, r=outside, o=op: Envelope(
                        env, {r: o}
                    ).touch(),  # a new content per op
                    envelope_reach,
                )
    ephemeral = tmp_path / "ephemeral"
    consumer_reach = core.Reach(
        consumer_ephemeral=ephemeral, fs_roots=(root,), issuer_generation=lambda: "g1"
    )
    _watched(
        tmp_path,
        "GrantReads.observe_in_consumer",
        lambda impl: Envelope(ephemeral, {"token.cache": "t"}).touch(),
        consumer_reach,
    )
    with pytest.raises(core.SuiteFailure, match="filesystem change"):
        _watched(
            tmp_path,
            "GrantReads.observe_in_consumer",
            lambda impl: Envelope(root, {"token.cache": "t"}).touch(),
            consumer_reach,
        )


def test_watcher_catches_a_read_that_leaves_a_process_and_one_that_listens(tmp_path: Path) -> None:
    reach = core.Reach(engine_inventory=lambda: {})
    children: list[subprocess.Popen[bytes]] = []

    def leave_child(impl: Any) -> None:
        # a child that blocks on its stdin until the test closes it: no timing, no marker reaping
        children.append(
            subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
            )
        )

    def run_and_wait(impl: Any) -> None:
        subprocess.run([sys.executable, "-c", "pass"], check=True)

    def listen(impl: Any) -> None:
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()

    try:
        with pytest.raises(core.SuiteFailure, match="process left running"):
            _watched(tmp_path, "ResourceReads.observe", leave_child, reach)
        _watched(tmp_path, "ResourceReads.observe", run_and_wait, reach)  # a finished child is fine
    finally:
        for child in children:
            child.kill()
            child.wait()
    with pytest.raises(core.SuiteFailure, match="network (bind|listen)"):
        _watched(tmp_path, "ResourceReads.observe", listen, reach)
    # the socket wrappers are removed again after every watched call
    with socket.socket() as probe:
        assert "wrap" not in getattr(type(probe).bind, "__qualname__", "")

    # connect: loopback and unix addresses are reads; a non-local address is not
    log = core._NetworkLog()
    log.events = [("connect", ("127.0.0.1", 5000)), ("connect", "/var/run/docker.sock")]
    assert log.violations() == []
    log.events = [("connect", ("192.0.2.1", 9)), ("bind", ("0.0.0.0", 0))]
    assert len(log.violations()) == 2


# ------------------------------------------------------------------------------ reach per port

CATEGORIES = ("containers", "images", "volumes", "networks")


class Engine:
    """A fake container engine whose inventory a read may (wrongly) change."""

    def __init__(self, plant: str | None) -> None:
        self.state = {c: {f"{c}-1"} for c in CATEGORIES}
        self.plant = plant

    def inventory(self) -> dict[str, frozenset[str]]:
        return {c: frozenset(v) for c, v in self.state.items()}

    def observe(self) -> None:
        if self.plant is not None:
            self.state[self.plant].add("planted")


def test_watcher_reach_per_port_b3_c17(tmp_path: Path) -> None:
    # Docker reads: the engine inventory of containers, images, volumes and networks
    for kind in (None, *CATEGORIES):
        engine = Engine(kind)
        reach = core.Reach(engine_inventory=engine.inventory)
        body = lambda impl, e=engine: e.observe()  # noqa: E731
        if kind is None:
            _watched(tmp_path, "ResourceReads.observe", body, reach)
        else:
            with pytest.raises(core.SuiteFailure, match=f"engine inventory changed: {kind}"):
                _watched(tmp_path, "ResourceReads.observe", body, reach)

    # resolver reads, HostScopeReads and the compose resolver: the envelope tree hash
    env = tmp_path / "envelope"
    env.mkdir()
    (env / "installs").mkdir()
    (env / "installs" / "tool").write_text("v1", encoding="utf-8")
    envelope_reach = core.Reach(envelope=env)

    def change_install(impl: Any) -> None:
        (env / "installs" / "tool").write_text("v2", encoding="utf-8")

    for op in ("ToolchainResolver.resolve", "HostScopeReads.read", "ComposeResolver.closure"):
        with pytest.raises(core.SuiteFailure, match="INCIDENTAL_WRITES"):
            _watched(tmp_path, op, change_install, envelope_reach)
        (env / "installs" / "tool").write_text("v1", encoding="utf-8")
    # the compose resolver has an empty incidental set even where the cache is allowed elsewhere
    with pytest.raises(core.SuiteFailure, match="INCIDENTAL_WRITES"):
        _watched(
            tmp_path,
            "ComposeResolver.closure",
            lambda impl: Envelope(env, {"cache/x": "1"}).touch(),
            envelope_reach,
        )

    # grant reads: the stub issuer's generation
    generation = ["g1"]
    grant_reach = core.Reach(issuer_generation=lambda: generation[0])
    _watched(tmp_path, "GrantReads.observe_host", lambda impl: None, grant_reach)
    for op in ("GrantReads.observe_host", "GrantReads.observe_in_consumer"):
        with pytest.raises(core.SuiteFailure, match="stub issuer generation changed"):
            _watched(
                tmp_path, op, lambda impl: generation.append(generation.pop() + "+"), grant_reach
            )

    # a port whose reach is named by B3-C17 must supply it: no reach, no vacuous pass
    for op in (
        "ResourceReads.observe",
        "ToolchainResolver.resolve",
        "HostScopeReads.read",
        "ComposeResolver.closure",
        "GrantReads.observe_host",
    ):
        with pytest.raises(core.ReachMissing):
            _watched(tmp_path, op, lambda impl: None, core.Reach())
