"""L.SL-3.4 fixture: a one-vertex workflow whose leaf acts on the machine through the REAL process
ports (`CommandPort`, `LocalProcessPort`), so the run the kernel starts is the runtime-launched
kind A6.1:single asks about. Two modes, chosen by the `mode` argument:

- `command`: the leaf runs one bound command through `ExecutionPort.run`. The command starts a
  process in its own session (`setsid`) that ignores SIGTERM and holds a grandchild of its own, then
  waits; nothing in it consults the run's cancel signal. Every process carries `tag` in its argv.
- `resource`: the leaf creates one local process through `ResourceCreate` (run lifetime) and polls
  for a postcondition that never holds, so the run is still converging when a cancel arrives;
  the release pass stops the created process through `ResourceOwned`. It observes only its own
  instance, so a process that already runs the same command line does not stop it from creating
  one, and must survive every cleanup the run does (a cleanup that matched by command line would
  reach it). `outcome` = `fail` / `block`
  makes `advance` return `Failed` / `Blocked` right after the create (a unit-authored end).
- `found` (L.SL-3.5): the leaf observes before it acts. A found holder (same command line, proven
  identity) that is ready (and, with `health_file`, healthy) is reused: no ticket. An unready one is
  `FOUND_UNHEALTHY`, an occupant of `port` with no proven identity is `FOUND_INCOMPATIBLE`; both are
  blocked and never touched. Only when nothing is there does `advance` create the holder.

`stall` (seconds, default 0) makes the first observation sleep that long, so the leaf's wait (which
fits its budget, L.SL-2.1) starts late and the run's release point comes first (the timed-out path).

The holder program logs any catchable stop signal it receives to
`<tag>.<pid>.sig` (the tag is a path) and exits, so a test can read whether a process was ever
signalled.

The publication allowlist admits only `trestle.workflow`'s declaration names to a plugin, and
`run_tree`, the port protocols and the value types are not among them, so this fixture reaches them
through `importlib` (which the AST check does not see). See A1-SL3B-RETURN.md, plan gap 1.
"""

from __future__ import annotations

import importlib
import socket
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from trestle_packs.process.command import CommandPort
from trestle_packs.process.local import LocalProcessPort

from trestle.plugin import Context, trestle
from trestle.workflow import (
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
    WorkflowEntry,
)

_loop = importlib.import_module("trestle.workflow.loop")
_ports = importlib.import_module("trestle.workflow.ports")
_units = importlib.import_module("trestle.workflow.units")
_values = importlib.import_module("trestle.workflow.values")

# What the bound command runs: a process in a new session that ignores SIGTERM (so a signal to the
# run's group cannot reach it) holding a grandchild of its own that also ignores SIGTERM.
_SLEEPER = (
    "import signal, sys, time\n"
    "if sys.argv[3] == '1':\n"
    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "time.sleep(float(sys.argv[2]))\n"
)
_DETACHED = (
    "import subprocess, sys\n"
    f"code = {_SLEEPER!r}\n"
    "subprocess.Popen([sys.executable, '-c', code, *sys.argv[1:4]],\n"
    "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "exec(code)\n"
)
_COMMAND = (
    "import pathlib, subprocess, sys, time\n"
    f"detached = {_DETACHED!r}\n"
    "subprocess.Popen([sys.executable, '-c', detached, *sys.argv[1:4]], start_new_session=True,\n"
    "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "time.sleep(float(sys.argv[2]))\n"
    "if len(sys.argv) > 4:\n"
    "    pathlib.Path(sys.argv[4]).write_text('done')\n"
)
# The holder a `resource` / `found` leaf creates: one process that waits for a signal, `tag` in its
# argv. A catchable stop signal is logged to `<tag>.<pid>.sig` (so the tag is a path a test owns),
# then it exits. One line: `ps` shows a multi-line argument with its newlines escaped, and the
# port matches a found process by comparing command lines.
HOLDER = (
    "import os, signal, sys; log = f'{sys.argv[1]}.{os.getpid()}.sig'; "
    "[signal.signal(getattr(signal, n), "
    "lambda s, f: (open(log, 'a').write(f'{s}\\n'), os._exit(0))) "
    "for n in ('SIGTERM', 'SIGINT', 'SIGHUP', 'SIGUSR1', 'SIGUSR2')]; "
    "[signal.pause() for _ in iter(int, 1)]  # trestle proc_leaf holder"
)

# The declared budget, deadline, wait and release timeout, in seconds. A test that needs a run to
# end at its deadline publishes a copy with these literals rewritten (`procrun.plugin_dir(...)`):
# `declare()` must stay pure, so they are never read from the environment. Registration refuses a
# leaf whose max_wait + release timeout exceeds its budget (L.SL-2.1), so the wait fits the budget
# and a run can reach its release point only when its wait starts late (`stall`, below). The release
# timeout stays at 4 s: at the published defaults B2-C2 (5) (grace + kill + 5 x release timeout <=
# the 35 s finalization margin, L.SV-3.5) admits no more for one CREATE + RUN target.
BUDGET_S = 100
DEADLINE_S = 120
MAX_WAIT_S = 90
RELEASE_S = 4

