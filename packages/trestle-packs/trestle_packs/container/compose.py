"""Real Compose resolver: `ComposeResolver.closure` over `docker compose config --format json`
(L.NW-2.7; B3-C13, B3-C20, V-11, V-13, MC-25, MC-B-01).

`RealComposeResolver(docker, projects)` reads a project's live Compose definition with ONE
read-only invocation, run through the injected `ExecutionPort` like every other docker call
(`DockerCli`: stdin closed, environment built from empty, attributable to the run)::

    <docker> [--host <endpoint>] compose -f <definition file> config --format json --no-interpolate

`config` renders the definition and starts nothing, so the read writes nothing (B3-C20:
`INCIDENTAL_WRITES` is empty). `--no-interpolate` leaves `${TRESTLE_IMAGE_<ROLE>}` unresolved
(the environment of the call is empty and the closure needs only who depends on whom).

The JSON is parsed into the MC-25 `Closure` the fake (`trestle_packs.fakes.compose`) returns over
the same file, by the same rule: only each service's `depends_on` is read (a list of names, or the
mapping keyed by name that `config` prints); the closure is the selection plus everything it
depends on, transitively; an edge is `(dependent, dependency)` for every dependency inside the
closure; `definition_fingerprint` is the sha256 of the canonical JSON of `{"services": {name:
sorted dependency names}}` over EVERY service of the definition (so it depends on the definition,
never on the selection). A container-package module imports only the standard library and
`trestle.workflow` (BFD-47), so the rule is restated here, not imported from the fakes; the
conformance suite runs unmodified against both.

Refusals are values (`ClosureRefused`), never exceptions, and there is no other code (V-11):
an unknown selected name is `UNKNOWN_IDENTIFIER` naming the first such name in sorted order; a
definition that cannot be read as a valid one (no file bound to the project, the CLI missing or
not startable, the engine or the read interrupted, a non-zero exit, output that is not JSON, no
`services`, a bad `depends_on`, a dependency on a service the definition lacks) is
`COMPOSE_DEFINITION_INVALID` with the reason as its subject; a closure over `IDSET_MAX` services or
`4 x IDSET_MAX` edges is `BOUND_EXCEEDED`.

Known bound: the port hands an adapter only the console TAIL of a command, at most `TEXT_MAX`
bytes (B3-C14, `ExecutionResult.excerpt`), and a rendered definition of more than a few services
is longer than that. Output that fills the excerpt and does not parse is refused as
`COMPOSE_DEFINITION_INVALID` saying so; the resolver never returns a closure computed from a
truncated document.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any, Final

from trestle.workflow.ports import CatalogEntry, Closure, ClosureRefused

from trestle_packs.container.engine import COMPOSE_DEFINITION_INVALID, TEXT_MAX, DockerCli

UNKNOWN_IDENTIFIER: Final = "admission.unknown_identifier"
BOUND_EXCEEDED: Final = "admission.bound_exceeded"
IDSET_MAX: Final = 1024  # V-13: an identifier set; Closure.edges is at most 4 x IDSET_MAX
SUBJECT_MAX: Final = 200  # executor-chosen: well inside TEXT_MAX (BoundedText)


def config_args(definition: str) -> tuple[str, ...]:
    """The arguments of the one read, after `<docker> [--host <endpoint>]`."""
    return ("compose", "-f", definition, "config", "--format", "json", "--no-interpolate")


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


def dependency_graph(document: object) -> dict[str, list[str]] | str:
    """`{service: sorted dependency names}` for a parsed definition, or the reason it is invalid."""
    services = document.get("services") if isinstance(document, Mapping) else None
    if not isinstance(services, Mapping) or not services:
        return "the definition has no services"
    graph: dict[str, list[str]] = {}
    for name, service in services.items():
        deps = dependencies(service)
        if deps is None:
            return f"{name}: depends_on is neither a list nor a mapping"
        graph[str(name)] = deps
    for name, deps in graph.items():
        missing = [d for d in deps if d not in graph]
        if missing:
            return f"{name} depends on {missing[0]}, which the definition lacks"
    return graph


def close_over(
    graph: Mapping[str, list[str]], selected: frozenset[str]
) -> Closure | ClosureRefused:
    """The selection plus everything it depends on; the definition's fingerprint (B3-C13)."""
    for name in sorted(selected):
        if name not in graph:
            return ClosureRefused(UNKNOWN_IDENTIFIER, name[:SUBJECT_MAX])
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


def _invalid(reason: str) -> ClosureRefused:
    return ClosureRefused(COMPOSE_DEFINITION_INVALID, reason[:SUBJECT_MAX])


class RealComposeResolver:
    """`ComposeResolver` over the operator's docker CLI, keyed by project (catalog entry).

    `projects` maps a catalog entry to the ABSOLUTE path of its Compose definition file."""

    def __init__(self, docker: DockerCli, projects: Mapping[CatalogEntry, str | os.PathLike[str]]):
        self.docker = docker
        self.projects: dict[str, str] = {}
        for name, path in projects.items():
            text = os.fspath(path)
            if not os.path.isabs(text):
                raise ValueError(f"the Compose definition of {name} must be an absolute path")
            self.projects[name] = text

    def closure(self, project: CatalogEntry, selected: frozenset[str]) -> Closure | ClosureRefused:
        graph = self._graph(project)
        if isinstance(graph, ClosureRefused):
            return graph
        return close_over(graph, frozenset(selected))

    def _graph(self, project: CatalogEntry) -> dict[str, list[str]] | ClosureRefused:
        definition = self.projects.get(project)
        if definition is None:
            return _invalid(f"no definition for {project}")
        call = self.docker.call(config_args(definition))
        if not call.started:
            return _invalid("the docker executable could not be started")
        if call.interrupted is not None:
            return _invalid(f"the read was ended before it answered ({call.interrupted})")
        if call.exit_status != 0:
            detail = call.output.strip().splitlines()[-1:] or [f"exit status {call.exit_status}"]
            return _invalid(f"compose config refused the definition: {detail[0]}")
        try:
            document: Any = json.loads(call.output)
        except ValueError as exc:
            if len(call.output.encode()) >= TEXT_MAX:
                return _invalid(
                    f"the rendered definition fills the {TEXT_MAX}-byte console excerpt "
                    "and cannot be read whole"
                )
            return _invalid(f"compose config did not print JSON: {exc}")
        graph = dependency_graph(document)
        return _invalid(graph) if isinstance(graph, str) else graph
