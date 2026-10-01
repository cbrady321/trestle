"""Fakes and rigs the CI twins share (L.RB-2.1, L.RB-2.2; MC-B-03).

The tree's own units run through the loop on `FakeContainerEngine`. `SupportEngine` answers the
declared checks as the real ones would: `http_support_ready` is satisfied once the supporting
container has been asked `REFUSED` times before (the fake's stand-in for the read facet that makes
the real request; `serves_declared` False: it is up and listening but never serves the declared
response), `postgres_ready` once the container runs (`password_ok` False: authentication fails).
Not a test module: pytest never collects it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.tree import treekit as tk
from trestle.workflow import ports
from trestle.workflow.values import CheckResult
from trestle_packs.fakes.container import FakeContainerEngine, selector_name

from trestle_env import schema, tree
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


def rig_over(tmp_path: Path, engine: SupportEngine) -> tk.TreeRig:
    return tk.tree_rig(
        tmp_path,
        tree.ENTRY.units[tree.ROOT_UNIT],  # type: ignore[arg-type]
        {name: unit for name, unit in tree.ENTRY.units.items() if name != tree.ROOT_UNIT},
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
