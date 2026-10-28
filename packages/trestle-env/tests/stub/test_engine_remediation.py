"""L.RB-7.2: bounded engine remediation against a stub engine starter (B3-C3, B3-C6, V-14, D-8).

The catalog declares the remediation pair (`adapter.docker_engine_unreachable` -> the effect
`start_engine`, with its attempts, total time and cooldown). The runtime is the real loop
(`run_tree`'s own `Loop`, under the loop tests' manual clock): it grants the declared remedy, bounds
it, and records every attempt as a ticket. The effect is a `ResourceSafeStart.start` whose release
descriptor is `Durable(HOST)` (a run never releases it) and whose implementation runs only the
absolute-path stub `stub_engine_starter` through the real `CommandPort`; the real docker binary is
never invoked (a decoy `docker` on the search path logs any call). What the stub stands for, the
real engine start, is deferred (D-8): the two labels are stub-proven; the loop bound is the
runtime's own, so B7.2 is PROVEN.

Three scenes: the start `succeeds` (the engine comes up, the node is satisfied), makes `no_progress`
(the start exits 0 and the engine stays down: J-3a) or is refused until the attempts are spent
(`exhausts`: J-3).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.single.workflow import loopkit as kit
from trestle.workflow import codes, ports
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
)
from trestle.workflow.ports import (
    ResourceCreate,
    ResourceOwned,
    ResourceReads,
    ResourceSafeStart,
    ResourceSpec,
)
from trestle.workflow.units import ActContext, Acted, EffectFacets, ObserveContext, ReadFacets, Step
from trestle.workflow.values import (
    CheckResult as UnitCheckResult,
)
from trestle.workflow.values import (
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    Observation,
    Verdict,
)
from trestle_packs.fakes import CheckResult, FakeMarker
from trestle_packs.process.command import CommandPort

from trestle_env.catalog import load_reference

REPO = Path(__file__).resolve().parents[4]
STUB = REPO / "tests" / "fixtures" / "stubs" / "stub_engine_starter.py"
PAIR = load_reference().remedy("adapter.docker_engine_unreachable")
assert PAIR is not None
ENGINE_DOWN = str(PAIR.code)
START_EFFECT = str(PAIR.effect)
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None)


def declaration(max_attempts: int = 6) -> LeafDeclaration:
    """One CREATE, the catalog's engine remedy as a SAFE_START `start`, and the release."""
    return LeafDeclaration(
        unit="engine_leaf",
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=1.0)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=(
            EffectDeclaration(
                "up", EffectFacetClass.CREATE, "", Lifetime.RUN, frozenset(), timedelta(seconds=2)
            ),
            EffectDeclaration(
                START_EFFECT,
                EffectFacetClass.SAFE_START,
                "start",
                Lifetime.DURABLE,
                frozenset(),
                None,
            ),
            EffectDeclaration(
                "stop",
                EffectFacetClass.OWNED,
                "",
                Lifetime.RUN,
                frozenset(),
                timedelta(seconds=2),
                is_release=True,
            ),
        ),
        retryable=frozenset({ENGINE_DOWN}),
        remedies=(
            RemedyDeclaration(
                code=ENGINE_DOWN,
                effect=START_EFFECT,
                attempts=PAIR.attempts,
                total=timedelta(seconds=PAIR.total_s),
                cooldown=timedelta(seconds=PAIR.cooldown_s),
            ),
        ),
        budget=timedelta(seconds=150),
        max_attempts=max_attempts,
        env_key_field=None,
    )


class EngineLeaf:
    """Create the marker; when the loop grants the engine remedy, start the engine; release it."""

    def __init__(self, decl: LeafDeclaration) -> None:
        self.decl = decl
        self.log: list[str] = []

    def declare(self) -> LeafDeclaration:
        return self.decl

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        self.log.append("observe")
        resource = reads.read(ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, "up")
        target = seen.selector_ref if seen.selector_ref is not None else (seen.found or (None,))[0]
        checked = resource.check("ready", target) if target is not None else None
        satisfied = checked is not None and checked.satisfied
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=UnitCheckResult(
                satisfied,
                None if checked is None else checked.code,
                "" if checked is None else checked.detail,
            ),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code
            if seen.code is not None
            else (None if checked is None else checked.code),
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if state.remedy is not None and state.owned:
            self.log.append("remedy")
            effects.safe_start(ResourceSafeStart).start(state.owned[-1], state.remedy.effect)
        else:
            self.log.append("advance")
            effects.create(ResourceCreate).create(SPEC, "up")
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        self.log.append("release")
        effects.owned(ResourceOwned).stop(handle, "stop")
        return Acted()


