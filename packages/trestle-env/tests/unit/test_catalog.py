"""The trusted catalog as data: closed identifier types, duplicates refused at load (L.NW-3.1)."""

from __future__ import annotations

import copy
import dataclasses
import json
import typing
from pathlib import Path
from typing import Any

import pytest
from trestle.common.plan.bounds import NAME_MAX

from trestle_env.catalog import (
    CLOSED_TYPES,
    REFERENCE_PATH,
    Catalog,
    CatalogError,
    OverrideId,
    ProjectId,
    ServiceId,
    TaskEntry,
    ToolName,
    load_reference,
    model,
)

GOOD: dict[str, Any] = {
    "schema": 1,
    "env_key": "env",
    "services": [
        {"id": "postgres", "selector": "postgres"},
        {"id": "redis", "selector": "redis"},
    ],
    "projects": [
        {
            "id": "demo-py",
            "pin": {"python": "3.12"},
            "tasks": [
                {"id": "pytest", "argv": ["pytest", "-q", "-k", "not slow"]},
                {"id": "serve", "argv": ["python", "app.py"]},
            ],
        }
    ],
    "tests": [{"id": "demo-unit", "project": "demo-py", "task": "pytest"}],
    "overrides": [{"id": "redis-local", "service": "redis", "project": "demo-py", "task": "serve"}],
}


def write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def variant(mutate: Any) -> dict[str, Any]:
    data = copy.deepcopy(GOOD)
    mutate(data)
    return data


def refused(tmp_path: Path, data: object) -> CatalogError:
    with pytest.raises(CatalogError) as caught:
        Catalog.load(write(tmp_path, data))
    return caught.value


def test_good_catalog_loads_and_answers_lookups(tmp_path: Path) -> None:
    catalog = Catalog.load(write(tmp_path, GOOD))
    assert [str(s.id) for s in catalog.services] == ["postgres", "redis"]
    assert catalog.service("redis") is not None and catalog.service("nope") is None
    assert catalog.project("demo-py") is not None and catalog.test("demo-unit") is not None
    assert catalog.override("redis-local") is not None
    task = catalog.task("demo-py", "pytest")
    assert task is not None and task.argv == ("pytest", "-q", "-k", "not slow")
    assert str(catalog.env_key) == "env"
    assert Catalog.loads(json.dumps(GOOD)) == catalog


def test_reference_catalog_ships_and_loads() -> None:
    catalog = load_reference()
    assert REFERENCE_PATH.name == "reference.json"
    assert [str(s.id) for s in catalog.services] == ["postgres", "http_support"]


@pytest.mark.proves("WR-ENV-1", "WR-ENV-1:catalog-refuses-duplicate-ids", "B", "B", "LOGIC", "CI")
@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        (lambda d: d["services"].append({"id": "postgres", "selector": "other"}), "postgres"),
        (lambda d: d["projects"].append(copy.deepcopy(d["projects"][0])), "demo-py"),
        (lambda d: d["tests"].append(copy.deepcopy(d["tests"][0])), "demo-unit"),
        (lambda d: d["overrides"].append(copy.deepcopy(d["overrides"][0])), "redis-local"),
        (lambda d: d["projects"][0]["tasks"].append({"id": "serve", "argv": ["x"]}), "serve"),
    ],
    ids=["service", "project", "test", "override", "task-in-project"],
)
def test_duplicate_identifier_refused_at_load(tmp_path: Path, mutate: Any, named: str) -> None:
    error = refused(tmp_path, variant(mutate))
    assert error.reason == "duplicate"
    assert error.identifier == named
    assert repr(named) in str(error)


def test_duplicate_json_key_and_duplicate_pin_are_refused(tmp_path: Path) -> None:
    text = json.dumps(GOOD).replace('"env_key": "env"', '"env_key": "env", "env_key": "other"')
    error = refused(tmp_path, text)
    assert (error.reason, error.identifier) == ("duplicate", "env_key")
    twice = '{"id": "p", "pin": {"python": "3.12", "python": "3.11"}, "tasks": []}'
    text = json.dumps({**GOOD, "projects": []}).replace('"projects": []', f'"projects": [{twice}]')
    assert refused(tmp_path, text).identifier == "python"


def closed_leaf_types(hint: object) -> set[object]:
    origin = typing.get_origin(hint)
    if origin is tuple:
        return {
            leaf
            for arg in typing.get_args(hint)
            if arg is not Ellipsis
            for leaf in closed_leaf_types(arg)
        }
    return {hint}


def model_dataclasses() -> list[type]:
    return [
        obj
        for obj in vars(model).values()
        if isinstance(obj, type)
        and dataclasses.is_dataclass(obj)
        and obj.__module__ == model.__name__
    ]


