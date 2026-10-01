"""L.RB-9.5: an older credential generation on real Docker with the demo issuer
(DOCKER+PROC+STUB · HOST; run only by the host-docker gate; D-9: the issuer is the stub, AWS is
DEMO ONLY and never real).

The consumer tree of `twin/consumers.py` runs through the real loop (the tree rig) over the real
container adapter on the operator's docker, the demo grant adapters (`DemoGrant` with the
`ContainerExecProbe`: one authenticated call from inside the consumer, through
`host.docker.internal`; `ChannelDelivery`: the mounted refreshable file) and the stub issuer on
loopback. The loop feeds no host-scope readings of its own, so the run's join reads the issuer's
current generation through a live `DemoHostScope` (`consumers.LiveScope`, as L.RB-9.3's proof does):
without it every current credential would read as stale in production (reported in the RETURN).

* owned: the run creates the consumer with its channel holding a generation the issuer has since
  moved past. `observe_in_consumer` proves it older (`CREDENTIAL_STALE`) and the declared remedy
  re-delivers the credential into the same channel on the same handle: a repair, not a release and
  not a recreation (the file-mounted credential's fix, decided 2026-09-30). The consumer then
  authenticates with the current generation and the node is satisfied; the container is the same
  one (same id, never restarted) until the run releases it.
* found: a consumer this run did not create, identity proven from inside it, holding an older
  generation, ends INCOMPATIBLE (J-13) with `CREDENTIAL_STALE`, the answer BLOCKED, and nothing
  acts on it: same container, same channel file.

* owned local process (L.RB-9.5.fix1, `::test_owned_process_older_generation_restarted`): a real
  local app (the stdlib app over the real `LocalProcessPort`) that reads its credential when it
  starts, so nothing refreshes it in place (`ChannelDelivery` addresses only container selectors,
  B-HOST1-RETURN): each launch is given the stub issuer's current token in the app's credentials
  file, which the shipped `LocalAppProbe` (the demo client `stub_cloud`) reads. The issuer rotates
  right after the launch; the join proves the generation older and the declared remedy restarts the
  run's own process once (`twin/local_consumer.py`'s `RestartingConsumerUnit`): a new process
  holding the current generation, no delivery, no recreate, no second create. No Docker is used by
  that case; the found-container case above is the not-owned half.

The CI twins are `twin/test_stale_generation_twin.py`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
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
from trestle_packs.process.command import CommandPort
from twin import consumers, local_consumer

pytestmark = pytest.mark.docker_host

REPO = Path(__file__).resolve().parents[4]
UNIT = "consumer.stale"
FIXTURE_LABEL = "trestle.proof.fixture=stale-generation"
KEEP_RUNNING = ("sh", "-c", "trap 'exit 0' TERM; while :; do sleep 1; done")
MOUNT = "/run/trestle-demo-credentials"


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
    return subprocess.run(  # noqa: S603 - the test's own fixture, the operator's docker
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


class Recording:
    """`ChannelDelivery`, with every delivery recorded by the consumer it served."""

    def __init__(self, inner: ChannelDelivery) -> None:
        self.inner, self.delivered = inner, []  # type: ignore[var-annotated]

    def release_descriptor(self, call: Any) -> Any:
        return self.inner.release_descriptor(call)

    def deliver(self, consumer: Any, ticket: Any) -> Any:
        self.delivered.append(consumer.selector)
        return self.inner.deliver(consumer, ticket)


class Watch:
    """Records the owned consumer's container identity each time the unit observes it."""

    def __init__(self, reads: Any, selector: str) -> None:
        self._reads, self._selector = reads, selector
        self.seen: list[tuple[str, str, int]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._reads, name)

    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        answer = self._reads.observe(spec, lineage, effect)
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


def _rig(
    tmp_path: Path, issuer: Any, system: str, run_id: str, mounts: tuple[BindMount, ...]
) -> tuple[tk.TreeRig, Any, Recording, DemoGrant]:
    cli, endpoint = _engine()
    port = int(issuer.url.rsplit(":", 1)[1])
    definitions = {
        consumers.CONSUMER_ENTRY: ContainerDefinition(
            os.environ["TRESTLE_IMAGE_ALPINE"], KEEP_RUNNING, mounts=mounts
        )
    }
    checks = {consumers.CONSUMER_IDENTITY: ExecCheck(consumers.IDENTITY_ARGV)}
    containers = bind(cli, endpoint, CommandPort(), definitions=definitions, checks=checks)
    probe = ContainerExecProbe(
        cli, endpoint, ArgvRunner(CommandPort()), f"http://host.docker.internal:{port}"
    )
    grant = DemoGrant(issuer.url, probe=probe)
    delivery = Recording(ChannelDelivery(issuer.url, tmp_path / "channels"))
    reads = Watch(containers.containers, selector_name(Lineage(run_id, NodePath((UNIT,)))))
    rig = tk.tree_rig(
        tmp_path,
        tk.group("consumers", (tk.bind(UNIT),)),
        {UNIT: consumers.ConsumerUnit(UNIT, system)},
        deadline_s=600,
        run_id=run_id,
        port_impl={
            ports.ResourceReads: reads,
            ports.ResourceCreate: containers.containers,
            ports.ResourceOwned: containers.containers,
            ports.GrantReads: grant,
            ports.GrantDelivery: delivery,
        },
    )
    return rig, reads, delivery, grant