class EngineMarker(FakeMarker):
    """A marker that reports the engine unreachable until the stub engine's state file says up."""

    def __init__(self, *args: Any, state: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.state = state

    def engine_up(self) -> bool:
        return self.state.exists() and self.state.read_text().strip() == "up"

    def check(self, check: str, target: Any) -> CheckResult:
        if self._record(target.selector) is not None and not self.engine_up():
            return CheckResult(False, ENGINE_DOWN, "the engine does not answer")
        return super().check(check, target)


class StubEngineStarter:
    """`ResourceSafeStart` over the stub engine starter: one `CommandPort` run of an absolute-path
    executable, nothing else. A start is durable and host-scoped: no run ever releases it."""

    def __init__(self, executable: Path, environment: dict[str, str]) -> None:
        self.executable = str(executable)
        self.environment = environment
        self.port = CommandPort()
        self.calls = 0

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        if call.member != "start":
            raise ValueError(f"the engine starter has no effect {call.member!r}")
        return ports.Durable(ports.DurableOwner.HOST)  # B3-C3: a SafeStartFacet member is durable

    def start(self, target: Any, ticket: Any) -> Confirmation:
        self.calls += 1
        command = ports.BoundCommand(
            task="engine.start",
            argv=(self.executable,),
            environment=dict(self.environment),
            resolved=ports.Resolved(self.executable, "", "", ""),
            reports_tests=False,
        )
        never = kit.RigCancel(kit.ManualClock())  # a cancel signal that is never requested
        until = datetime.now(UTC) + timedelta(seconds=60)
        _, result = self.port.run(command, ticket, never, until)
        if result is not None and result.exit_status == 0:
            return Confirmation(ConfirmationStatus.APPLIED, None, "engine")
        return Confirmation(ConfirmationStatus.NOT_APPLIED, ENGINE_DOWN, "engine")


def write_wrapper(directory: Path) -> Path:
    """The stub, by absolute path, under an interpreter of this venv (never named `docker`)."""
    path = directory / "stub_engine_starter"
    path.write_text(
        f"#!{sys.executable}\nimport runpy, sys\n"
        f"sys.argv = ['stub_engine_starter.py', *sys.argv[1:]]\n"
        f"runpy.run_path({str(STUB)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


MODES = {"succeeds": "starts", "no_progress": "no-progress", "exhausts": "refuses"}


def scene(
    tmp_path: Path, mode: str
) -> tuple[kit.Rig, EngineLeaf, StubEngineStarter, dict[str, Path]]:
    files = {"state": tmp_path / "engine.state", "log": tmp_path / "starter.log"}
    files["state"].write_text("down\n")
    starter = StubEngineStarter(
        write_wrapper(tmp_path),
        {
            "STUB_ENGINE_STATE": str(files["state"]),
            "STUB_ENGINE_MODE": MODES[mode],
            "STUB_ENGINE_LOG": str(files["log"]),
        },
    )
    marker = EngineMarker(tmp_path / "markers", "run", state=files["state"])
    unit = EngineLeaf(declaration())
    rig = kit.build(
        tmp_path,
        unit,
        ports={
            ResourceReads: marker,
            ResourceCreate: marker,
            ResourceOwned: marker,
            ResourceSafeStart: starter,
        },
    )
    return rig, unit, starter, files


def remedy_tickets(rig: kit.Rig) -> list[dict[str, Any]]:
    return [t for t in rig.rows("issue") if t["remedy"] is not None]


@pytest.fixture
def decoy_docker(tmp_path: Path) -> Path:
    """A `docker` first on the search path that logs any call: the starter path never reaches it."""
    log = tmp_path / "docker-called.log"
    bin_dir = tmp_path / "decoy-bin"
    bin_dir.mkdir()
    script = bin_dir / "docker"
    script.write_text(f"#!/bin/sh\necho called >> {log}\nexit 99\n")
    script.chmod(0o755)
    saved = os.environ["PATH"]
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{saved}"
    try:
        yield log
    finally:
        os.environ["PATH"] = saved


@pytest.mark.proves("WR-ENV-6", "B7.2", "B", "B", "STUB", "CI")
@pytest.mark.proves("WR-ENV-6", "WR-ENV-6:remediation-bounded-recorded", "B", "B", "STUB", "CI")
@pytest.mark.proves("WR-REMEDY-4", "WR-REMEDY-4:b-engine-safe-start-stub", "B", "B", "STUB", "CI")
@pytest.mark.parametrize("mode", ["succeeds", "no_progress", "exhausts"])
def test_engine_remediation_bounded_and_recorded(
    tmp_path: Path, mode: str, decoy_docker: Path
) -> None:
    rig, unit, starter, files = scene(tmp_path, mode)
    rig.run()
    tickets = remedy_tickets(rig)
    # bounded: never more attempts than the catalog declared, and each one is a recorded ticket
    assert 1 <= len(tickets) <= PAIR.attempts
    assert [t["remedy"]["attempt"] for t in tickets] == list(range(1, len(tickets) + 1))
    for ticket in tickets:
        assert ticket["remedy"]["code"] == ENGINE_DOWN and ticket["effect"] == START_EFFECT
        assert ticket["facet"] == "safe_start"
        assert ticket["release"] == {"form": "durable", "owner": "host"}  # B3-C3: never released
    (end,) = rig.ends()
    calls = [json.loads(line) for line in files["log"].read_text().splitlines()]
    assert len(calls) == starter.calls == len(tickets)  # every call the stub saw was a ticket
    assert all(call["argv"] == [] and call["state_before"] == "down" for call in calls[:1])
    if mode == "succeeds":
        assert len(tickets) == 1 and (end["condition"], end["code"]) == ("satisfied", None)
        assert files["state"].read_text().strip() == "up"
    elif mode == "no_progress":
        assert len(tickets) == 1  # a second attempt was declared and never ticketed: no progress
        assert (end["condition"], end["code"]) == ("blocked", codes.REMEDY_NO_PROGRESS)
    else:
        assert len(tickets) == PAIR.attempts
        assert (end["condition"], end["code"]) == ("blocked", codes.REMEDY_EXHAUSTED)
        gap = datetime.fromisoformat(tickets[1]["issued_at"].replace("Z", "+00:00")) - (
            datetime.fromisoformat(tickets[0]["issued_at"].replace("Z", "+00:00"))
        )
        assert gap >= timedelta(seconds=PAIR.cooldown_s)  # the declared cooldown is kept
    assert "remedy" in unit.log and unit.log.count("remedy") == len(tickets)
    assert not decoy_docker.exists(), "the real docker binary was invoked"


def test_the_starter_descriptor_is_durable_host_and_no_run_releases_it(tmp_path: Path) -> None:
    rig, _, starter, _ = scene(tmp_path, "succeeds")
    call = ports.EffectCall(
        "start", {}, kit.Lineage("r", kit.NodePath(("u",))), START_EFFECT, Lifetime.DURABLE, None
    )
    descriptor = ports.as_descriptor(starter.release_descriptor(call))
    assert descriptor == ports.Durable(ports.DurableOwner.HOST)
    rig.run()
    assert not [r for r in rig.rows("released") if r.get("effect") == START_EFFECT]
    stops = [t for t in rig.rows("issue") if t["effect"] == "stop"]
    assert all(t["effect"] != START_EFFECT for t in stops)  # the release pass stops the marker only
    with pytest.raises(ValueError):
        starter.release_descriptor(
            ports.EffectCall("restart", {}, call.lineage, "x", Lifetime.DURABLE, None)
        )


def test_the_stub_engine_starter_modes(tmp_path: Path) -> None:
    import subprocess

    wrapper = write_wrapper(tmp_path)
    state = tmp_path / "state"

    def run(mode: str) -> int:
        env = {"STUB_ENGINE_STATE": str(state), "STUB_ENGINE_MODE": mode}
        return subprocess.run([str(wrapper)], env=env, check=False, capture_output=True).returncode

    state.write_text("down\n")
    assert run("no-progress") == 0 and state.read_text().strip() == "down"
    assert run("refuses") == 3 and state.read_text().strip() == "down"
    assert run("bogus") == 2
    assert run("starts") == 0 and state.read_text().strip() == "up"
