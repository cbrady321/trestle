"""The trusted catalog as data (MC-B-09; L.NW-3.1).

A `Catalog` holds the services, projects (a toolchain pin plus allowlisted task argv entries),
tests, selectors and local overrides a request may name. Everything is closed:

* every identifier is a distinct validated string type (`ServiceId`, `ProjectId`, `TestId`,
  `OverrideId`, `TaskId`, `SelectorId`, `EnvKey`), so a request field bound to one cannot carry
  another's value and no field is a bare `str`;
* the only place an argv exists is a project's `TaskEntry`, whose `argv[0]` is a bare tool name
  (never a path) and whose remaining entries are literals; a test or an override names a task by
  id, never an argv, and an unknown key in the file is refused, so no free-form executable is
  representable (WR-AUTH-3's catalog half);
* a `Service` may declare `depends_on`, the catalog services it needs started first (the user's own
  Compose `depends_on`, as data the plan is compiled from: a `services` selection admits its
  dependency closure, C-3); a dependency that is not a catalog service, on itself, or in a cycle is
  refused;
* a `RemediationPair` declares, as data, which stable code the loop may answer with which effect,
  and the budget of that repair (attempts, total time, cooldown; V-14): the pair is the catalog's,
  the bound and the recording are the runtime's;
* `Catalog.load` refuses a duplicate identifier (of one type, or a duplicate JSON key), an unknown
  key, a malformed value and a cross-reference to an identifier that is not in the catalog, naming
  the identifier (`CatalogError`), before anything is built.

Stdlib only (root C.5 step 4). `ID_MAX` is V-13's `NAME_MAX` (a logical name); a drift test pins it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Self

ID_MAX = 128  # = trestle.common.plan.bounds.NAME_MAX (V-13: a logical name)
PIN_VERSION_MAX = 64
ARG_MAX = 256  # bytes of one task argument
ARGV_MAX = 32  # entries of one task argv, tool included
ATTEMPTS_MAX = 16  # attempts one remediation pair may declare
BUDGET_SECONDS_MAX = 3600.0  # total time or cooldown one remediation pair may declare
SCHEMA_VERSION = 1


class CatalogError(ValueError):
    """A catalog refused at load. `reason` is one of `duplicate`, `unknown_reference`,
    `unknown_key`, `missing_key`, `invalid`; `identifier` names the offending identifier or
    value."""

    def __init__(self, reason: str, identifier: str, detail: str = "") -> None:
        self.reason = reason
        self.identifier = identifier
        self.detail = detail
        text = f"catalog refused: {reason} {identifier!r}"
        super().__init__(f"{text}: {detail}" if detail else text)


class Closed(str):
    """A validated string of one closed type: subclasses set `KIND` and `PATTERN`."""

    KIND: ClassVar[str] = "value"
    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_.-]*")
    MAX: ClassVar[int] = ID_MAX

    def __new__(cls, value: object) -> Self:
        if cls is Closed:
            raise TypeError("Closed is abstract")
        if type(value) is not str and not isinstance(value, Closed):
            raise CatalogError("invalid", repr(value), f"a {cls.KIND} is a string")
        text = str(value)
        if len(text) > cls.MAX or not cls.PATTERN.fullmatch(text):
            raise CatalogError("invalid", text[:ID_MAX], f"not a valid {cls.KIND}")
        return super().__new__(cls, text)


class ServiceId(Closed):
    KIND = "service id"


class ProjectId(Closed):
    KIND = "project id"


class TestId(Closed):
    __test__ = False  # not a pytest class
    KIND = "test id"


class OverrideId(Closed):
    KIND = "override id"


class TaskId(Closed):
    KIND = "task id"


class SelectorId(Closed):
    """Names the run-scoped instance of a service (B3-C1 `selector_ref`), never a command."""

    KIND = "selector id"


class EnvKey(Closed):
    """The request field that carries the Compose project (the opaque environment key)."""

    KIND = "environment key name"


class ToolName(Closed):
    """`argv[0]` of a task: a bare tool name, resolved by the toolchain (B3-C14), never a path."""

    KIND = "tool name"
    PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*")


class PinVersion(Closed):
    KIND = "toolchain version"
    PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.+-]*")
    MAX = PIN_VERSION_MAX


class StableCodeName(Closed):
    """A V-11 stable code, spelled `<origin>.<snake>` (the code a remediation answers)."""

    KIND = "stable code"
    PATTERN = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")


class EffectName(Closed):
    """The declared effect a remediation runs (a leaf's effect id)."""

    KIND = "effect id"
    PATTERN = re.compile(r"[a-z][a-z0-9_]*")


class Arg(Closed):
    """One literal task argument: printable, no control character, not a shell fragment."""

    KIND = "task argument"
    PATTERN = re.compile(r"[^\x00-\x1f\x7f`$;&|<>\\]+")
    MAX = ARG_MAX


CLOSED_TYPES: tuple[type[Closed], ...] = (
    ServiceId,
    ProjectId,
    TestId,
    OverrideId,
    TaskId,
    SelectorId,
    EnvKey,
    ToolName,
    PinVersion,
    StableCodeName,
    EffectName,
    Arg,
)


@dataclass(frozen=True)
class Service:
    id: ServiceId
    selector: SelectorId
    depends_on: tuple[ServiceId, ...] = ()  # started before this one (Compose `depends_on`)


@dataclass(frozen=True)
class ToolPin:
    tool: ToolName
    version: PinVersion


@dataclass(frozen=True)
class TaskEntry:
    """An allowlisted task: the one place an argv exists in the catalog."""

    id: TaskId
    argv: tuple[Arg, ...]  # argv[0] is a `ToolName` (checked at construction), the rest literals
    reports_tests: bool = False  # a test selector: its result is read from a JUnit report

    def __post_init__(self) -> None:
        if not self.argv or len(self.argv) > ARGV_MAX:
            raise CatalogError("invalid", str(self.id), "a task has 1..ARGV_MAX argv entries")
        ToolName(self.argv[0])  # a bare tool name, never a path


@dataclass(frozen=True)
class Project:
    id: ProjectId
    pin: tuple[ToolPin, ...]
    tasks: tuple[TaskEntry, ...]


@dataclass(frozen=True)
class TestSpec:
    __test__ = False  # not a pytest class
    id: TestId
    project: ProjectId
    task: TaskId
    provision: bool = False  # the test needs the provisioned fixture record before it runs


@dataclass(frozen=True)
class Override:
    """A local realization of a service: a project's task launches it in place of the container."""

    id: OverrideId
    service: ServiceId
    project: ProjectId
    task: TaskId


@dataclass(frozen=True)
class RemediationPair:
    """A declared repair: on `code` the loop may run `effect`, at most `attempts` times within
    `total_s` seconds, `cooldown_s` seconds apart (V-14 `RemedyDeclaration`, as data)."""

    code: StableCodeName
    effect: EffectName
    attempts: int
    total_s: float
    cooldown_s: float

    def __post_init__(self) -> None:
        bad = (
            type(self.attempts) is not int
            or not 1 <= self.attempts <= ATTEMPTS_MAX
            or not 0 < self.total_s <= BUDGET_SECONDS_MAX
            or not 0 <= self.cooldown_s <= self.total_s
        )
        if bad:
            raise CatalogError("invalid", str(self.code), "a remediation budget is out of bounds")


_TOP = ("schema", "env_key", "services", "projects", "tests", "overrides", "remediation")
_REMEDIATION = ("code", "effect", "attempts", "total_s", "cooldown_s")
_SERVICE = ("id", "selector", "depends_on")
_SERVICE_OPTIONAL = ("depends_on",)
_PROJECT = ("id", "pin", "tasks")
_TASK = ("id", "argv", "reports_tests")
_TASK_OPTIONAL = ("reports_tests",)
_TEST = ("id", "project", "task", "provision")
_TEST_OPTIONAL = ("provision",)
_OVERRIDE = ("id", "service", "project", "task")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise CatalogError("duplicate", key, "a key repeated in one object")
        seen[key] = value
    return seen


def _keys(obj: object, allowed: tuple[str, ...], where: str, optional: tuple[str, ...] = ()) -> Any:
    if not isinstance(obj, dict):
        raise CatalogError("invalid", where, "expected an object")
    for key in obj:
        if key not in allowed:
            raise CatalogError("unknown_key", str(key)[:ID_MAX], f"not a field of {where}")
    for key in allowed:
        if key not in obj and key not in optional:
            raise CatalogError("missing_key", key, f"required in {where}")
    return obj


def _flag(test: Mapping[str, Any]) -> bool:
    flag = test.get("provision", False)
    if not isinstance(flag, bool):
        raise CatalogError("invalid", str(test["id"])[:ID_MAX], "provision is a boolean")
    return flag


def _items(obj: object, where: str) -> list[Any]:
    if not isinstance(obj, list):
        raise CatalogError("invalid", where, "expected a list")
    return obj


def _unique[T: Closed](ids: list[T]) -> None:
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            raise CatalogError("duplicate", str(identifier), f"a {type(identifier).KIND} twice")
        seen.add(str(identifier))


@dataclass(frozen=True)
class Catalog:
    services: tuple[Service, ...]
    projects: tuple[Project, ...]
    tests: tuple[TestSpec, ...]
    overrides: tuple[Override, ...]
    env_key: EnvKey
    remediation: tuple[RemediationPair, ...] = ()
    _index: dict[str, dict[str, Any]] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        _unique([s.id for s in self.services])
        _unique([p.id for p in self.projects])
        _unique([t.id for t in self.tests])
        _unique([o.id for o in self.overrides])
        _unique([r.code for r in self.remediation])  # one repair per code
        for project in self.projects:
            _unique([t.id for t in project.tasks])
            for pin in project.pin:
                if sum(p.tool == pin.tool for p in project.pin) > 1:
                    raise CatalogError("duplicate", str(pin.tool), f"pinned twice in {project.id}")
        index: dict[str, dict[str, Any]] = {
            "service": {str(s.id): s for s in self.services},
            "project": {str(p.id): p for p in self.projects},
            "test": {str(t.id): t for t in self.tests},
            "override": {str(o.id): o for o in self.overrides},
        }
        object.__setattr__(self, "_index", index)
        self._check_dependencies()
        for test in self.tests:
            self._check_task(test.project, test.task, str(test.id))
        for override in self.overrides:
            if str(override.service) not in index["service"]:
                raise CatalogError("unknown_reference", str(override.service), str(override.id))
            self._check_task(override.project, override.task, str(override.id))

    def _check_dependencies(self) -> None:
        """Every `depends_on` names another catalog service, and the dependencies are acyclic."""
        known = self._index["service"]
        for service in self.services:
            _unique(list(service.depends_on))
            for dependency in service.depends_on:
                if str(dependency) not in known:
                    raise CatalogError("unknown_reference", str(dependency), str(service.id))
                if dependency == service.id:
                    raise CatalogError("invalid", str(service.id), "a service depends on itself")
        done: set[str] = set()

        def visit(name: str, path: tuple[str, ...]) -> None:
            if name in path:
                raise CatalogError("invalid", name, f"a dependency cycle: {' -> '.join(path)}")
            if name in done:
                return
            for dependency in known[name].depends_on:
                visit(str(dependency), (*path, name))
            done.add(name)

        for service in self.services:
            visit(str(service.id), ())

    def dependency_closure(self, selected: Iterable[str]) -> frozenset[str]:
        """`selected` and every service it depends on, transitively."""
        chosen: set[str] = set()
        work = [str(s) for s in selected]
        while work:
            name = work.pop()
            if name in chosen:
                continue
            chosen.add(name)
            found = self.service(name)
            work.extend(str(d) for d in (found.depends_on if found else ()))
        return frozenset(chosen)

    def _check_task(self, project: ProjectId, task: TaskId, owner: str) -> None:
        found = self._index["project"].get(str(project))
        if found is None:
            raise CatalogError("unknown_reference", str(project), owner)
        if all(t.id != task for t in found.tasks):
            raise CatalogError("unknown_reference", f"{project}/{task}", owner)

    # -- lookups (None when the identifier is not in the catalog)

    def service(self, identifier: str) -> Service | None:
        return self._index["service"].get(identifier)

    def project(self, identifier: str) -> Project | None:
        return self._index["project"].get(identifier)

    def test(self, identifier: str) -> TestSpec | None:
        return self._index["test"].get(identifier)

    def override(self, identifier: str) -> Override | None:
        return self._index["override"].get(identifier)

    def remedy(self, code: str) -> RemediationPair | None:
        return next((r for r in self.remediation if r.code == code), None)

    def task(self, project: str, task: str) -> TaskEntry | None:
        found = self.project(project)
        return next((t for t in found.tasks if t.id == task), None) if found else None

    # -- loading

    @classmethod
    def load(cls, path: str | Path) -> Catalog:
        """Read `path` (UTF-8 JSON) and build the catalog, or raise `CatalogError`."""
        return cls.loads(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def loads(cls, text: str) -> Catalog:
        try:
            data = json.loads(text, object_pairs_hook=_pairs)
        except json.JSONDecodeError as error:
            raise CatalogError("invalid", "catalog", f"not JSON: {error.msg}") from error
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: object) -> Catalog:
        top = _keys(
            data, _TOP, "the catalog", optional=("tests", "overrides", "projects", "remediation")
        )
        if top["schema"] != SCHEMA_VERSION or isinstance(top["schema"], bool):
            raise CatalogError("invalid", str(top["schema"])[:ID_MAX], "unsupported schema version")
        services = tuple(
            Service(
                ServiceId(o["id"]),
                SelectorId(o["selector"]),
                tuple(ServiceId(d) for d in _items(o.get("depends_on", []), "depends_on")),
            )
            for o in (
                _keys(o, _SERVICE, "a service", optional=_SERVICE_OPTIONAL)
                for o in _items(top["services"], "services")
            )
        )
        projects = tuple(
            _project(_keys(o, _PROJECT, "a project"))
            for o in _items(top.get("projects", []), "projects")
        )
        tests = tuple(
            TestSpec(TestId(o["id"]), ProjectId(o["project"]), TaskId(o["task"]), _flag(o))
            for o in (
                _keys(o, _TEST, "a test", optional=_TEST_OPTIONAL)
                for o in _items(top.get("tests", []), "tests")
            )
        )
        overrides = tuple(
            Override(
                OverrideId(o["id"]),
                ServiceId(o["service"]),
                ProjectId(o["project"]),
                TaskId(o["task"]),
            )
            for o in (
                _keys(o, _OVERRIDE, "an override")
                for o in _items(top.get("overrides", []), "overrides")
            )
        )
        remediation = tuple(_remedy(o) for o in _items(top.get("remediation", []), "remediation"))
        return cls(services, projects, tests, overrides, EnvKey(top["env_key"]), remediation)


def _project(obj: Mapping[str, Any]) -> Project:
    pin_obj = obj["pin"]
    if not isinstance(pin_obj, dict):
        raise CatalogError(
            "invalid", str(obj["id"])[:ID_MAX], "a pin is an object of tool: version"
        )
    pin = tuple(ToolPin(ToolName(tool), PinVersion(version)) for tool, version in pin_obj.items())
    tasks = []
    for task in _items(obj["tasks"], "tasks"):
        entry = _keys(task, _TASK, "a task", optional=_TASK_OPTIONAL)
        argv = _items(entry["argv"], "argv")
        reports = entry.get("reports_tests", False)
        if not isinstance(reports, bool):
            raise CatalogError("invalid", str(entry["id"])[:ID_MAX], "reports_tests is a boolean")
        tasks.append(TaskEntry(TaskId(entry["id"]), tuple(Arg(a) for a in argv), reports))
    return Project(ProjectId(obj["id"]), pin, tuple(tasks))


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogError("invalid", where, "expected a number")
    return float(value)


def _remedy(obj: object) -> RemediationPair:
    entry = _keys(obj, _REMEDIATION, "a remediation pair")
    attempts = entry["attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise CatalogError("invalid", str(entry["code"])[:ID_MAX], "attempts is an integer")
    return RemediationPair(
        StableCodeName(entry["code"]),
        EffectName(entry["effect"]),
        attempts,
        _number(entry["total_s"], "total_s"),
        _number(entry["cooldown_s"], "cooldown_s"),
    )