def _cleanup(run_id: str) -> None:
    listed = _docker("ps", "-aq", "--filter", f"name=^/trwr-{run_id}-")
    for cid in listed.stdout.split():
        _docker("rm", "-f", cid)


@pytest.mark.proves("WR-OWN-9", "B8.3", "B", "B", "DOCKER+PROC+STUB", "HOST")
def test_owned_container_older_generation_recreated(tmp_path: Path, issuer: Any) -> None:
    run_id = f"r_stale_{uuid.uuid4().hex[:10]}"
    selector = selector_name(Lineage(run_id, NodePath((UNIT,))))
    old = issuer.state.current()
    channel = provision_channel(tmp_path / "channels", selector)
    write_channel(channel, issuer.state.token(old))
    issuer.state.advance()  # the consumer's generation is now older than the issuer's
    source, target = channel_mount(tmp_path / "channels", selector)
    rig, reads, delivery, grant = _rig(
        tmp_path, issuer, "consumer", run_id, (BindMount(source, target),)
    )
    try:
        rig.run(consumers.LiveScope(grant, lambda: kit.NOW))
    finally:
        _cleanup(run_id)
    end = rig.ends()[UNIT]
    assert end["condition"] == "satisfied", end
    issues = [r for r in rig.rows() if r.get("path") == UNIT and r["class"] == "issue"]
    remedied = [r for r in issues if r["effect"] == consumers.DELIVER]
    assert len(remedied) == 1 and remedied[0]["remedy"]["code"] == codes.CREDENTIAL_STALE
    assert delivery.delivered == [selector]  # re-delivered, on the same handle
    assert issuer.state.current() in (channel / "credentials").read_text(encoding="utf-8")
    assert len({ident for ident, _, _ in reads.seen}) == 1  # the same container throughout
    assert {started for _, started, _ in reads.seen} == {reads.seen[0][1]}  # never restarted
    assert str(tk.answer_of(rig).primary.node_class) == "repaired"


@pytest.mark.stub_proven("WR-OWN-9:not-owned-blocked-untouched")
def test_found_container_unproven_generation_blocked_untouched(tmp_path: Path, issuer: Any) -> None:
    run_id = f"r_stale_{uuid.uuid4().hex[:10]}"
    found = f"trestle-stale-{uuid.uuid4().hex[:8]}"
    channel = tmp_path / "found-channel"
    channel.mkdir(mode=0o755)
    old = issuer.state.current()
    write_channel(channel, issuer.state.token(old))
    issuer.state.advance()
    made = _docker(
        "run", "-d", "--pull", "never", "--name", found, "--label", FIXTURE_LABEL,
        "--mount", f"type=bind,source={channel},target={MOUNT},readonly",
        os.environ["TRESTLE_IMAGE_ALPINE"], *KEEP_RUNNING,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr
    try:
        before = _container(found)
        rig, _reads, delivery, grant = _rig(tmp_path, issuer, found, run_id, ())
        rig.run(consumers.LiveScope(grant, lambda: kit.NOW))
        end = rig.ends()[UNIT]
        assert (end["condition"], end["code"]) == ("incompatible", codes.CREDENTIAL_STALE), end
        answer = tk.answer_of(rig)
        assert str(answer.outcome) == "blocked" and answer.primary.human_action
        assert delivery.delivered == []  # nothing acted on it
        assert not [r for r in rig.rows() if r["class"] == "issue"]
        assert _container(found) == before  # same container, still the same start, no restart
        assert old in (channel / "credentials").read_text(encoding="utf-8")  # channel untouched
    finally:
        _docker("rm", "-f", found)
        _cleanup(run_id)


@pytest.mark.stub_proven("WR-OWN-9:owned-process-restarted")
def test_owned_process_older_generation_restarted(tmp_path: Path, issuer: Any) -> None:
    run_id = f"r_stale_{uuid.uuid4().hex[:10]}"
    channels = tmp_path / "channels"
    delivery = local_consumer.LocalAppDelivery(issuer.url, channels)  # declared, never used

    def issue(selector: str) -> None:  # a launch takes the issuer's current credential
        directory = delivery.channel(selector)
        directory.mkdir(parents=True, exist_ok=True)
        write_channel(directory, issuer.state.token())

    probe = LocalAppProbe(
        sys.executable,
        str(REPO / "tests" / "fixtures" / "stubs" / "stub_cloud.py"),
        issuer.url,
        lambda sel: delivery.channel(sel) / CHANNEL_FILE if sel.startswith("proc-") else None,
        ArgvRunner(CommandPort()),
    )
    grant = DemoGrant(issuer.url, probe=probe)
    before = issuer.state.current()
    case = local_consumer.stale_restart_case(
        tmp_path, run_id, grant, delivery, issue=issue, rotate=issuer.state.advance
    )
    local_consumer.assert_restarted(case)
    assert issuer.state.current() != before  # the issuer moved on after the first launch
    assert delivery.delivered == []  # nothing was delivered: the restart took the new one
    held = (delivery.channel(case.selector) / CHANNEL_FILE).read_text(encoding="utf-8")
    assert issuer.state.current() in held
