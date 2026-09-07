"""Wave planning — topological ordering for staged orchestration."""

from __future__ import annotations

from dataclasses import dataclass


class CycleError(ValueError):
    """Raised when wave dependencies contain a cycle."""


@dataclass(frozen=True)
class WavePlan:
    """Ordered waves of service names to start sequentially."""

    waves: tuple[tuple[str, ...], ...]

    def flat(self) -> tuple[str, ...]:
        return tuple(name for wave in self.waves for name in wave)


def plan_waves(
    services: list[str],
    *,
    depends_on: dict[str, list[str]] | None = None,
    explicit_waves: list[list[str]] | None = None,
) -> WavePlan:
    """Resolve service startup order.

    If ``explicit_waves`` is provided, each wave runs after the prior wave
    completes. Within-wave order is alphabetical. Dependencies must not point
    forward across waves.

    Otherwise, ``depends_on`` is topologically sorted into waves (Kahn).
    """
    if explicit_waves is not None:
        return _plan_explicit_waves(services, explicit_waves, depends_on or {})

    graph = {name: list(depends_on.get(name, [])) if depends_on else [] for name in services}
    return WavePlan(waves=_kahn_waves(graph))


def _plan_explicit_waves(
    services: list[str],
    explicit_waves: list[list[str]],
    depends_on: dict[str, list[str]],
) -> WavePlan:
    known = set(services)
    seen: set[str] = set()
    waves: list[tuple[str, ...]] = []

    for wave in explicit_waves:
        ordered = tuple(sorted(name for name in wave if name in known))
        for name in ordered:
            if name not in known:
                msg = f"unknown service in wave: {name}"
                raise ValueError(msg)
            for dep in depends_on.get(name, []):
                if dep not in seen:
                    msg = f"service {name} depends on {dep} which is not in a prior wave"
                    raise ValueError(msg)
        waves.append(ordered)
        seen.update(ordered)

    leftover = sorted(known - seen)
    if leftover:
        waves.append(tuple(leftover))

    return WavePlan(waves=tuple(waves))


def _kahn_waves(graph: dict[str, list[str]]) -> tuple[tuple[str, ...], ...]:
    nodes = set(graph)
    for deps in graph.values():
        for dep in deps:
            if dep not in nodes:
                msg = f"unknown dependency: {dep}"
                raise ValueError(msg)

    incoming = {node: len(graph[node]) for node in graph}
    dependents: dict[str, list[str]] = {node: [] for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            dependents[dep].append(node)

    waves: list[tuple[str, ...]] = []
    remaining = set(graph)

    while remaining:
        ready = sorted(node for node in remaining if incoming[node] == 0)
        if not ready:
            raise CycleError("dependency cycle detected")

        waves.append(tuple(ready))
        for node in ready:
            remaining.remove(node)
            for child in dependents[node]:
                incoming[child] -= 1

    return tuple(waves)
