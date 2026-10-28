"""Shared helpers of the SV-3 host tests: an in-process kernel over plugins written to a scratch
directory, and workflow plugin sources (a one-vertex declared tree) with optional release
effects."""

from __future__ import annotations

import json
from pathlib import Path

from trestle.server.main import Kernel, create_kernel

REPO = Path(__file__).resolve().parents[3]
ECHO = REPO / "examples" / "plugins" / "echo.py"

_WORKFLOW = """
from __future__ import annotations

from datetime import timedelta

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

EFFECTS = (__EFFECTS__)


class Unit:
    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit="unit",
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=20)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=EFFECTS,
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=__BUDGET__),
            max_attempts=1,
        )


ENTRY = WorkflowEntry(root="unit", units={"unit": Unit()}, deadline=timedelta(seconds=60))


@trestle(deadline=__DEADLINE__)
def __NAME__(ctx: Context, name: str = "x", other: str = "y") -> dict[str, str]:
    return {"name": name}
"""


def workflow_source(
    name: str = "wf",
    *,
    budget_s: int = 30,
    deadline_s: int = 300,
    release_timeouts_s: tuple[int, ...] = (),
) -> str:
    """A workflow plugin whose declared tree is one leaf: `release_timeouts_s` gives it one
    CREATE + RUN effect per entry (one release target each, B2-C2 (5))."""
    effects = "".join(
        f"""
    EffectDeclaration(
        effect="create_{i}",
        facet=EffectFacetClass.CREATE,
        verb="",
        lifetime=Lifetime.RUN,
        host_sections=frozenset(),
        release_timeout=timedelta(seconds={t}),
    ),"""
        for i, t in enumerate(release_timeouts_s)
    )
    return (
        _WORKFLOW.replace("__EFFECTS__", effects + ("\n" if effects else ""))
        .replace("__BUDGET__", str(budget_s))
        .replace("__DEADLINE__", str(deadline_s))
        .replace("__NAME__", name)
    )


def make_kernel(home: Path, plugins: dict[str, str]) -> Kernel:
    """A kernel over `home` whose plugin directory holds `plugins` (file stem -> source)."""
    plugin_dir = home / "plugin-src"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for stem, source in plugins.items():
        (plugin_dir / f"{stem}.py").write_text(source, encoding="utf-8")
    return create_kernel(home=home / "trestle-home", plugin_dirs=[plugin_dir], skip_recovery=True)


def read_spec(run_dir: Path) -> dict[str, object]:
    loaded = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def run_dir_of(kernel: Kernel, run_id: str) -> Path:
    found = list((kernel.home / "runs").glob(f"*/{run_id}"))
    assert len(found) == 1, run_id
    return found[0]
