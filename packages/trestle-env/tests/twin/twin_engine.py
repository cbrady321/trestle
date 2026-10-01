"""Fakes and rigs the CI twins share (L.RB-2.1, L.RB-2.2; MC-B-03).

The tree's own units run through the loop on `FakeContainerEngine`. `SupportEngine` answers the
declared checks as the real ones would: `http_support_ready` is satisfied once the supporting
container has been asked `REFUSED` times before (the fake's stand-in for the read facet that makes
the real request; `serves_declared` False: it is up and listening but never serves the declared
response), `postgres_ready` once the container runs (`password_ok` False: authentication fails).
Not a test module: pytest never collects it."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from tests.tree import treekit as tk
from trestle.workflow import WorkflowEntry, ports
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    Repeat,
    WaitPolicy,
)
from trestle.workflow.values import CheckResult, Observation
from trestle_packs.fakes.container import FakeContainerEngine, selector_name

from trestle_env import schema, tree
from trestle_env.catalog import REFERENCE_PATH, Catalog
from trestle_env.plugins._tasks import TaskExecution

REFUSED = 3  # the fake serves the declared response from the request after this many refusals


class SupportEngine(FakeContainerEngine):
    """A fake Docker engine whose declared checks answer as the real ones would."""

    def __init__(
        self,
        *,
        serves_declared: bool = True,
        password_ok: bool = True,
        identity_ok: bool = True,
        configuration_ok: bool = True,
    ) -> None:
        super().__init__()
        self.serves_declared = serves_declared
        self.password_ok = password_ok
        self.identity_ok = identity_ok  # a found Postgres carries the reference role and database
        self.configuration_ok = configuration_ok  # ... and the pinned major version
        self.asked: list[str] = []
        self.effects: list[tuple[str, str]] = []  # every effect the engine was asked for, by name

    def check(self, check: str, target: Any) -> CheckResult:
        running = super().check("running", target)
        if check == "running" or not running.satisfied:
            return running
        self.asked.append(check)
        if check == tree.HTTP_SUPPORT_READY:
            asked = self.asked.count(check)
            ok = self.serves_declared and asked > REFUSED
            return CheckResult(ok, None, f"GET {tree.HTTP_SUPPORT_READINESS.path}: answer {asked}")
        if check == tree.POSTGRES_READY:
            return CheckResult(self.password_ok, None, "psql SELECT 1")
        if check == tree.POSTGRES_IDENTITY:
            return CheckResult(self.identity_ok, None, "role and database of the reference stack")
        if check == tree.POSTGRES_CONFIGURATION:
            return CheckResult(self.configuration_ok, None, "the pinned Postgres major version")
        return CheckResult(False, None, f"{check} is not a check this engine knows")

    # every effect is recorded by the name it acts on, so a test can show what was never touched
    def create(self, spec: Any, ticket: Any) -> Any:
        self.effects.append(("create", selector_name(ticket.lineage)))
        return super().create(spec, ticket)

    def stop(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("stop", target.selector))
        return super().stop(target, ticket)

    def restart(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("restart", target.selector))
        return super().restart(target, ticket)

    def recreate(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("recreate", target.selector))
        return super().recreate(target, ticket)

    def start(self, target: Any, ticket: Any) -> Any:
        self.effects.append(("start", target.selector))
        return super().start(target, ticket)


TEST_ID = "demo-version"
TEST_NODE = tree.task_unit_name(TEST_ID)


class Probe:
    """A leaf with no resource and no effect, ready as soon as the loop looks at it: a stand-in for
    a node the case is not about, and the dependent whose start a case orders. `observed` counts
    the times the loop observed it; `at_observe` runs with each observation (a case reads what an
    app had answered by then)."""

    def __init__(self, unit: str, at_observe: Callable[[], None] | None = None) -> None:
        self._unit = unit
        self._at_observe = at_observe
        self.observed = 0

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="probe_ready",
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
        self.observed += 1
        if self._at_observe is not None:
            self._at_observe()
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
        raise AssertionError("a probe is satisfied on observation and never advanced")

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise AssertionError("a probe creates nothing")


def catalog_file(directory: Path) -> Path:
    """A catalog file (for `TRESTLE_ENV_CATALOG`) that lists the one test, `demo-version`."""
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    data["tests"] = [{"id": TEST_ID, "project": "demo-py", "task": "version"}]
    path = directory / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def entry_with_test() -> WorkflowEntry:
    """The reference tree over a catalog that lists one test, `demo-version`: its node needs both
    backends' readiness pass, and (the request not naming it) runs nothing."""
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    data["tests"] = [{"id": TEST_ID, "project": "demo-py", "task": "version"}]
    return tree.build_entry(Catalog.from_data(data))


def rig_over(
    tmp_path: Path, engine: SupportEngine, entry: WorkflowEntry | None = None
) -> tk.TreeRig:
    shown = tree.ENTRY if entry is None else entry
    return tk.tree_rig(
        tmp_path,
        shown.units[tree.ROOT_UNIT],  # type: ignore[arg-type]
        {name: unit for name, unit in shown.units.items() if name != tree.ROOT_UNIT},
        port_impl={
            ports.ResourceReads: engine,
            ports.ResourceCreate: engine,
            ports.ResourceOwned: engine,
            ports.ResourceSafeStart: engine,
            ports.ExecutionPort: TaskExecution(None, None),  # a run of nothing (no test named)
        },
        deadline_s=tree.DEADLINE_S,
        request={schema.ENV_ARG: "twin"},
    )


def first_row(rows: list[dict[str, Any]], path: str) -> int:
    return next(n for n, row in enumerate(rows) if row.get("path") == path)


def end_row(rows: list[dict[str, Any]], path: str) -> int:
    return next(
        n for n, row in enumerate(rows) if row.get("path") == path and row["class"] == "end"
    )
