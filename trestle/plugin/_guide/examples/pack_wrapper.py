"""A pack wrapper: a few lines over a library, with the library declared in `packages=`.

A pack is a `pack.toml`, wrapper files like this one under `plugins/`, and the library they call,
installed by the operator with `trestle pack install`. Declaring the library records a digest of its code at
publication; a run that finds the library changed stops with `execution.provenance_mismatch`
until the wrapper is published again. The library here is `trestle_packs`; use your own."""

from __future__ import annotations

from trestle_packs import CycleError, plan_waves

from trestle.plugin import Blocked, Context, trestle


@trestle(packages=["trestle_packs"])
def pack_wrapper(
    ctx: Context,
    services: list[str],
    depends_on: dict[str, list[str]] | None = None,
) -> dict[str, list[list[str]]]:
    try:
        plan = plan_waves(services, depends_on=depends_on)
    except CycleError as exc:
        raise Blocked(
            "dependency_cycle", f"Fix the dependency list: {exc}", "will_not_succeed"
        ) from exc
    ctx.log(f"planned {len(plan.waves)} waves for {len(services)} services")
    return {"waves": [list(wave) for wave in plan.waves]}
