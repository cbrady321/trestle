"""`python -m tests.proof.differ d8 [--pair <family>]` (MC-06 mode d8; CSC-5 builder L.SL-3.3).

Fake == real, one suite (SA-14, B3-C17). The conformance suite says each implementation of a port
family satisfies the contract; d8 says the fake and the real one are also *the same* on everything
a caller can see: one scripted scenario is driven through each implementation, every answer is
normalized (instants, found-instance selectors, pids and start tokens are what differ by nature;
everything else must not) and the two transcripts are compared step by step.

`--pair process` drives `[fake-local]` and `[real-local]` (Local Process Supervision) through
create, idempotent re-create, a found instance, repair, stop and the refusals. A pair with no
builder yet is refused with exit 2. Exit 0: zero differences; 1: at least one; 2: usage.

`--pair container` and `--pair compose` (L.NW-2.8) have a real side only on the host-docker gate:
their scenarios (`container_scenario`, `compose_scenario`) are driven through the fake and the real
adapter by the `docker_host` nodes named in `RECORD_PAIRS`, which assert zero differences. Outside
the gate this mode is a check over the host-docker record admissible for HEAD (CM-6): the pair is
equal only when that record is a `run` in which its node PASSED; otherwise the real side is
reported UNPROVEN, never equal, and the exit is 3 (no record, a PRECONDITION_UNMET record, or the
node not PASSED). It registers no label.
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
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Vantage
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

# -- the Docker pairs (L.NW-2.8): driven on the host-docker gate, read back from its record -------

UNPROVEN_EXIT = 3
REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTAINER_NODES = "packages/trestle-packs/tests/container/test_conformance.py"
# pair -> the docker_host node that drives the scenario through `[fake]` and `[real]` on the gate
RECORD_PAIRS: dict[str, str] = {
    "container": f"{_CONTAINER_NODES}::test_d8_container_pair[real]",
    "compose": f"{_CONTAINER_NODES}::test_d8_compose_pair[real]",
}
_HEX_ID = re.compile(r"\b[0-9a-f]{12,64}\b")


def _neutral(built: core.Implementation) -> Callable[[Any], Any]:
    """What differs between two engines by nature: the bound docker path and endpoint, engine
    object ids, and the published host port. Everything else must not."""
    executable = str(built.extras.get("executable") or "")
    endpoint = str(built.extras.get("endpoint") or "")

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            if executable:
                value = value.replace(executable, "<docker>")
            if endpoint:
                value = value.replace(endpoint, "<endpoint>")
            return _HEX_ID.sub("<id>", value)
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        return value

    return scrub


def container_scenario(built: core.Implementation) -> list[tuple[str, Any]]:
    """The Container Control scenario (B3-C1..C6, MC-B-01): the process scenario's steps over the
    container port, with the published host port and the engine-bound values neutralized."""
    impl, spec = built.impl, built.extras["spec"]
    lineage = families.LINEAGE
    raw: list[tuple[str, Any]] = []

    def observe(effect: str | None = "up") -> Any:
        return impl.observe(spec, lineage, effect)

    def call(member: str, lifetime: Lifetime = Lifetime.RUN, **arguments: Any) -> Any:
        return ports.as_descriptor(
            impl.release_descriptor(families.effect_call(member, arguments, lifetime, "up"))
        )

    def host_endpoint(ref: Any) -> Any:
        answer = impl.endpoint(ref, Vantage.HOST)
        if dataclasses.is_dataclass(answer) and hasattr(answer, "port"):
            return {**normalize(answer), "port": "<published>"}  # the engine picks the host port
        return answer

    _step(raw, "observe before create", observe)
    _step(raw, "observe with no effect", lambda: observe(None))
    _step(raw, "launch policy", lambda: impl.launch_policy(spec))
    _step(raw, "descriptor create RUN", lambda: call("create", spec=spec))
    _step(raw, "descriptor create DURABLE", lambda: call("create", Lifetime.DURABLE, spec=spec))
    planted = built.extras["plant_found"](spec.logical_system)
    _step(raw, "observe with a found instance", observe, planted)
    first = _step(raw, "create", lambda: impl.create(spec, families.ticket("up", _create())))
    _step(
        raw, "create again", lambda: impl.create(spec, families.ticket("up", _create(), attempt=2))
    )
    seen = _step(raw, "observe after create", observe, planted)
    ref = seen.selector_ref if seen is not None else None
    _step(raw, "check running", lambda: impl.check("running", ref))
    _step(raw, "check unknown", lambda: impl.check("healthy", ref))
    _step(raw, "endpoint host", lambda: host_endpoint(ref))
    _step(raw, "endpoint container", lambda: impl.endpoint(ref, Vantage.CONTAINER))
    identity = first.identity if first is not None else "none"
    handle = CreatedHandle(lineage, "up", identity, call("create", spec=spec))
    owned = families.ticket("repair", EffectFacetClass.OWNED)
    _step(raw, "restart", lambda: impl.restart(handle, owned))
    _step(raw, "check running after restart", lambda: impl.check("running", ref))
    _step(raw, "recreate", lambda: impl.recreate(handle, owned))
    _step(raw, "check running after recreate", lambda: impl.check("running", ref))
    _step(raw, "stop", lambda: impl.stop(handle, owned))
    _step(raw, "observe after stop", observe, planted)
    _step(raw, "check running after stop", lambda: impl.check("running", ref))
    _step(raw, "endpoint after stop", lambda: impl.endpoint(ref, Vantage.HOST))
    _step(raw, "restart after stop", lambda: impl.restart(handle, owned))
    scrub = _neutral(built)
    return [(name, scrub(answer)) for name, answer in raw]


def compose_scenario(built: core.Implementation) -> list[tuple[str, Any]]:
    """The Compose resolver scenario (B3-C13, B3-C20): closures, the fingerprint across a changed
    definition, and every refusal."""
    impl, extras = built.impl, built.extras
    log: list[tuple[str, Any]] = []

    def closure(project: str, *selected: str) -> Any:
        return impl.closure(project, frozenset(selected))

    project = extras["project"]
    _step(log, "closure of a chain", lambda: closure(project, "web"))
    _step(log, "closure of a lone service", lambda: closure(project, "cache"))
    _step(log, "closure of a selection", lambda: closure(project, "web", "worker"))
    _step(
        log, "closure of the changed definition", lambda: closure(extras["changed_project"], "web")
    )
    _step(log, "an unknown service", lambda: closure(project, "nope"))
    _step(log, "an unreadable definition", lambda: closure(extras["invalid_project"], "web"))
    _step(log, "a closure over the bound", lambda: closure(extras["oversized_project"], "s0"))
    _step(log, "an unbound project", lambda: closure("no-such-project", "web"))
    # an unreadable definition's subject is the reader's own words (json vs `compose config`);
    # its code, and every other answer, must be equal
    return [
        (name, {**answer, "subject": "<reader's reason>"})
        if isinstance(answer, dict) and answer.get("code") == "adapter.compose_definition_invalid"
        else (name, answer)
        for name, answer in log
    ]


def pair_transcripts(
    scenario: Scenario, fake: core.Implementation, real: core.Implementation
) -> tuple[list[tuple[str, Any]], list[tuple[str, Any]]]:
    """Drive one scenario through each built implementation (the gate's `docker_host` nodes)."""
    out = []
    for built in (fake, real):
        try:
            out.append(scenario(built))
        finally:
            if built.close is not None:
                built.close()
    return out[0], out[1]


def record_verdict(name: str, cwd: Path | None = None) -> tuple[bool, str]:
    """(equal, reason) for a Docker pair from the host-docker record admissible for HEAD."""
    from tests.proof import fence as fence_mod
    from tests.proof.host import record as record_mod

    root = cwd or REPO_ROOT
    head = fence_mod._git(root, "rev-parse", "HEAD").stdout.strip()  # noqa: SLF001
    record = record_mod.select("host-docker", head, cwd=root)
    node = RECORD_PAIRS[name]
    if record is None:
        return False, "no host-docker record admissible for HEAD"
    if record.get("mode") != "run":
        return False, f"host-docker record {record['sha'][:12]} is {record.get('status')}"
    outcome = next((r.get("outcome") for r in record["results"] if r.get("nodeid") == node), None)
    if outcome != "PASSED":
        return False, f"host-docker record {record['sha'][:12]}: {node} is {outcome or 'absent'}"
    return True, f"host-docker record {record['sha'][:12]}: {node} PASSED"


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
    built = sorted({*PAIRS, *RECORD_PAIRS})
    names = [args.pair] if args.pair else built
    unknown = [n for n in names if n not in built]
    if unknown:
        print(f"differ d8: no builder for pair {unknown[0]!r}; built: {', '.join(built)}")
        return 2
    total = unproven = 0
    for name in names:
        if name in RECORD_PAIRS:
            equal, reason = record_verdict(name)
            if equal:
                print(f"differ d8 --pair {name}: equal on the host ({reason})")
            else:
                unproven += 1
                print(f"differ d8 --pair {name}: real = UNPROVEN ({reason})")
            continue
        _, fake_id, real_id, scenario = PAIRS[name]
        fake, real = transcript(fake_id, scenario), transcript(real_id, scenario)
        diffs = compare(fake, real)
        total += len(diffs)
        print(f"differ d8 --pair {name}: {len(fake)} steps, {len(diffs)} differences")
        for line in diffs:
            print(f"  {line}")
    if total:
        return 1
    return UNPROVEN_EXIT if unproven else 0
