"""HOST: readiness requires an in-container authenticated call; an invalid demo credential is
never ready; a host rotation is refreshed in place in the consumer container with no recreate or
restart (L.RB-9.4; B4.4, WR-VERIFY-6, WR-ENV-13; DOCKER+PROC+STUB · HOST, run only by the
host-docker gate; D-9: the issuer is the stub, AWS is DEMO ONLY and never real).

The credential consumer of `twin/consumers.py` (L.RB-9.5's `ConsumerUnit`; the reference tree has
no credential consumer of its own, a deviation recorded in B-HOST2-RETURN) runs through the real
loop (the tree rig) over the real container adapter on the operator's docker, `DemoGrant` with the
`ContainerExecProbe` (one authenticated call from inside the consumer, to the stub issuer through
`host.docker.internal`), `ChannelDelivery` (the mounted refreshable file) and the stub issuer on
loopback. The loop feeds no host-scope readings of its own (lane-close's caveat), so every run sets
`walked.host_scope` to a live `DemoHostScope` over the same issuer (`consumers.LiveScope`).

Not built: `test_rotation_refreshed_in_place_no_recreate[local_app]`. `ChannelDelivery` addresses
only run-scoped container selectors (`trwr-...`) and a local process's is `proc-<hex>`, so nothing
can refresh a local app in place (as L.RB-9.5 found for its local-process case).
Twins: `twin/test_credential_currency_twin.py` (same node names).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.proof import tolerances
from tests.proof.host.docker_gate import inventory
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.workflow import codes, ports
from trestle.workflow.values import Lineage, NodePath
from trestle_packs.container import ContainerDefinition, ExecCheck, bind
from trestle_packs.container.effects import BindMount
from trestle_packs.fakes.container import selector_name
from trestle_packs.grant import (
    ArgvRunner,
    ChannelDelivery,
    ContainerExecProbe,
    DemoGrant,
    channel_mount,
    provision_channel,
    write_channel,
)
from trestle_packs.process.command import CommandPort
from twin import consumers

pytestmark = pytest.mark.docker_host

REPO = Path(__file__).resolve().parents[4]
UNIT = "consumer.current"
KEEP_RUNNING = ("sh", "-c", "trap 'exit 0' TERM; while :; do sleep 1; done")


def _stub_issuer() -> ModuleType:
    name = "stub_issuer"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "tests" / "fixtures" / "stubs" / "stub_issuer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _engine() -> tuple[str, str | None]:
    cli = shutil.which("docker")
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    return cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT")


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    cli, endpoint = _engine()
    return subprocess.run(  # noqa: S603 - the test's own reads, the operator's docker
        inventory.docker_cmd(cli, endpoint, *args),
        capture_output=True,
        text=True,
        env=inventory.docker_env(),
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S * 3,
        check=False,
    )


def _container(name: str) -> tuple[str, str, int]:
    shown = _docker("inspect", "--format", "{{json .}}", name)
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    return str(data["Id"]), str(data["State"]["StartedAt"]), int(data["RestartCount"])


class RecordingExecution:
    """`CommandPort`, every argv it runs recorded (what the probe really ran, and where)."""

    def __init__(self) -> None:
        self._inner = CommandPort()
        self.argvs: list[tuple[str, ...]] = []

    def policy(self, command: Any) -> Any:
        return self._inner.policy(command)

    def release_descriptor(self, call: Any) -> Any:
        return self._inner.release_descriptor(call)

    def run(self, command: Any, ticket: Any, cancel: Any, until: Any) -> Any:
        self.argvs.append(tuple(command.argv))
        return self._inner.run(command, ticket, cancel, until)


class Recording:
    """`ChannelDelivery`, every delivery recorded by the consumer it served."""

    def __init__(self, inner: ChannelDelivery) -> None:
        self.inner, self.delivered = inner, []  # type: ignore[var-annotated]

    def release_descriptor(self, call: Any) -> Any:
        return self.inner.release_descriptor(call)

    def deliver(self, consumer: Any, ticket: Any) -> Any:
        self.delivered.append(consumer.selector)
        return self.inner.deliver(consumer, ticket)


class Containers:
    """The container adapter; `rotate` runs right after the consumer is created (the host's
    credential rotates while the run waits for the consumer), and every observation of the owned
    consumer records its container identity."""

    def __init__(self, inner: Any, selector: str, rotate: Any = None) -> None:
        self._inner, self._selector, self._rotate = inner, selector, rotate
        self.seen: list[tuple[str, str, int]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create(self, spec: Any, ticket: Any) -> Any:
        answer = self._inner.create(spec, ticket)
        if self._rotate is not None:
            self._rotate()
        return answer

    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        answer = self._inner.observe(spec, lineage, effect)
        if answer.selector_present:
            self.seen.append(_container(self._selector))
        return answer


@pytest.fixture
def issuer() -> Iterator[Any]:
    stub = _stub_issuer().StubIssuer(identity="demo-user")
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


class Case:
    """One owned consumer whose channel holds `token` when it is created."""

    def __init__(self, tmp_path: Path, issuer: Any, token: str, rotate: Any = None) -> None:
        cli, endpoint = _engine()
        self.run_id = f"r_cred_{uuid.uuid4().hex[:10]}"
        self.selector = selector_name(Lineage(self.run_id, NodePath((UNIT,))))
        channels = tmp_path / "channels"
        self.channel = provision_channel(channels, self.selector)
        write_channel(self.channel, token)
        source, target = channel_mount(channels, self.selector)
        definitions: Mapping[str, ContainerDefinition] = {
            consumers.CONSUMER_ENTRY: ContainerDefinition(
                os.environ["TRESTLE_IMAGE_ALPINE"],
                KEEP_RUNNING,
                mounts=(BindMount(source, target),),
            )
        }
        checks = {consumers.CONSUMER_IDENTITY: ExecCheck(consumers.IDENTITY_ARGV)}
        bound = bind(cli, endpoint, CommandPort(), definitions=definitions, checks=checks)
        self.containers = Containers(bound.containers, self.selector, rotate)
        port = int(issuer.url.rsplit(":", 1)[1])
        self.execution = RecordingExecution()
        probe = ContainerExecProbe(
            cli, endpoint, ArgvRunner(self.execution), f"http://host.docker.internal:{port}"
        )
        self.grant = DemoGrant(issuer.url, probe=probe)
        self.delivery = Recording(ChannelDelivery(issuer.url, channels))
        self.rig = tk.tree_rig(
            tmp_path,
            tk.group("consumers", (tk.bind(UNIT),)),
            {UNIT: consumers.ConsumerUnit(UNIT, "consumer")},
            deadline_s=600,
            run_id=self.run_id,
            port_impl={
                ports.ResourceReads: self.containers,
                ports.ResourceCreate: self.containers,
                ports.ResourceOwned: bound.containers,
                ports.GrantReads: self.grant,
                ports.GrantDelivery: self.delivery,
            },
        )

    def run(self) -> dict[str, Any]:
        try:
            self.rig.run(consumers.LiveScope(self.grant, lambda: kit.NOW))
        finally:
            listed = _docker("ps", "-aq", "--filter", f"name=^/trwr-{self.run_id}-")
            for cid in listed.stdout.split():
                _docker("rm", "-f", cid)
        return self.rig.ends()[UNIT]

    def execs(self) -> list[Sequence[str]]:
        return [a for a in self.execution.argvs if "exec" in a and self.selector in a]


@pytest.mark.proves("WR-VERIFY-6", "B4.4", "B", "B", "PROC", "HOST")
def test_readiness_requires_in_container_authenticated_call(tmp_path: Path, issuer: Any) -> None:
    case = Case(tmp_path, issuer, issuer.state.token())
    end = case.run()
    assert end["condition"] == "satisfied", end
    # ready BECAUSE the call made from inside the consumer authenticated: the probe ran in the
    # container the run created, and the issuer served that authenticated call
    assert case.execs(), case.execution.argvs
    assert ("GET", "/whoami") in issuer.state.requests
    assert case.delivery.delivered == []  # nothing had to be re-delivered
    assert str(tk.answer_of(case.rig).outcome) == "passed"


@pytest.mark.stub_proven("WR-VERIFY-6:invalid-credential-not-ready")
def test_invalid_demo_credential_not_ready(tmp_path: Path, issuer: Any) -> None:
    invalid = f"demo-token:never-issued:{uuid.uuid4().hex[:8]}"
    case = Case(tmp_path, issuer, invalid)
    end = case.run()
    assert end["condition"] != "satisfied", end  # never ready on a credential the issuer refuses
    assert case.execs() and ("GET", "/whoami") in issuer.state.requests  # it was really asked
    assert str(tk.answer_of(case.rig).outcome) != "passed"
    assert case.delivery.delivered == []  # an invalid credential is not a stale one


@pytest.mark.stub_proven("WR-ENV-13:refresh-in-place-container")
@pytest.mark.parametrize("consumer", ["container"])
def test_rotation_refreshed_in_place_no_recreate(
    tmp_path: Path, issuer: Any, consumer: str
) -> None:
    before = issuer.state.current()
    case = Case(tmp_path, issuer, issuer.state.token(), rotate=issuer.state.advance)
    end = case.run()
    assert end["condition"] == "satisfied", end
    assert issuer.state.current() != before  # the host rotated while the run waited
    remedied = [
        r
        for r in case.rig.rows()
        if r.get("path") == UNIT and r["class"] == "issue" and r["effect"] == consumers.DELIVER
    ]
    assert len(remedied) == 1 and remedied[0]["remedy"]["code"] == codes.CREDENTIAL_STALE
    assert case.delivery.delivered == [case.selector]  # refreshed into the same channel
    assert issuer.state.current() in (case.channel / "credentials").read_text(encoding="utf-8")
    seen = case.containers.seen
    assert seen and len({ident for ident, _, _ in seen}) == 1  # never recreated
    assert {started for _, started, _ in seen} == {seen[0][1]}  # never restarted
    assert {restarts for _, _, restarts in seen} == {0}
