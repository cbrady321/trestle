"""Real Compose resolver: `ComposeResolver.closure` over `docker compose config --format json`
(L.NW-2.7; B3-C13, B3-C20, V-11, V-13, MC-25, MC-B-01).

`RealComposeResolver(docker, projects, artifacts)` reads a project's live Compose definition with
ONE read-only invocation, run through the injected `ExecutionPort` like every other docker call
(`DockerCli`: stdin closed, environment built from empty, attributable to the run)::

    <docker> [--host <endpoint>] compose -f <definition file> config --format json --no-interpolate
        --output <artifact file>

`config` renders the definition and starts nothing. The rendered document goes to an artifact file
in the resolver's own artifact directory (`artifacts`, else a fresh temporary directory), never
next to the definition, and the resolver reads it whole from there, the way the pytest runner
reads its JUnit artifact (L.RB-5.1): the port hands an adapter only the console TAIL of a command
(`ExecutionResult.excerpt`, at most `TEXT_MAX` bytes, B3-C14), and a real rendered definition is
longer than that (the decision of 2026-09-30, L.NW-2.7.fix1; no port contract change). The
artifact is removed once read, so the read leaves nothing behind and writes nothing the Compose
definition's envelope holds (B3-C20: `INCIDENTAL_WRITES` is empty). `--no-interpolate` leaves
`${TRESTLE_IMAGE_<ROLE>}` unresolved (the environment of the call is empty and the closure needs
only who depends on whom).

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

A rendered document over `DOCUMENT_MAX` bytes, or an artifact the call did not write, is
`COMPOSE_DEFINITION_INVALID` saying so; the resolver never returns a closure computed from a
truncated or partial document.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from trestle.workflow.ports import CatalogEntry, Closure, ClosureRefused

from trestle_packs.container.engine import COMPOSE_DEFINITION_INVALID, DockerCli

UNKNOWN_IDENTIFIER: Final = "admission.unknown_identifier"
BOUND_EXCEEDED: Final = "admission.bound_exceeded"
IDSET_MAX: Final = 1024  # V-13: an identifier set; Closure.edges is at most 4 x IDSET_MAX
SUBJECT_MAX: Final = 200  # executor-chosen: well inside TEXT_MAX (BoundedText)
DOCUMENT_MAX: Final = 8 * 1024 * 1024  # executor-chosen: the largest rendered definition read


def config_args(definition: str, output: str) -> tuple[str, ...]:
    """The arguments of the one read, after `<docker> [--host <endpoint>]`: the rendered
    definition is written to the artifact file `output`."""
    return (
        "compose",
        "-f",
        definition,
        "config",
        "--format",
        "json",
        "--no-interpolate",
        "--output",
        output,
    )


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

    `projects` maps a catalog entry to the ABSOLUTE path of its Compose definition file;
    `artifacts` is the directory the rendered document is written to and read from (absolute;
    `None`: a fresh temporary directory per read, removed afterwards)."""

    def __init__(
        self,
        docker: DockerCli,
        projects: Mapping[CatalogEntry, str | os.PathLike[str]],
        artifacts: str | os.PathLike[str] | None = None,
    ):
        self.docker = docker
        self.projects: dict[str, str] = {}
        for name, path in projects.items():
            text = os.fspath(path)
            if not os.path.isabs(text):
                raise ValueError(f"the Compose definition of {name} must be an absolute path")
            self.projects[name] = text
        self.artifacts = None if artifacts is None else Path(artifacts)
        if self.artifacts is not None and not self.artifacts.is_absolute():
            raise ValueError("the Compose artifact directory must be an absolute path")

    def closure(self, project: CatalogEntry, selected: frozenset[str]) -> Closure | ClosureRefused:
        graph = self._graph(project)
        if isinstance(graph, ClosureRefused):
            return graph
        return close_over(graph, frozenset(selected))

    def _graph(self, project: CatalogEntry) -> dict[str, list[str]] | ClosureRefused:
        definition = self.projects.get(project)
        if definition is None:
            return _invalid(f"no definition for {project}")
        if self.artifacts is not None:
            self.artifacts.mkdir(parents=True, exist_ok=True)
            directory, owned = self.artifacts, False
        else:
            directory, owned = Path(tempfile.mkdtemp(prefix="trestle-compose-")), True
        output = directory / f"config-{uuid.uuid4().hex}.json"
        try:
            return self._render(definition, output)
        finally:
            output.unlink(missing_ok=True)
            if owned:
                shutil.rmtree(directory, ignore_errors=True)

    def _render(self, definition: str, output: Path) -> dict[str, list[str]] | ClosureRefused:
        call = self.docker.call(config_args(definition, str(output)))
        if not call.started:
            return _invalid("the docker executable could not be started")
        if call.interrupted is not None:
            return _invalid(f"the read was ended before it answered ({call.interrupted})")
        if call.exit_status != 0:
            detail = call.output.strip().splitlines()[-1:] or [f"exit status {call.exit_status}"]
            return _invalid(f"compose config refused the definition: {detail[0]}")
        try:
            size = output.stat().st_size
        except OSError:
            return _invalid("compose config wrote no rendered definition")
        if size > DOCUMENT_MAX:
            return _invalid(f"the rendered definition is over {DOCUMENT_MAX} bytes")
        try:
            document: Any = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _invalid(f"compose config did not write JSON: {exc}")
        graph = dependency_graph(document)
        return _invalid(graph) if isinstance(graph, str) else graph
