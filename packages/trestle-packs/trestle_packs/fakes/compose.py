"""Fake Compose resolver (L.NW-2.4; B3-C13, B3-C20, MC-B-01, MC-25, DM-09).

Stdlib only. `FakeComposeResolver(projects)` implements `ComposeResolver` over compose fixtures
written in JSON syntax (JSON is YAML): the fake parses the file with `json` and reads only each
service's `depends_on` (a list of names, or a mapping keyed by name, the two forms Compose
accepts). It is read-only and writes nothing.

`closure(project, selected)` returns the selected services plus everything they depend on,
transitively, with an edge `(dependent, dependency)` for every dependency inside the closure and
the definition's fingerprint: the sha256 of the canonical JSON of `{"services": {name: sorted
dependency names}}` over EVERY service of the definition (so any change of who depends on whom
changes it, and it does not depend on the selection). The real resolver (L.NW-2.7) computes the
same fingerprint from `docker compose config --format json`, so the two agree over the same file.

Refusals (a value, never an exception): an unknown selected name is
`ClosureRefused(UNKNOWN_IDENTIFIER)` naming the first such name in sorted order; a definition that
cannot be read as a valid definition (missing, not JSON, no `services` mapping, a dependency on a
service the definition lacks) is `COMPOSE_DEFINITION_INVALID`; a closure over `IDSET_MAX` services
or `4 x IDSET_MAX` edges is `BOUND_EXCEEDED`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UNKNOWN_IDENTIFIER = "admission.unknown_identifier"
BOUND_EXCEEDED = "admission.bound_exceeded"
COMPOSE_DEFINITION_INVALID = "adapter.compose_definition_invalid"
IDSET_MAX = 1024  # V-13: an identifier set; Closure.edges is at most 4 x IDSET_MAX


@dataclass(frozen=True, slots=True)
class Closure:
    services: frozenset[str]
    edges: frozenset[tuple[str, str]]
    definition_fingerprint: str


@dataclass(frozen=True, slots=True)
class ClosureRefused:
    code: str
    subject: str


def dependencies(service: object) -> list[str] | None:
    """The names a service depends on, or None if `depends_on` is neither a list nor a mapping."""
    if not isinstance(service, Mapping):
        return None
    raw = service.get("depends_on", [])
    if isinstance(raw, Mapping):
        return sorted(str(name) for name in raw)
    if isinstance(raw, list) and all(isinstance(name, str) for name in raw):
        return sorted(raw)
    return None


def fingerprint(graph: Mapping[str, list[str]]) -> str:
    canonical = json.dumps({"services": {k: sorted(v) for k, v in graph.items()}}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def close_over(
    graph: Mapping[str, list[str]], selected: frozenset[str]
) -> Closure | ClosureRefused:
    """The pure part both resolvers share by construction of the contract (B3-C13)."""
    for name in sorted(selected):
        if name not in graph:
            return ClosureRefused(UNKNOWN_IDENTIFIER, name)
    members: set[str] = set()
    pending = sorted(selected)
    while pending:
        name = pending.pop()
        if name in members:
            continue
        members.add(name)
        pending.extend(graph[name])
    edges = frozenset((name, dep) for name in members for dep in graph[name])
    if len(members) > IDSET_MAX or len(edges) > 4 * IDSET_MAX:
        return ClosureRefused(BOUND_EXCEEDED, f"{len(members)} services, {len(edges)} edges")
    return Closure(frozenset(members), edges, fingerprint(graph))


class FakeComposeResolver:
    """`ComposeResolver` over JSON-syntax compose fixtures, keyed by project (catalog entry)."""

    def __init__(self, projects: Mapping[str, str | Path]) -> None:
        self.projects = {name: Path(path) for name, path in projects.items()}

    def closure(self, project: str, selected: frozenset[str]) -> Closure | ClosureRefused:
        graph = self._graph(project)
        if isinstance(graph, ClosureRefused):
            return graph
        return close_over(graph, frozenset(selected))

    def _graph(self, project: str) -> dict[str, list[str]] | ClosureRefused:
        path = self.projects.get(project)
        if path is None:
            return ClosureRefused(COMPOSE_DEFINITION_INVALID, f"no definition for {project}")
        try:
            document: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ClosureRefused(COMPOSE_DEFINITION_INVALID, f"{path.name}: {exc}"[:200])
        services = document.get("services") if isinstance(document, Mapping) else None
        if not isinstance(services, Mapping) or not services:
            return ClosureRefused(COMPOSE_DEFINITION_INVALID, f"{path.name}: no services")
        graph: dict[str, list[str]] = {}
        for name, service in services.items():
            deps = dependencies(service)
            if deps is None:
                return ClosureRefused(COMPOSE_DEFINITION_INVALID, f"{name}: bad depends_on")
            graph[str(name)] = deps
        for name, deps in graph.items():
            missing = [d for d in deps if d not in graph]
            if missing:
                return ClosureRefused(COMPOSE_DEFINITION_INVALID, f"{name} needs {missing[0]}")
        return graph
