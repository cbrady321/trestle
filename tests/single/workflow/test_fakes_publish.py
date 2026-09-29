"""L.SV-5.16: the stdlib-only fakes in `trestle_packs.fakes` (MC-25, SA-14): a fixture plugin that
imports them publishes, they import only the standard library, a run-lifetime marker is a fake
in-run process that dies with the run's process group, a file-backed marker is `Durable`, and the
`fixed_fingerprint` knob keeps a repair's trigger code. The conformance suite runs against them in
`tests/proof/suites/ports/test_port_suite.py::test_family_suite`."""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from trestle_packs import fakes
from trestle_packs.fakes import FakeMarker

from tests.single.workflow.test_run_services import make_services
from trestle.common import codes
from trestle.server.plugin_validate import PublicationRefused
from trestle.server.snapshots import materialize_snapshot
from trestle.workflow import ports
from trestle.workflow import services as svc
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
)
from trestle.workflow.facets import EffectBinder, FacetContext
from trestle.workflow.values import CreatedHandle, Goal, Lineage, NodePath, RemedyGrant

FAKES_DIR = Path(fakes.__file__).parent
BUDGET = timedelta(seconds=10)
LINEAGE = Lineage("r_fake_0001", NodePath(("db",)))
SPEC = ports.ResourceSpec("db", RealizationKind.AGENT_LAUNCHED_PROJECT, "db-entry", None)
PROVISIONED = ports.ResourceSpec("record", RealizationKind.PROVISIONED, "rec-entry", None)

FIXTURE = """
from __future__ import annotations

from trestle.plugin import Context, trestle
from trestle_packs.fakes import FakeCommand, FakeMarker  # noqa: F401


@trestle{decorator}
def uses_fakes(ctx: Context, env: str = "dev") -> dict[str, str]:
    return {{"env": env}}
"""


def _call(member: str, arguments: dict[str, object], lifetime: Lifetime) -> ports.EffectCall:
    return ports.EffectCall(member, arguments, LINEAGE, "up", lifetime, timedelta(seconds=30))


def _ticket(effect: str, lifetime: Lifetime) -> svc.AttemptTicket:
    return svc.AttemptTicket(
        LINEAGE,
        effect,
        EffectFacetClass.CREATE,
        1,
        Repeat.SAFE,
        lifetime,
        ports.InRunGroup(),
        None,
    )


# ------------------------------------------------------------------------------ publication


def test_fixture_importing_fakes_publishes(tmp_path: Path) -> None:
    src = tmp_path / "uses_fakes.py"
    src.write_text(FIXTURE.format(decorator='(env_arg="env")'), encoding="utf-8")
    snapshot = materialize_snapshot(src, "uses_fakes", home=tmp_path / "home")
    assert snapshot is not None  # published: the validator admits trestle_packs imports
    assert (tmp_path / "home" / "snapshots").exists()

    # the fakes are port modules: importing them without env_arg is the D-b refusal (WR-OWN-8)
    bare = tmp_path / "bare.py"
    bare.write_text(FIXTURE.format(decorator=""), encoding="utf-8")
    with pytest.raises(PublicationRefused) as refused:
        materialize_snapshot(bare, "bare", home=tmp_path / "home-bare")
    assert refused.value.code == codes.PUBLICATION_ENV_ARG_MISSING
    assert "trestle_packs.fakes" in str(refused.value)


