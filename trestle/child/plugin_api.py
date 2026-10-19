"""Describe the public plugin surface for `trestle://guide/api` (run by the server in a throwaway
process: the host never imports `trestle.workflow` or plugin-side code).

`python -m trestle.child.plugin_api` prints the index (each public module and its names, one line
each); `--name <module>.<name>` prints one name's signature and docstring. The answer is one JSON
line, `{"ok": true, "value": ...}` or `{"ok": false, "error": "..."}`."""

from __future__ import annotations

import argparse
import enum
import importlib
import inspect
import json
import re
import sys
from types import ModuleType
from typing import Any

import trestle.workflow as _workflow_package

# Every module a plugin (or its tests) may import from: what `trestle://guide/api` describes.
API_MODULES: tuple[str, ...] = (
    "trestle.plugin",
    "trestle.plugin.testing",
    "trestle.workflow",
    *(f"trestle.workflow.{name}" for name in _workflow_package.PUBLIC_MODULES),
)


def public_names(module: ModuleType) -> list[str]:
    """A module's export list: `__all__` when it declares one, else every name it defines whose
    name does not start with `_` (what a plugin importing that public module can name)."""
    declared = getattr(module, "__all__", None)
    if declared is not None:
        return list(declared)
    return [
        name
        for name, value in vars(module).items()
        if not name.startswith("_") and getattr(value, "__module__", None) == module.__name__
    ]


def api_index() -> list[dict[str, Any]]:
    """Each public module and its names, one line each; a re-export is listed by name under
    `reexports`, keyed by the module that defines (and describes) it."""
    modules = []
    for module_name in API_MODULES:
        module = importlib.import_module(module_name)
        names: dict[str, str] = {}
        reexports: dict[str, list[str]] = {}
        for name in public_names(module):
            value = getattr(module, name)
            home = _home(value, module_name)
            if home != module_name:
                # a re-export (`trestle.workflow.Acted`): listed where it is defined
                reexports.setdefault(home, []).append(name)
            else:
                names[name] = f"{_kind(value)}: {_summary(value) or ''}".rstrip(": ")
        entry: dict[str, Any] = {"module": module_name, "names": names}
        if reexports:
            entry["reexports"] = reexports
        modules.append(entry)
    return modules


def api_names() -> tuple[str, ...]:
    """Every `{module}.{name}` that `trestle://guide/api/{name}` describes."""
    return tuple(
        f"{module_name}.{name}"
        for module_name in API_MODULES
        for name in public_names(importlib.import_module(module_name))
    )


def plugin_api_name(qualified: str) -> dict[str, Any]:
    """One name (`trestle.plugin.Context`): its signature and docstring, members for a class.
    A re-export is described where it is defined. `KeyError` for a name that is not public."""
    module_name, _, name = qualified.rpartition(".")
    if module_name not in API_MODULES:
        raise KeyError(f"{qualified!r} is not in a public module: {', '.join(API_MODULES)}")
    module = importlib.import_module(module_name)
    if name not in public_names(module):
        raise KeyError(f"{name!r} is not a public name of {module_name}")
    value = getattr(module, name)
    home = _home(value, module_name)
    return {"name": f"{home}.{name}", "module": home, **_describe(value)}


def _home(value: Any, module_name: str) -> str:
    home = getattr(value, "__module__", None)
    return home if isinstance(home, str) and home in API_MODULES else module_name


def _kind(value: Any) -> str:
    if inspect.isclass(value):
        return "enum" if issubclass(value, enum.Enum) else "class"
    return "function" if callable(value) else "constant"


SUMMARY_CHARS = 100


def _summary(obj: Any) -> str | None:
    """The docstring's first sentence, on one line and at most `SUMMARY_CHARS` long."""
    doc = _own_doc(obj) if inspect.isclass(obj) else _doc(obj)
    if not doc:
        return None
    first = " ".join(doc.split("\n\n", 1)[0].split())
    sentence = re.split(r"(?<=[.:;])\s", first, maxsplit=1)[0]
    return sentence if len(sentence) <= SUMMARY_CHARS else sentence[: SUMMARY_CHARS - 1] + "…"


def _doc(obj: object) -> str | None:
    return inspect.getdoc(obj) or None


def _signature(obj: Any) -> str | None:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return None


def _describe(value: Any) -> dict[str, Any]:
    if inspect.isclass(value):
        return _describe_class(value)
    if callable(value):
        return {"kind": "function", "signature": _signature(value), "doc": _doc(value)}
    return {"kind": "constant", "value": repr(value)[:200]}


def _describe_class(cls: type) -> dict[str, Any]:
    entry: dict[str, Any] = {"kind": "class", "doc": _own_doc(cls)}
    if issubclass(cls, enum.Enum):
        entry["kind"] = "enum"
        entry["members"] = {member.name: repr(member.value) for member in cls}
        return entry
    if not getattr(cls, "_is_protocol", False):  # a Protocol's own signature says nothing
        entry["signature"] = _signature(cls)
    members = []
    for attr, raw in vars(cls).items():
        if attr.startswith("_"):
            continue
        if isinstance(raw, property):
            members.append({"name": attr, "kind": "property", "doc": _doc(raw)})
        elif isinstance(raw, staticmethod | classmethod) or inspect.isfunction(raw):
            fn = raw.__func__ if isinstance(raw, staticmethod | classmethod) else raw
            members.append(
                {"name": attr, "kind": "method", "signature": _signature(fn), "doc": _doc(fn)}
            )
    annotations = {
        attr: hint
        for attr, hint in inspect.get_annotations(cls).items()
        if not attr.startswith("_")
    }
    if annotations:
        entry["fields"] = {attr: _hint(hint) for attr, hint in annotations.items()}
    if members:
        entry["members"] = members
    return entry


def _own_doc(cls: type) -> str | None:
    """The class's own docstring, not one inherited from a base (or the dataclass default)."""
    doc = cls.__dict__.get("__doc__")
    if not isinstance(doc, str) or doc.startswith(f"{cls.__name__}("):
        return None
    return inspect.cleandoc(doc) or None


def _hint(hint: object) -> str:
    return hint if isinstance(hint, str) else getattr(hint, "__name__", repr(hint))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-plugin-api")
    parser.add_argument("--name")
    args = parser.parse_args(argv)
    try:
        value: Any = plugin_api_name(args.name) if args.name else api_index()
    except KeyError as exc:
        print(json.dumps({"ok": False, "error": str(exc.args[0])}))
        return 0
    print(json.dumps({"ok": True, "value": value}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
