"""`python -m tests.proof.differ d8 [--pair <family>]` (MC-06 mode d8; CSC-5 builder L.SL-3.3).

Fake == real, one suite (SA-14, B3-C17). The conformance suite says each implementation of a port
family satisfies the contract; d8 says the fake and the real one are also *the same* on everything
a caller can see: one scripted scenario is driven through each implementation, every answer is
normalized (instants, found-instance selectors, pids and start tokens are what differ by nature;
everything else must not) and the two transcripts are compared step by step.

`--pair process` drives `[fake-local]` and `[real-local]` (Local Process Supervision) through
create, idempotent re-create, a found instance, repair, stop and the refusals. A pair with no
builder yet is refused with exit 2. Exit 0: zero differences; 1: at least one; 2: usage.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import tempfile
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from tests.proof.suites.ports import core, families, implementations
from trestle.workflow import ports
from trestle.workflow.declarations import Lifetime, Vantage
from trestle.workflow.values import CreatedHandle

Scenario = Callable[[core.Implementation], list[tuple[str, Any]]]

_FOUND = re.compile(r"found-\d+-\d+")


def normalize(value: Any, planted: str = "") -> Any:
    """A JSON-shaped, implementation-neutral view of one answer."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return "<instant>"
    if isinstance(value, str):
        if value == planted:
            return "<found:planted>"
        return _FOUND.sub("<found>", value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: normalize(getattr(value, f.name), planted) for f in dataclasses.fields(value)
        }
    if isinstance(value, (tuple, list, frozenset, set)):
        items = [normalize(v, planted) for v in value]
        if isinstance(value, (frozenset, set)):
            items.sort(key=repr)
        return items
    if isinstance(value, dict):
        return {str(k): normalize(v, planted) for k, v in sorted(value.items(), key=str)}
    if isinstance(value, Path):
        return "<path>"
    return value


def _sorted_found(answer: Any) -> Any:
    """`found` is a set of instances: its order is by selector, which carries the pid."""
    if isinstance(answer, dict) and isinstance(answer.get("found"), list):
        return {**answer, "found": sorted(answer["found"], key=repr)}
    return answer


def _step(log: list[tuple[str, Any]], name: str, fn: Callable[[], Any], planted: str = "") -> Any:
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - a refusal is part of the transcript
        log.append((name, f"raises {type(exc).__name__}"))
        return None
    log.append((name, _sorted_found(normalize(result, planted))))
    return result


def process_scenario(built: core.Implementation) -> list[tuple[str, Any]]:
    """The Local Process Supervision scenario (B3-C1..C5, B3-C21)."""
    impl, spec = built.impl, built.extras["spec"]
    log: list[tuple[str, Any]] = []
    lineage = families.LINEAGE

    def observe(effect: str | None = "up") -> Any:
        return impl.observe(spec, lineage, effect)

    def call(member: str, lifetime: Lifetime = Lifetime.RUN, **arguments: Any) -> Any:
        return ports.as_descriptor(
            impl.release_descriptor(families.effect_call(member, arguments, lifetime, "up"))
        )

    _step(log, "observe before create", observe)
    _step(log, "observe with no effect", lambda: observe(None))
    _step(log, "launch policy", lambda: impl.launch_policy(spec))
    _step(log, "descriptor create RUN", lambda: call("create", spec=spec))
    _step(log, "descriptor create DURABLE", lambda: call("create", Lifetime.DURABLE, spec=spec))
    _step(
        log,
        "create DURABLE",
        lambda: impl.create(spec, families.ticket("up", _create(), Lifetime.DURABLE)),
    )
    planted = built.extras["plant_found"](spec.logical_system)
    _step(log, "observe with a found instance", observe, planted)
    before = built.reach.engine_inventory()["containers"]
    first = _step(
        log, "create", lambda: impl.create(spec, families.ticket("up", _create())), planted
    )
    _step(
        log, "create again", lambda: impl.create(spec, families.ticket("up", _create(), attempt=2))
    )
    added = built.reach.engine_inventory()["containers"] - before
    log.append(("instances added", sorted(normalize(v) for v in added)))
    seen = _step(log, "observe after create", observe, planted)
    ref = seen.selector_ref
    _step(log, "check ready", lambda: impl.check("ready", ref))
    _step(log, "check unknown", lambda: impl.check("healthy", ref))
    _step(log, "endpoint host", lambda: impl.endpoint(ref, Vantage.HOST))
    _step(log, "endpoint container", lambda: impl.endpoint(ref, Vantage.CONTAINER))
    _step(log, "create second effect", lambda: impl.create(spec, families.ticket("two", _create())))
    _step(log, "observe sees the second as found", observe, planted)
    handle = CreatedHandle(lineage, "up", first.identity, ports.InRunGroup())
    owned = families.ticket("repair", families.EffectFacetClass.OWNED)
    _step(log, "descriptor restart", lambda: call("restart", target=handle))
    _step(log, "restart", lambda: impl.restart(handle, owned))
    _step(log, "observe after restart", observe, planted)
    _step(log, "recreate", lambda: impl.recreate(handle, owned))
    _step(log, "check ready after recreate", lambda: impl.check("ready", ref))
    _step(log, "stop", lambda: impl.stop(handle, owned))
    _step(log, "observe after stop", observe, planted)
    _step(log, "check ready after stop", lambda: impl.check("ready", ref))
    _step(log, "stop again", lambda: impl.stop(handle, owned))
    _step(log, "restart after stop", lambda: impl.restart(handle, owned))
    _step(log, "endpoint after stop", lambda: impl.endpoint(ref, Vantage.HOST))
    unknown = CreatedHandle(lineage, "never", "proc-none", ports.InRunGroup())
    _step(log, "stop a handle it does not hold", lambda: impl.stop(unknown, owned))
    return log


def _create() -> Any:
    return families.EffectFacetClass.CREATE


# pair -> (family, fake id, real id, scenario)
PAIRS: dict[str, tuple[str, str, str, Scenario]] = {
    "process": (families.LOCAL_PROCESS_SUPERVISION, "fake-local", "real-local", process_scenario),
}


def transcript(impl_id: str, scenario: Scenario) -> list[tuple[str, Any]]:
    _, factory = implementations.IMPLEMENTATIONS[impl_id]
    with tempfile.TemporaryDirectory(prefix="d8-") as tmp:
        built = factory(Path(tmp))
        try:
            return scenario(built)
        finally:
            if built.close is not None:
                built.close()


def compare(fake: list[tuple[str, Any]], real: list[tuple[str, Any]]) -> list[str]:
    diffs: list[str] = []
    for index in range(max(len(fake), len(real))):
        left = fake[index] if index < len(fake) else ("<missing>", None)
        right = real[index] if index < len(real) else ("<missing>", None)
        if left != right:
            diffs.append(f"step {index} {left[0]!r}: fake {left[1]!r} != real {right[1]!r}")
    return diffs


def main(args: argparse.Namespace) -> int:
    names = [args.pair] if args.pair else sorted(PAIRS)
    unknown = [n for n in names if n not in PAIRS]
    if unknown:
        print(f"differ d8: no builder for pair {unknown[0]!r}; built: {', '.join(sorted(PAIRS))}")
        return 2
    total = 0
    for name in names:
        _, fake_id, real_id, scenario = PAIRS[name]
        fake, real = transcript(fake_id, scenario), transcript(real_id, scenario)
        diffs = compare(fake, real)
        total += len(diffs)
        print(f"differ d8 --pair {name}: {len(fake)} steps, {len(diffs)} differences")
        for line in diffs:
            print(f"  {line}")
    return 0 if total == 0 else 1
