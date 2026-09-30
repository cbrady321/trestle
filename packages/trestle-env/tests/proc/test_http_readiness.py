"""HTTP readiness on a real local process: the dependent starts after the declared response
(L.RB-2.1; hld-wr-environment KDD 2, WR-VERIFY-2). PROC, venue BOTH: no Docker.

The tree's supporting service is run as its agent-launched realization: the stdlib app
`tests/fixtures/apps/http_app.py`, in `ready-after 3` mode, launched by the real
`LocalProcessPort`, and its readiness read by the composition root's real HTTP read facet over the
declared contract (`GET /health` answers 200 `ok`). The dependent is the tree's own `needs` edge.
Every claim is read from the run's lane and the app's own event log, never from timing."""

from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.proof import tolerances
from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
)
from trestle.workflow.values import CheckResult, Observation
from trestle_packs.process.local import LocalProcessPort

from trestle_env import schema, tree
from trestle_env.plugins._http import HttpReadinessReads

APP = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "apps" / "http_app.py"
REFUSED = 3  # the app answers `/health` 503 this many times before the first 200


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class AwaitListening:
    """The create facet of the local port, returning once the app says it is listening.

    The loop's clock is manual here (no test sleeps), so without this a poll could ask a
    still-starting app: process start-up is the harness's to wait for, the readiness contract is
    the read facet's. Every other member is the port's own."""

    def __init__(self, inner: LocalProcessPort, log: Path) -> None:
        self._inner = inner
        self._log = log

    def create(self, spec: ports.ResourceSpec, ticket: Any) -> Any:
        confirmation = self._inner.create(spec, ticket)
        deadline = time.monotonic() + tolerances.JOIN_WAIT_S
        while time.monotonic() < deadline:
            if self._log.exists() and "listening" in self._log.read_text().splitlines():
                return confirmation
            time.sleep(tolerances.POLL_FINE_S)
        raise AssertionError("the app never said it was listening")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class Dependent:
    """The backend, as a leaf that reads what the app has answered when the loop starts it."""

    def __init__(self, log: Path) -> None:
        self._log = log
        self.saw: list[list[str]] = []

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=tree.POSTGRES_UNIT,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="dependent_ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=5)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=tree.LEAF_BUDGET_S),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: Any, ctx: Any) -> Observation:
        self.saw.append(self._log.read_text().split("\n"))
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

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise AssertionError("a satisfied dependent is never advanced")

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise AssertionError("the dependent created nothing")


def rig_over_local_app(tmp_path: Path, mode: tuple[str, ...]) -> tuple[tk.TreeRig, Dependent, Path]:
    port, log = free_port(), tmp_path / "app-events.log"
    resolved = ports.Resolved(sys.executable, "3.12", "pin", "adoption")
    command = ports.BoundCommand(
        "app",
        (sys.executable, str(APP), *mode),
        {"PORT": str(port), "APP_EVENT_LOG": str(log), "PATH": "/usr/bin:/bin"},
        resolved,
        False,
    )
    spec = ports.ResourceSpec(
        tree.HTTP_SUPPORT_SERVICE, RealizationKind.AGENT_LAUNCHED_PROJECT, "http-app", command
    )
    local = LocalProcessPort()
    creating = AwaitListening(local, log)
    reads = HttpReadinessReads(local, tree.HTTP_READINESS, alive="ready")
    dependent = Dependent(log)
    units = {
        tree.HTTP_SUPPORT_UNIT: tree.ServiceUnit(
            tree.HTTP_SUPPORT_UNIT,
            tree.HTTP_SUPPORT_SERVICE,
            tree.HTTP_SUPPORT_READY,
            spec=spec,
        ),
        tree.POSTGRES_UNIT: dependent,
    }
    rig = tk.tree_rig(
        tmp_path,
        tree.ENTRY.units[tree.ROOT_UNIT],  # type: ignore[arg-type]
        units,
        port_impl={
            ports.ResourceReads: reads,
            ports.ResourceCreate: creating,
            ports.ResourceOwned: local,
        },
        deadline_s=tree.DEADLINE_S,
        request={schema.ENV_ARG: "proc"},
    )
    return rig, dependent, log


@pytest.mark.proves(
    "WR-VERIFY-2", "WR-VERIFY-2:b-readiness-ordering-proc", "B", "B", "PROC", "BOTH"
)
def test_dependent_starts_after_local_http_readiness_pass(tmp_path: Path) -> None:
    rig, dependent, log = rig_over_local_app(tmp_path, ("ready-after", str(REFUSED)))
    rig.run()
    rows = rig.rows()

    def first(path: str) -> int:
        return next(n for n, row in enumerate(rows) if row.get("path") == path)

    def end(path: str) -> int:
        return next(
            n for n, row in enumerate(rows) if row.get("path") == path and row["class"] == "end"
        )

    ends = rig.ends()
    assert ends[tree.HTTP_SUPPORT_UNIT]["condition"] == "satisfied"
    assert ends[tree.POSTGRES_UNIT]["condition"] == "satisfied"
    # the readiness pass (the supporting node's satisfied end) precedes the dependent's first entry
    assert end(tree.HTTP_SUPPORT_UNIT) < first(tree.POSTGRES_UNIT)
    # and the app itself had answered the declared response by the time the dependent was started
    assert dependent.saw and dependent.saw[0][-3:-1] == ["health 503", "health 200"]  # then a `""`
    # the app saw exactly the polls the contract needed: REFUSED refusals, then the pass
    events = log.read_text().splitlines()
    health = [e for e in events if e.startswith("health")]
    assert events[0] == "listening"
    assert " ".join(health) == " ".join(["health 503"] * REFUSED + ["health 200"])
    assert events[-1] == "stop"  # released with the run
    # polled on the declared wait and never slept past it: one poll interval before each of the
    # REFUSED + 1 observations that follow the create, and no other wait
    waits = rig.rig.cancel.waits
    assert waits == [timedelta(seconds=tree.READY_POLL_S)] * (REFUSED + 1)