def test_fakes_import_only_stdlib() -> None:
    for path in sorted(FAKES_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                assert top in sys.stdlib_module_names or name.startswith("trestle_packs.fakes"), (
                    f"{path.name} imports {name}"
                )
                assert top != "trestle", path.name


# ------------------------------------------------------------------------------ the run's group


HELPER = """
import sys
from pathlib import Path
from trestle_packs.fakes import FakeMarker
from trestle.workflow import ports, services as svc
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat, RealizationKind
from trestle.workflow.values import Lineage, NodePath

lineage = Lineage("r_fake_0001", NodePath(("db",)))
marker = FakeMarker(Path(sys.argv[1]), "run")
spec = ports.ResourceSpec("db", RealizationKind.AGENT_LAUNCHED_PROJECT, "db-entry", None)
ticket = svc.AttemptTicket(lineage, "up", EffectFacetClass.CREATE, 1, Repeat.SAFE,
                           Lifetime.RUN, ports.InRunGroup(), None)
conf = marker.create(spec, ticket)
print(conf.identity, flush=True)
sys.stdin.read()
"""


def test_run_marker_gone_when_group_gone(tmp_path: Path) -> None:
    root = tmp_path / "markers"
    helper = subprocess.Popen(
        [sys.executable, "-c", HELPER, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,  # the "run": its own session and process group
    )
    try:
        assert helper.stdout is not None
        selector = helper.stdout.readline().strip()
        assert selector
        observer = FakeMarker(root, "run")
        seen = observer.observe(SPEC, LINEAGE, "up")
        assert seen.selector_present is True and seen.selector_ref.selector == selector
        record = json.loads((root / f"{selector}.marker").read_text(encoding="utf-8"))
        assert record["kind"] == "process"  # a fake in-run process, not just a file
        assert os.getpgid(record["pid"]) == helper.pid  # in the run's process group (V-2.3)
        os.killpg(helper.pid, signal.SIGKILL)  # the run's group ends
        helper.wait()
    finally:
        if helper.poll() is None:
            os.killpg(helper.pid, signal.SIGKILL)
            helper.wait()
    assert (root / f"{selector}.marker").exists()  # the file lingers: it is only the observable
    gone = FakeMarker(root, "run").observe(SPEC, LINEAGE, "up")
    assert gone.selector_present is False and gone.selector_ref is None
    assert observer.inventory()["containers"] == frozenset()  # nothing observable is left


# ------------------------------------------------------------------------------ file-backed


def test_file_marker_descriptor_is_durable(tmp_path: Path) -> None:
    marker = FakeMarker(tmp_path / "files", "durable")
    for owner in ("environment", "host"):
        fake = FakeMarker(tmp_path / f"o-{owner}", "durable", owner=owner)
        call = _call("create", {"spec": SPEC}, Lifetime.DURABLE)
        wire = fake.release_descriptor(call)
        assert ports.descriptor_from_wire(wire) == ports.Durable(ports.DurableOwner(owner))
    # the default owner is ENVIRONMENT (B3-C3's provisioned-record case)
    default = ports.descriptor_from_wire(
        marker.release_descriptor(_call("create", {"spec": SPEC}, Lifetime.DURABLE))
    )
    assert default == ports.Durable(ports.DurableOwner.ENVIRONMENT)
    # a file-backed marker has no RUN form: refused before any change, never an InRunGroup
    with pytest.raises(ValueError, match="RUN"):
        marker.release_descriptor(_call("create", {"spec": SPEC}, Lifetime.RUN))
    with pytest.raises(ValueError, match="RUN"):
        marker.create(SPEC, _ticket("up", Lifetime.RUN))
    assert list((tmp_path / "files").iterdir()) == []
    # created durable: a plain file, no process, the descriptor Durable
    conf = marker.create(SPEC, _ticket("up", Lifetime.DURABLE))
    record = json.loads((tmp_path / "files" / f"{conf.identity}.marker").read_text("utf-8"))
    assert record["kind"] == "file" and marker._children == {}
    # a PROVISIONED spec is durable whatever lifetime is asked, on a run-capable fake too
    runner = FakeMarker(tmp_path / "runner", "run")
    form = ports.descriptor_from_wire(
        runner.release_descriptor(_call("create", {"spec": PROVISIONED}, Lifetime.RUN))
    )
    assert form == ports.Durable(ports.DurableOwner.ENVIRONMENT)
    made = runner.create(PROVISIONED, _ticket("up", Lifetime.RUN))
    assert (
        json.loads((tmp_path / "runner" / f"{made.identity}.marker").read_text("utf-8"))["kind"]
        == "file"
    )
    runner.close()
    # a run marker that IS a process never returns Durable for RUN, and a file never InRunGroup
    with FakeMarker(tmp_path / "proc", "run") as process:
        run_form = ports.descriptor_from_wire(
            process.release_descriptor(_call("create", {"spec": SPEC}, Lifetime.RUN))
        )
        assert run_form == ports.InRunGroup(helpers_disclosed=False)


# ------------------------------------------------------------------------------ the knobs


def _check(marker: FakeMarker, selector: str, times: int = 1) -> list[tuple[bool, str | None]]:
    ref = marker.observe(SPEC, LINEAGE, "up").selector_ref
    assert ref is not None and ref.selector == selector
    return [(r.satisfied, r.code) for r in (marker.check("ready", ref) for _ in range(times))]


def test_lag_never_ready_and_outcome_codes(tmp_path: Path) -> None:
    with FakeMarker(tmp_path / "lag", "durable", lag_polls=2, outcome_codes=("t.warming",)) as lag:
        selector = lag.create(SPEC, _ticket("up", Lifetime.DURABLE)).identity
        assert _check(lag, selector, 4) == [
            (False, "t.warming"),
            (False, "t.warming"),
            (True, None),
            (True, None),
        ]
    with FakeMarker(tmp_path / "never", "durable", never_ready=True) as never:
        selector = never.create(SPEC, _ticket("up", Lifetime.DURABLE)).identity
        assert _check(never, selector, 5) == [(False, None)] * 5


def test_fixed_fingerprint_keeps_trigger_code(tmp_path: Path) -> None:
    codes_seen = ("t.trigger", "t.next")
    for fixed in (False, True):
        with FakeMarker(
            tmp_path / f"fp-{fixed}",
            "durable",
            never_ready=True,
            fixed_fingerprint=fixed,
            outcome_codes=codes_seen,
        ) as marker:
            selector = marker.create(SPEC, _ticket("up", Lifetime.DURABLE)).identity
            assert _check(marker, selector) == [(False, "t.trigger")]
            handle = ports_handle(selector)
            marker.restart(handle, _ticket("fix", Lifetime.DURABLE))  # a confirmed remedy
            after = _check(marker, selector)
            # with fixed_fingerprint the observed code after the remedy equals the trigger code
            assert after == [(False, "t.trigger" if fixed else "t.next")]
            marker.recreate(handle, _ticket("fix", Lifetime.DURABLE))
            assert _check(marker, selector) == [(False, "t.trigger" if fixed else None)]


def ports_handle(selector: str) -> CreatedHandle:
    return CreatedHandle(LINEAGE, "up", selector, ports.Durable(ports.DurableOwner.ENVIRONMENT))


def test_fakes_through_the_facets_on_a_real_lane(tmp_path: Path) -> None:
    """The fakes are ports as the loop meets them: descriptors as wire mappings, values with the
    vocabulary's field names, a remedy ticket that changes what the next check reports."""
    services = make_services(tmp_path)
    lane = services.attempts()
    lane.record_plan(svc.PlanIdentity("d" * 64, "a" * 64, (), "o" * 64))
    decl = LeafDeclaration(
        unit="leaf",
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(poll_every=timedelta(seconds=1), backoff=1.0, max_wait=BUDGET),
        resource_kind="db",
        may_touch=frozenset(),
        effects=(
            EffectDeclaration(
                "up", EffectFacetClass.CREATE, "create", Lifetime.DURABLE, frozenset(), None
            ),
            EffectDeclaration(
                "fix", EffectFacetClass.OWNED, "restart", Lifetime.DURABLE, frozenset(), None
            ),
        ),
        retryable=frozenset(),
        remedies=(),
        budget=BUDGET,
        max_attempts=3,
    )
    lineage = Lineage("r_svc_0001", NodePath(("a",)))
    with FakeMarker(
        tmp_path / "m",
        "durable",
        never_ready=True,
        fixed_fingerprint=True,
        outcome_codes=("t.hot",),
    ) as marker:
        binder = EffectBinder(
            FacetContext(
                lane=lane,
                lineage=lineage,
                declaration=decl,
                ports={
                    ports.ResourceCreate: marker,
                    ports.ResourceOwned: marker,
                    ports.ResourceReads: marker,
                },
                cancellation=_NoStop(),
                goal=lambda: Goal.CONVERGE,
                flip_goal=lambda: None,
                hold=lambda step: None,
                now=lambda: datetime(2026, 9, 30, 11, 55, tzinfo=UTC),
                remedy=RemedyGrant("t.hot", "fix", 1),
            )
        )
        binder.create(ports.ResourceCreate).create(SPEC, "up")
        (up,) = lane.node_record(lineage.path).tickets
        assert up.handle is not None
        assert up.release == {"form": "durable", "owner": "environment"}  # recorded with the ticket
        ref = binder.read(ports.ResourceReads).observe(SPEC, lineage, "up").selector_ref
        assert marker.check("ready", ref).code == "t.hot"
        binder.owned(ports.ResourceOwned).restart(up.handle, "fix")
        fix = lane.node_record(lineage.path).tickets[1]
        assert fix.confirmation is not None and fix.remedy == RemedyGrant("t.hot", "fix", 1)
        assert fix.release == up.release  # a repair carries the handle's own descriptor
        assert marker.check("ready", ref).code == "t.hot"  # fixed_fingerprint: no progress


class _NoStop:
    requested = False

    def cause(self) -> None:
        return None

    def wait(self, timeout: object) -> bool:
        return False
