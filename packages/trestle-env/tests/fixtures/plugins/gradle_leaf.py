"""L.RB-10.2 fixture: a one-vertex workflow whose leaf runs `stub_gradle` (a Gradle-shaped build)
through the REAL `CommandPort`, so the run the kernel starts is one whose helper processes the LOOP
launched (WR-CANCEL-7). The leaf issues one EVENT effect `run` with the bound command and ends when
the build exits; nothing in it consults the cancel signal beyond the port's own.

The command is the argv a Gradle-shaped task binds (B3-C14): the resolved interpreter as `java`, the
stub as the wrapper main, `--no-daemon` and the auto-download property when `daemon` is false (the
declared configuration), the stub's absolute path is a parameter (the plugin is published from a
copy), and the environment names the stub's log, the tag every helper carries in
its argv, the kind of helper it starts and how long the build runs. The publication allowlist admits
only `trestle.workflow`'s declaration names to a plugin, so the loop, the ports and the values are
reached through `importlib` (as `tests/fixtures/workflows/proc_leaf.py` does).
"""

from __future__ import annotations

import importlib
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle_packs.process.command import CommandPort

_loop = importlib.import_module("trestle.workflow.loop")
_ports = importlib.import_module("trestle.workflow.ports")
_units = importlib.import_module("trestle.workflow.units")
_values = importlib.import_module("trestle.workflow.values")

RUN = "run"
BUDGET_S = 100
DEADLINE_S = 120
MAX_WAIT_S = 90
RESOLVED = _ports.Resolved(sys.executable, "3.12", "fixture-pin", "fixture-adoption")


def _decl() -> LeafDeclaration:
    return LeafDeclaration(
        unit="unit",
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="done",
        wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=MAX_WAIT_S)),
        resource_kind="local_process",
        may_touch=frozenset({"local_process"}),
        effects=(
            EffectDeclaration(RUN, EffectFacetClass.EVENT, "run", Lifetime.RUN, frozenset(), None),
        ),
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=BUDGET_S),
        max_attempts=1,
        env_key_field="env",
    )


def _command(params: dict[str, Any]) -> Any:
    argv: tuple[str, ...] = (
        sys.executable,
        params["stub"],
        "-classpath",
        "gradle/wrapper/gradle-wrapper.jar",
        "org.gradle.wrapper.GradleWrapperMain",
    )
    if not params["daemon"]:
        argv += ("--no-daemon", "-Porg.gradle.java.installations.auto-download=false")
    environment = {
        "STUB_GRADLE_LOG": params["log"],
        "STUB_GRADLE_TAG": params["tag"],
        "STUB_GRADLE_HELPER": params["helper"],
        "STUB_GRADLE_SECONDS": str(params["seconds"]),
    }
    return _ports.BoundCommand("build/assemble", argv, environment, RESOLVED, False)


class Unit:
    def declare(self) -> LeafDeclaration:
        return _decl()

    def observe(self, params: dict[str, Any], reads: Any, ctx: Any) -> Any:
        done = Path(params["log"] + ".done").exists()
        return _values.Observation(
            present=False,
            selector_present=False,
            identity_proven=False,
            configuration_compatible=True,
            postcondition=_values.CheckResult(done, None, ""),
            preconditions=(),
            currency=(),
            found=(),
            code=None,
            payload=None,
        )

    def advance(self, params: dict[str, Any], state: Any, effects: Any, ctx: Any) -> Any:
        until = ctx.clock.now + timedelta(seconds=float(params["seconds"]) * 2 + 30)
        effects.event(_ports.ExecutionPort).run(_command(params), RUN, ctx.cancellation, until)
        Path(params["log"] + ".done").write_text("done")
        return _units.Acted()

    def release(self, params: dict[str, Any], handle: Any, effects: Any, ctx: Any) -> Any:
        raise AssertionError("a build creates nothing to release")


ENTRY = WorkflowEntry(root="unit", units={"unit": Unit()}, deadline=timedelta(seconds=DEADLINE_S))


@trestle(deadline=120, env_arg="env")
def gradle_leaf(
    ctx: Context,
    env: str = "e",
    tag: str = "gradle-leaf-tag",
    log: str = "",
    stub: str = "",
    helper: str = "",
    seconds: float = 120.0,
    daemon: bool = False,
) -> None:
    intent = {
        "env": env,
        "tag": tag,
        "log": log,
        "stub": stub,
        "helper": helper,
        "seconds": seconds,
        "daemon": daemon,
    }
    _loop.run_tree(ctx, ENTRY, intent, ports={_ports.ExecutionPort: CommandPort()})