def test_identifiers_are_closed_types(tmp_path: Path) -> None:
    assert model.ID_MAX == NAME_MAX  # V-13: a logical name

    # No field of any model class is a bare `str`: each is a closed identifier type, a tuple of
    # them, or (the Catalog itself and its parts) another model class.
    dataclasses_ = model_dataclasses()
    assert {cls.__name__ for cls in dataclasses_} >= {"Catalog", "Service", "Project", "TaskEntry"}
    for cls in dataclasses_:
        hints = typing.get_type_hints(cls)
        for f in dataclasses.fields(cls):
            if not f.init:
                continue
            for leaf in closed_leaf_types(hints[f.name]):
                assert leaf in CLOSED_TYPES or leaf in dataclasses_, (cls.__name__, f.name, leaf)
                assert leaf is not str, (cls.__name__, f.name)

    # A value of one identifier type is not another's, and an invalid value is refused by name.
    assert not isinstance(ServiceId("a"), ProjectId) and not isinstance(ServiceId("a"), OverrideId)
    for bad in ("", "A", "has space", "../etc", "a;b", "$(x)", "x" * (model.ID_MAX + 1), "-lead"):
        for closed in (ServiceId, ProjectId, OverrideId, model.TestId, model.TaskId):
            with pytest.raises(CatalogError):
                closed(bad)
    for not_a_string in (5, None, True, ["a"], {"id": "a"}):
        with pytest.raises(CatalogError):
            ServiceId(not_a_string)
    error = refused(
        tmp_path, variant(lambda d: d["services"].__setitem__(0, {"id": "Bad Id", "selector": "x"}))
    )
    assert error.reason == "invalid" and error.identifier == "Bad Id"

    # A reference to an identifier that is not in the catalog is refused, naming it.
    for mutate, named in (
        (lambda d: d["tests"][0].update(project="ghost"), "ghost"),
        (lambda d: d["tests"][0].update(task="ghost"), "demo-py/ghost"),
        (lambda d: d["overrides"][0].update(service="ghost"), "ghost"),
        (lambda d: d["overrides"][0].update(project="ghost"), "ghost"),
    ):
        error = refused(tmp_path, variant(mutate))
        assert (error.reason, error.identifier) == ("unknown_reference", named)


def test_catalog_holds_no_free_form_executable(tmp_path: Path) -> None:
    # Structure: an argv exists only on a `TaskEntry`; no other model class has a field that could
    # hold a command, an executable path or a script.
    for cls in model_dataclasses():
        names = {f.name for f in dataclasses.fields(cls)}
        forbidden = names & {"argv", "command", "cmd", "executable", "script", "shell", "exec"}
        assert forbidden == (set() if cls is not TaskEntry else {"argv"}), cls.__name__

    # An unknown key is refused wherever it appears; a command cannot ride on any entry.
    for mutate in (
        lambda d: d.update(command="rm -rf /"),
        lambda d: d["services"][0].update(command="sh -c x"),
        lambda d: d["projects"][0].update(argv=["sh"]),
        lambda d: d["tests"][0].update(argv=["pytest"]),
        lambda d: d["overrides"][0].update(executable="/bin/sh"),
        lambda d: d["projects"][0]["tasks"][0].update(shell=True),
    ):
        assert refused(tmp_path, variant(mutate)).reason == "unknown_key"

    # A test or override names a task by id: an inline argv in place of the id is refused.
    inline = variant(lambda d: d["tests"][0].pop("task"))
    inline["tests"][0]["argv"] = ["pytest"]
    assert refused(tmp_path, inline).reason in ("unknown_key", "missing_key")

    # Inside a task entry: argv[0] is a bare tool name (never a path or a command line), the rest
    # literals with no shell metacharacter, and the argv is bounded.
    def task(argv: object) -> dict[str, Any]:
        return variant(lambda d: d["projects"][0]["tasks"][0].update(argv=argv))

    for argv in (
        ["/bin/sh", "-c", "x"],
        ["./run"],
        ["../tool"],
        ["sh -c x"],
        ["python", "a;b"],
        ["python", "$(id)"],
        ["python", "`id`"],
        ["python", "a|b"],
        ["python", "a\nb"],
        ["python", 3],
        [],
        ["python"] + ["x"] * model.ARGV_MAX,
        "pytest -q",
    ):
        assert refused(tmp_path, task(argv)).reason in ("invalid",), argv
    with pytest.raises(CatalogError):
        TaskEntry(model.TaskId("t"), (model.Arg("/bin/sh"),))
    assert ToolName("pytest") == "pytest"
    assert Catalog.load(write(tmp_path, task(["python", "-m", "pytest", "tests/unit"]))).task(
        "demo-py", "pytest"
    )


def test_malformed_catalogs_are_refused(tmp_path: Path) -> None:
    assert refused(tmp_path, "not json").reason == "invalid"
    assert refused(tmp_path, "[]").reason == "invalid"
    assert refused(tmp_path, variant(lambda d: d.update(schema=2))).reason == "invalid"
    assert refused(tmp_path, variant(lambda d: d.update(schema=True))).reason == "invalid"
    assert refused(tmp_path, variant(lambda d: d.pop("services"))).reason == "missing_key"
    assert refused(tmp_path, variant(lambda d: d.update(services={}))).reason == "invalid"
    assert (
        refused(tmp_path, variant(lambda d: d["projects"][0].update(pin=["python"]))).reason
        == "invalid"
    )
