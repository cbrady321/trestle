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
from trestle_packs.fakes.container import FakeContainerEngine

from trestle_env import schema, tree

REFUSED = 3  # the fake serves the declared response from the request after this many refusals


class SupportEngine(FakeContainerEngine):
    """A fake Docker engine whose declared checks answer as the real ones would."""

    def __init__(self, *, serves_declared: bool = True, password_ok: bool = True) -> None:
        super().__init__()
        self.serves_declared = serves_declared
        self.password_ok = password_ok
        self.asked: list[str] = []

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
        return CheckResult(False, None, f"{check} is not a check this engine knows")


def rig_over(tmp_path: Path, engine: SupportEngine) -> tk.TreeRig:
    return tk.tree_rig(
        tmp_path,
        tree.ENTRY.units[tree.ROOT_UNIT],  # type: ignore[arg-type]
        {
            tree.HTTP_SUPPORT_UNIT: tree.ENTRY.units[tree.HTTP_SUPPORT_UNIT],
            tree.POSTGRES_UNIT: tree.ENTRY.units[tree.POSTGRES_UNIT],
        },
        port_impl={
            ports.ResourceReads: engine,
            ports.ResourceCreate: engine,
            ports.ResourceOwned: engine,
            ports.ResourceSafeStart: engine,
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