RUN = "run"
UP = "up"
STOP = "stop"
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
            EffectDeclaration(
                UP,
                EffectFacetClass.CREATE,
                "up",
                Lifetime.RUN,
                frozenset(),
                timedelta(seconds=RELEASE_S),
            ),
            EffectDeclaration(
                STOP,
                EffectFacetClass.OWNED,
                "stop",
                Lifetime.RUN,
                frozenset(),
                None,
                is_release=True,
            ),
        ),
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=BUDGET_S),
        max_attempts=1,
        env_key_field="env",
    )


def _spec(params: dict[str, Any]) -> Any:
    command = _ports.BoundCommand(
        "holder", (sys.executable, "-c", HOLDER, params["tag"]), {}, RESOLVED, False
    )
    return _ports.ResourceSpec("proc", RealizationKind.AGENT_LAUNCHED_PROJECT, "holder", command)


def _bound_command(params: dict[str, Any]) -> Any:
    hold = "1" if params["hold_term"] else "0"
    argv: tuple[str, ...] = (
        sys.executable,
        "-c",
        _COMMAND,
        params["tag"],
        str(params["seconds"]),
        hold,
    )
    if params["done_file"]:
        argv += (params["done_file"],)
    return _ports.BoundCommand("tree", argv, {}, RESOLVED, False)


_STALLED: list[bool] = []  # set once the stalled first observation has returned (one run per child)


class Unit:
    def declare(self) -> LeafDeclaration:
        return _decl()

    def observe(self, params: dict[str, Any], reads: Any, ctx: Any) -> Any:
        mode = params["mode"]
        if params["stall"] > 0 and not _STALLED:  # the first observation only: the wait starts late
            _STALLED.append(True)
            time.sleep(float(params["stall"]))
        present = selector_present = proven = False
        found: tuple[Any, ...] = ()
        ready = False
        if mode != "command":
            resource = reads.read(_ports.ResourceReads)
            seen = resource.observe(_spec(params), ctx.lineage, UP)
            selector_present = seen.selector_present
            proven = seen.identity_proven
            present = selector_present
            target = seen.selector_ref
            if mode == "found":  # only this mode looks at what else is there
                found = tuple(seen.found)
                target = seen.selector_ref if selector_present else (found[0] if found else None)
                present = selector_present or bool(found)
                occupied = params["port"] > 0 and _listening(params["port"])
                present = present or occupied  # occupancy is presence, never identity
                ready = target is not None and resource.check("ready", target).satisfied
                health = params["health_file"]
                ready = ready and (not health or Path(health).exists())
        done = bool(params["done_file"]) and Path(params["done_file"]).exists()
        return _values.Observation(
            present=present,
            selector_present=selector_present,
            identity_proven=proven,
            configuration_compatible=True,
            postcondition=_values.CheckResult(done or ready, None, ""),
            preconditions=(),
            currency=(),
            found=found,
            code=None,
            payload=None,
        )

    def advance(self, params: dict[str, Any], state: Any, effects: Any, ctx: Any) -> Any:
        if params["mode"] == "command":
            until = ctx.clock.now + timedelta(seconds=float(params["seconds"]) * 2)
            effects.event(_ports.ExecutionPort).run(
                _bound_command(params), RUN, ctx.cancellation, until
            )
            return _units.Acted()
        effects.create(_ports.ResourceCreate).create(_spec(params), UP)
        if params["outcome"] == "fail":
            return _units.Failed("fixture.failed", "the fixture failed on request")
        if params["outcome"] == "block":
            return _units.Blocked(
                "fixture.blocked", "Fix the fixture, then re-send.", _values.Resend.WILL_NOT_SUCCEED
            )
        return _units.Acted()

    def release(self, params: dict[str, Any], handle: Any, effects: Any, ctx: Any) -> Any:
        effects.owned(_ports.ResourceOwned).stop(handle, STOP)
        return _units.Acted()


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


ENTRY = WorkflowEntry(root="unit", units={"unit": Unit()}, deadline=timedelta(seconds=DEADLINE_S))


@trestle(deadline=120, env_arg="env")
def proc_leaf(
    ctx: Context,
    env: str = "e",
    mode: str = "command",
    tag: str = "proc-leaf-tag",
    seconds: float = 120.0,
    hold_term: bool = True,
    done_file: str = "",
    outcome: str = "",
    port: int = 0,
    health_file: str = "",
    stall: float = 0.0,
) -> None:
    intent = {
        "env": env,
        "mode": mode,
        "tag": tag,
        "seconds": seconds,
        "hold_term": hold_term,
        "done_file": done_file,
        "outcome": outcome,
        "port": port,
        "health_file": health_file,
        "stall": stall,
    }
    local = LocalProcessPort()
    _loop.run_tree(
        ctx,
        ENTRY,
        intent,
        ports={
            _ports.ExecutionPort: CommandPort(),
            _ports.ResourceReads: local,
            _ports.ResourceCreate: local,
            _ports.ResourceOwned: local,
        },
    )
