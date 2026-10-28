"""A child whose descendants ignore SIGTERM and keep writing to the run's evidence (L.TR-6.5;
MC-B3-01 behaviour fixture; the survivor-write case of WR-CANCEL-2:tree).

`app` runs `writer` and `quick`. `writer`'s `advance` starts two process trees, each a middle
process that ignores SIGTERM and starts an appender of its own (so an appender is a descendant of
the leaf, two levels down); one tree stays in the leaf's process group, the other starts its own
session. Each appender ignores SIGTERM too and, until it is killed, appends a line to
`evidence/late_<name>.log` every `POLL_S`, and once the run's ledger ends in a terminal row (a
survivor that outlives the run) it also replaces `evidence/result.json` (whole, through a rename,
so a reader never sees a torn file): it writes into the child's evidence and result, and only
after the run is over, so the host's own polling of a live run is undisturbed.
Every process carries `tag` in its argv, so a test attributes them to
the run (MC-13). `writer` then blocks inside one cancellable `observe` call far longer than the
root's deadline, so it is the root deadline's release point (never the leaf's own slice) that
stops it: only the runtime's stop (the group stop, then the kill) takes the writers away, and no
byte may change after the terminal row. `quick` is satisfied at once. Three vertices, depth 2."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path
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

WAIT_MAX_S = 6
LEAF_BUDGET_S = 10
ROOT_BUDGET_S = 20
DEADLINE_S = 36
HOLD_S = 120  # one blocked call, far past the root's deadline
POLL_S = 0.05  # how often an appender writes

# The middle process ignores SIGTERM and starts the appender (also ignoring SIGTERM), then sleeps.
# argv: 1 = tag, 2 = evidence directory, 3 = writer name, 4 = poll seconds.
_APPENDER = (
    "import json, pathlib, signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "evidence, name, poll = pathlib.Path(sys.argv[2]), sys.argv[3], float(sys.argv[4])\n"
    "ended = {'succeeded', 'failed', 'cancelled', 'timed_out', 'worker_exit', 'crashed',\n"
    "    'interrupted'}\n"
    "\n"
    "def terminal():\n"
    "    try:\n"
    "        lines = (evidence / 'ledger.ndjson').read_text(encoding='utf-8').splitlines()\n"
    "        return json.loads(lines[-1]).get('kind') in ended\n"
    "    except (OSError, ValueError, IndexError):\n"
    "        return False\n"
    "\n"
    "n = 0\n"
    "while True:\n"
    "    n += 1\n"
    "    with open(evidence / f'late_{name}.log', 'a', encoding='utf-8') as fh:\n"
    "        fh.write(f'{n}\\n')\n"
    "    if terminal():  # once the run is over it rewrites the result, whole, through a rename\n"
    "        part = evidence / f'.result_{name}.part'\n"
    '        part.write_text(f\'{{"writer": "{name}", "n": {n}}}\', encoding=\'utf-8\')\n'
    "        part.replace(evidence / 'result.json')\n"
    "    time.sleep(poll)\n"
)
_MIDDLE = (
    "import signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    f"subprocess.Popen([sys.executable, '-c', {_APPENDER!r}, *sys.argv[1:5]],\n"
    "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "time.sleep(3600)\n"
)

# Set by the plugin function before `run_tree` (a unit is built at import, the request is known
# only at run time): the tag every process carries and the run's evidence directory.
RUN: dict[str, Any] = {"tag": "survivor-writer-tag", "evidence": None}


def _declaration(unit: str, max_wait_s: int) -> LeafDeclaration:
    return LeafDeclaration(
        unit=unit,
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=max_wait_s)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=(),
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=LEAF_BUDGET_S),
        max_attempts=1,
    )


def _observation(ready: bool) -> Observation:
    return Observation(
        present=ready,
        selector_present=ready,
        identity_proven=True,
        configuration_compatible=True,
        postcondition=CheckResult(ready, None, ""),
        preconditions=(),
        currency=(),
        found=(),
        code=None,
        payload=None,
    )


class Writer:
    """Starts the writers, then never becomes ready and blocks until the runtime stops it."""

    def __init__(self) -> None:
        self._started = False

    def declare(self) -> LeafDeclaration:
        return _declaration("writer", WAIT_MAX_S)

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        if self._started:
            ctx.cancellation.wait(timedelta(seconds=HOLD_S))  # blocked inside one call
        return _observation(False)

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        self._started = True
        evidence = str(RUN["evidence"])
        for name, new_session in (("group", False), ("session", True)):
            subprocess.Popen(
                [sys.executable, "-c", _MIDDLE, str(RUN["tag"]), evidence, name, str(POLL_S)],
                start_new_session=new_session,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return NoAction("fixture.survivor_writers_started")  # no port effect: nothing to ticket

    def release(self, params: Any, handle: Any, effects: Any, ctx: ActContext) -> Step:
        raise NotImplementedError("the writer creates no resource through a port")


class Quick:
    """Satisfied at once: the sibling the writer does not hold up."""

    def declare(self) -> LeafDeclaration:
        return _declaration("quick", 8)

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        return _observation(True)

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
                ChildBinding(unit="writer", params={}, needs=()),
                ChildBinding(unit="quick", params={}, needs=()),
            ),
            concurrency=2,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field=None,
        ),
        "writer": Writer(),
        "quick": Quick(),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=36)  # deadline: DEADLINE_S (a decorator argument is a literal)
def survivor_writer(ctx: Context, tag: str = "survivor-writer-tag") -> dict[str, str]:
    RUN["tag"] = tag
    RUN["evidence"] = Path(ctx.tmp).parent.parent / "evidence"
    run_tree(ctx, ENTRY, {}, ports={})
    return {"fixture": "survivor_writer"}
