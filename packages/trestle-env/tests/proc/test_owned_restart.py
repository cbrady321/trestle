"""An owned local app killed during its readiness wait is restarted within the leaf's budget and
the run is answered passed with disposition `repaired`, never clean (L.RB-8.3; B7.1, MC-22, DM-03,
WR-ENV-7, WR-REMEDY-4). PROC, venue BOTH: no Docker.

DEVIATION (recorded in B-HOST2-RETURN): the reference `tree.ServiceUnit` declares no owned-restart
remedy, so the unit under test is `OwnedRestartUnit` of the test plugin
`fixtures/owned_restart.py` (the reference service unit plus one declared `restart` effect and the
remedy `execution.postcondition_timeout -> restart`, once). The stdlib app
`tests/fixtures/apps/http_app.py` runs as the real `LocalProcessPort`'s agent-launched process and
is read by the composition root's real HTTP read facet over the declared contract. While the run
waits for its readiness the test SIGKILLs the owned process (the rig's cancel signal is the seam,
as in `test_readiness_cancel.py`); every claim is read from the run's lane and the app's own
event log:

* the run's answer is class `passed`; the node that was repaired is reported with disposition
  `repaired` (a disposition beside `passed`, never a class);
* exactly one restart ticket, the remedy's first and only attempt, applied on the SAME selector
  and within the declared wait and remedy bounds; no second create;
* the process is a new one (the app logged `listening` twice, no `stop` between them: a kill),
  answers ready after the restart, and is stopped with the run;
* WR-REMEDY-4: the restart is of the run's own handle only. A process running the same command
  line that this run did not launch is never signalled, never restarted and never adopted.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.proof import tolerances
from tests.single.workflow import loopkit
from tests.tree import treekit as tk
from trestle.common.outcome import OutcomeClass
from trestle.common.plan.vocabulary import NodeClass, ResourceDisposition
from trestle.workflow import codes, ports
from trestle_packs.process.local import LocalProcessPort
from twin.local_app import APP, AwaitListening, free_port

from trestle_env import tree
from trestle_env.plugins._http import HttpReadinessReads

PLUGIN = Path(__file__).resolve().parents[1] / "fixtures" / "owned_restart.py"


def plugin() -> ModuleType:
    spec = importlib.util.spec_from_file_location("owned_restart_plugin", PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class KillOnce:
    """Wraps the rig's cancel signal: at the first readiness wait, SIGKILL every process the local
    port holds (the owned app dies mid-wait). It never raises the flag itself."""

    def __init__(
        self, rig: tk.TreeRig, local: LocalProcessPort, then: Callable[[], None] | None = None
    ) -> None:
        self._inner = rig.rig.cancel
        self._local = local
        self._then = then
        self.waits = 0
        self.killed: list[int] = []

    def wait(self, timeout: timedelta) -> bool:
        self.waits += 1
        if self.waits == 1:
            for instance in list(self._local._instances.values()):  # noqa: SLF001 (the rig's kill)
                os.kill(instance.proc.pid, signal.SIGKILL)
                instance.proc.wait()
                self.killed.append(instance.proc.pid)
            if self._then is not None:
                self._then()
        return bool(self._inner.wait(timeout))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def rig_for(tmp_path: Path, refusals: str = "2") -> tuple[tk.TreeRig, Path, int, LocalProcessPort]:
    module = plugin()
    port, log = free_port(), tmp_path / "app-events.log"
    spec = module.app_spec(module.app_command(str(port), str(log), str(APP), refusals))
    local = LocalProcessPort()
    unit = module.OwnedRestartUnit(
        tree.HTTP_SUPPORT_UNIT, tree.HTTP_SUPPORT_SERVICE, tree.HTTP_SUPPORT_READY, spec
    )
    rig = tk.tree_rig(
        tmp_path,
        tk.group("env", (tk.bind(tree.HTTP_SUPPORT_UNIT),)),
        {tree.HTTP_SUPPORT_UNIT: unit},
        port_impl={
            ports.ResourceReads: HttpReadinessReads(local, tree.HTTP_READINESS, alive="ready"),
            ports.ResourceCreate: AwaitListening(local, log),
            ports.ResourceOwned: local,
        },
        deadline_s=600,
    )
    return rig, log, port, local


def events(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.proves("WR-ENV-7", "B7.1", "B", "B", "PROC", "BOTH")
@pytest.mark.proves("WR-ENV-7", "WR-ENV-7:restart-within-budget-repaired", "B", "B", "PROC", "BOTH")
def test_killed_owned_app_restarted_reported_repaired(tmp_path: Path) -> None:
    rig, log, _, local = rig_for(tmp_path)
    kill = KillOnce(rig, local)
    rig.rig.services._cancel = kill  # noqa: SLF001  (the signal the loop reads)
    rig.run()

    assert len(kill.killed) == 1, "the owned app was killed once, mid-wait"
    answer = tk.answer_of(rig)
    # class passed, disposition repaired (DM-03): never a clean pass, never a failure
    assert answer.outcome is OutcomeClass.PASSED, answer
    assert answer.primary.path == (tree.HTTP_SUPPORT_UNIT,)
    assert answer.primary.node_class is NodeClass.REPAIRED
    assert answer.primary.disposition is ResourceDisposition.REPAIRED
    assert answer.primary.code is None

    rows = rig.rows()
    node = [r for r in rows if r.get("path") == tree.HTTP_SUPPORT_UNIT]
    issued = [(r["effect"], r.get("attempt")) for r in node if r["class"] == "issue"]
    applied = [
        r["effect"] for r in node if r["class"] == "confirmation" and r["status"] == "applied"
    ]
    # one create, then the remedy's first and only attempt: one restart of the same handle
    assert [effect for effect, _ in issued if effect != "stop"] == ["up", "restart"], issued
    assert [e for e in applied if e != "stop"] == ["up", "restart"], applied
    (end,) = [r for r in node if r["class"] == "end"]
    assert end["condition"] == "satisfied" and end["code"] is None, end

    # within budget: the repair came after the declared wait ran out and used the declared
    # attempts (one), and the whole leaf ended inside its budget
    module = plugin()
    assert sum(1 for e, _ in issued if e == "restart") == module.REMEDY_ATTEMPTS
    assert module.POSTCONDITION_TIMEOUT == codes.POSTCONDITION_TIMEOUT  # the spelling is pinned
    assert rig.rig.clock.now - loopkit.NOW <= timedelta(seconds=tree.LEAF_BUDGET_S)

    # a new process: it listened twice with no `stop` between (a kill), answered ready after the
    # restart, and was stopped with the run
    seen = events(log)
    assert seen.count("listening") == 2, seen
    assert seen.index("listening") < seen.index("health 503") < len(seen)
    second = seen.index("listening", seen.index("listening") + 1)
    assert "stop" not in seen[:second], seen
    assert "health 200" in seen[second:], seen
    assert seen[-1] == "stop", seen
    assert [r for r in node if r["class"] == "released"], "released with the run"


class Stranger:
    """A stand-in for another app: the same command line as the run's, launched by the test, so
    the run did not start it (it is `found`, never owned)."""

    def __init__(self, tmp_path: Path) -> None:
        self.log = tmp_path / "stranger-events.log"
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen(  # noqa: S603 - the test's own stand-in
            [sys.executable, str(APP), "ready-after", "2"],
            env={"PORT": str(free_port()), "APP_EVENT_LOG": str(self.log), "PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + tolerances.JOIN_WAIT_S
        while "listening" not in events(self.log):
            assert time.monotonic() < deadline, "the stand-in app never listened"
            time.sleep(tolerances.POLL_FINE_S)

    def untouched(self) -> bool:
        """Alive, never told to stop, never asked anything."""
        assert self.proc is not None
        return self.proc.poll() is None and events(self.log) == ["listening"]

    def end(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()


@contextmanager
def stranger(tmp_path: Path) -> Iterator[Stranger]:
    other = Stranger(tmp_path)
    try:
        yield other
    finally:
        other.end()


@pytest.mark.proves("WR-REMEDY-4", "WR-REMEDY-4:b-owned-restart-only", "B", "B", "PROC", "BOTH")
def test_restart_is_of_the_owned_process_only(tmp_path: Path) -> None:
    """The remedy restarts the run's own handle and nothing else, in both directions:

    * another process with the same command line, appearing while the owned app is being repaired,
      is never signalled, restarted or adopted;
    * a run that FINDS such a process (it did not launch it) is granted no create and no restart:
      the node ends without either and the process is exactly as it was."""
    owned, found = tmp_path / "owned", tmp_path / "found"
    owned.mkdir()
    found.mkdir()
    rig, log, _, local = rig_for(owned)
    with stranger(owned) as other:
        kill = KillOnce(rig, local, then=other.start)
        rig.rig.services._cancel = kill  # noqa: SLF001
        rig.run()
        answer = tk.answer_of(rig)
        assert answer.primary.disposition is ResourceDisposition.REPAIRED, answer
        assert len(kill.killed) == 1 and other.proc is not None
        assert other.proc.pid not in kill.killed
        assert other.untouched(), "the run touched a process it did not launch"
        restarted = [
            r for r in rig.rows() if r["class"] == "issue" and r.get("effect") == "restart"
        ]
        assert len(restarted) == 1, restarted

    found_rig, found_log, _, found_local = rig_for(found)
    with stranger(found) as other:
        other.start()
        found_rig.rig.services._cancel = KillOnce(found_rig, found_local)
        found_rig.run()
        assert other.untouched(), "the run touched a process it did not launch"
        effects = [
            r.get("effect") for r in found_rig.rows() if r["class"] in ("issue", "confirmation")
        ]
        assert effects == [], effects  # neither created over, restarted, nor stopped
        assert not events(found_log), "the run's own app was never started beside the found one"
        assert tk.answer_of(found_rig).outcome is not OutcomeClass.PASSED
