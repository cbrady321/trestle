"""A non-cooperating child with a grandchild (L.TR-3.3; MC-B3-01 behaviour fixture; consumed by
L.TR-L.5).

`app` runs `stubborn` and `quick`. `stubborn` never consults the cancel signal: its `advance`
starts a process that ignores SIGTERM and holds a grandchild of its own (both carry `tag` in their
argv, so a test attributes them to the run, MC-13), and its `observe` then sleeps for `seconds`
without a cancellable wait. Nothing in the leaf ends at its slice: only the runtime's stop (the root
deadline plus margin, then the kill) takes the process tree away. Three vertices, depth 2."""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.units import (
    ActContext,
    Acted,
    EffectFacets,
    NoAction,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import CheckResult, Observation, Verdict

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references; `depth` is
# the longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 3, "depth": 2, "shared": None, "expect": "valid"}

# Two levels of a process that ignores SIGTERM: the child the leaf starts and its own child.
_SLEEPER = (
    "import signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "code = 'import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
    " time.sleep(float(sys.argv[2]))'\n"
    "subprocess.Popen([sys.executable, '-c', code, *sys.argv[1:3]],\n"
    "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "time.sleep(float(sys.argv[2]))\n"
)

# The carve is the one of `slice_coop`: each child gets 16 s and ends 20 s before the deadline.
STUBBORN_BUDGET_S = 16
QUICK_BUDGET_S = 10
ROOT_BUDGET_S = 26
DEADLINE_S = 36


def _declaration(unit: str, budget_s: int, max_wait_s: int) -> LeafDeclaration:
    return LeafDeclaration(
        unit=unit,
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=max_wait_s)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=(),
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=budget_s),
        max_attempts=1,
    )


class Stubborn:
    """Ignores the cancel signal. The process tree it starts is the run's to take away."""

    def __init__(self) -> None:
        self._started = False

    def declare(self) -> LeafDeclaration:
        return _declaration("stubborn", STUBBORN_BUDGET_S, 12)

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        if self._started:
            time.sleep(float(params["seconds"]))  # not ctx.cancellation.wait: it does not listen
        return Observation(
            present=False,
            selector_present=False,
            identity_proven=True,
            configuration_compatible=True,
            postcondition=CheckResult(False, None, ""),
            preconditions=(),
            currency=(),
            found=(),
            code=None,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        self._started = True
        subprocess.Popen(
            [sys.executable, "-c", _SLEEPER, str(params["tag"]), str(params["seconds"])],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return NoAction("fixture.noncoop_started")  # no port effect: nothing to ticket

    def release(self, params: Any, handle: Any, effects: Any, ctx: ActContext) -> Step:
        raise NotImplementedError("the stubborn leaf creates no resource through a port")


class Quick:
    """Satisfied at once: the sibling the stubborn child does not hold up."""

    def declare(self) -> LeafDeclaration:
        return _declaration("quick", QUICK_BUDGET_S, 8)

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        return Observation(
            present=True,
            selector_present=True,
            identity_proven=True,
            configuration_compatible=True,
            postcondition=CheckResult(True, None, ""),
            preconditions=(),
            currency=(),
            found=(),
            code=None,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        return Acted()

    def release(self, params: Any, handle: Any, effects: Any, ctx: ActContext) -> Step:
        raise NotImplementedError("the quick leaf creates no resource")


ENTRY = WorkflowEntry(
    root="app",
    units={
        "app": AllDeclaration(
            unit="app",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(
                    unit="stubborn", params={"tag": "tag", "seconds": "seconds"}, needs=()
                ),
                ChildBinding(unit="quick", params={}, needs=()),
            ),
            concurrency=2,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field=None,
        ),
        "stubborn": Stubborn(),
        "quick": Quick(),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=36)  # DEADLINE_S (a decorator argument is a literal)
def slice_noncoop_grandchild(
    ctx: Context, tag: str = "slice-noncoop-tag", seconds: float = 120.0
) -> dict[str, str]:
    run_tree(ctx, ENTRY, {"tag": tag, "seconds": seconds}, ports={})
    return {"fixture": "slice_noncoop_grandchild"}
