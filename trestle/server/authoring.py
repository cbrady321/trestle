"""The authoring resources: how to write plugins and packs, served by the running build.

All of it lives under one parent, `trestle://guide` (`trestle/plugin/_guide/root.md`), which
lists its children: the view catalog at `trestle://guide/views` and the authoring resources. They
come in layers an agent reads on demand, so no read floods it with authoring context:

- `trestle://guide/plugins`, a short index (`trestle/plugin/_guide/index.md`): what a plugin and a
  pack are, the rules that matter most, and one line per topic saying when to read it;
- `trestle://guide/plugins/{topic}`, one small self-contained topic (`_guide/topics/<topic>.md`);
- `trestle://guide/api`, every public name with a one-line summary, read with `inspect` at
  request time, and `trestle://guide/api/{name}`, one name's signature and docstring;
- `trestle://guide/examples/{name}`, one complete example plugin.

Agents learn the surface from these, never from Trestle's source or its home (decision record
service-and-credentials, Decision 3)."""

from __future__ import annotations

import enum
import importlib
import inspect
import re
from importlib import resources
from types import ModuleType
from typing import Any

import trestle.workflow as _workflow_package

GUIDE_URI = "trestle://guide"
# the view catalog, also served at its first URI (`trestle://views`), which the query and fetch
# tool descriptions name and golden S0 pins
VIEWS_GUIDE_URI = f"{GUIDE_URI}/views"
PLUGIN_GUIDE_URI = f"{GUIDE_URI}/plugins"
PLUGIN_GUIDE_TOPIC_TEMPLATE = f"{PLUGIN_GUIDE_URI}/{{topic}}"
PLUGIN_API_URI = f"{GUIDE_URI}/api"
PLUGIN_API_NAME_TEMPLATE = f"{PLUGIN_API_URI}/{{name}}"
PLUGIN_EXAMPLES_PREFIX = f"{GUIDE_URI}/examples/"
PLUGIN_EXAMPLES_TEMPLATE = f"{PLUGIN_EXAMPLES_PREFIX}{{name}}"

# Every module a plugin (or its tests) may import from: what `trestle://guide/api` describes.
API_MODULES: tuple[str, ...] = (
    "trestle.plugin",
    "trestle.plugin.testing",
    "trestle.workflow",
    *(f"trestle.workflow.{name}" for name in _workflow_package.PUBLIC_MODULES),
)

INDEX_RESTRICTED_NOTE = (
    "> Restricted profile: runtime publishing is unavailable here. Give a finished plugin file "
    "to the operator.\n\n"
)
RESTRICTED_NOTE = (
    "> **This server runs the restricted profile: runtime publishing is unavailable here.** "
    "`publish_plugin` is not offered, and only plugins the operator allowlists run. Write and "
    "test a plugin, then give the file to the operator to place in a watched folder.\n\n"
)

_GUIDE = resources.files("trestle.plugin._guide")


def server_instructions(restricted: bool) -> str:
    """What the MCP `initialize` answer tells every agent: where to start, and that Trestle is
    extended by plugins, never by reading or editing it."""
    publishing = (
        "Runtime publishing is unavailable on this server (restricted profile). "
        if restricted
        else ""
    )
    return (
        f"Guidance is under {GUIDE_URI}: start there and read only what you need. "
        f"{publishing}Extend Trestle by writing plugins and packs; do not read its source or "
        "home directory, or modify it."
    )


def _stems(folder: str, suffix: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            entry.name.removesuffix(suffix)
            for entry in (_GUIDE / folder).iterdir()
            if entry.name.endswith(suffix) and not entry.name.startswith("_")
        )
    )


def topic_names() -> tuple[str, ...]:
    return _stems("topics", ".md")


def topic_uri(topic: str) -> str:
    return f"{PLUGIN_GUIDE_URI}/{topic}"


def example_names() -> tuple[str, ...]:
    """The example plugins, by name (each file's stem, which is also its entry point's name)."""
    return _stems("examples", ".py")


def example_uris() -> tuple[str, ...]:
    return tuple(f"{PLUGIN_EXAMPLES_PREFIX}{name}" for name in example_names())


def plugin_example(name: str) -> str:
    """The source of example `name`; `KeyError` for a name that is not one."""
    if name not in example_names():
        raise KeyError(f"no example {name!r}; examples: {', '.join(example_names())}")
    return (_GUIDE / "examples" / f"{name}.py").read_text(encoding="utf-8")


def guide_root() -> str:
    """The parent of every guide resource: one line per child."""
    text = (_GUIDE / "root.md").read_text(encoding="utf-8")
    return text.replace("{examples}", ", ".join(example_names()))


def plugin_guide(*, restricted: bool = False) -> str:
    """The index, naming the examples this build ships; under the restricted profile it opens
    with the note that runtime publishing is unavailable."""
    text = (_GUIDE / "index.md").read_text(encoding="utf-8")
    text = text.replace("{examples}", ", ".join(example_names()))
    return _with_note(text, INDEX_RESTRICTED_NOTE) if restricted else text


def guide_topic(topic: str, *, restricted: bool = False) -> str:
    """One topic; `KeyError` for a topic that is not one. Under the restricted profile the
    publishing topic opens with the restricted note."""
    if topic not in topic_names():
        raise KeyError(f"no topic {topic!r}; topics: {', '.join(topic_names())}")
    text = (_GUIDE / "topics" / f"{topic}.md").read_text(encoding="utf-8")
    return _with_note(text, RESTRICTED_NOTE) if restricted and topic == "publishing" else text


def _with_note(text: str, note: str) -> str:
    title, _, rest = text.partition("\n\n")
    return f"{title}\n\n{note}{rest}"


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


def plugin_api(*, version: str, restricted: bool = False) -> dict[str, Any]:
    """The index of the plugin surface: each public module and its names, one line each. A
    name's signature and docstring are at `trestle://guide/api/{module}.{name}`."""
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
    return {
        "server_version": version,
        "runtime_publishing": not restricted,
        "detail": PLUGIN_API_NAME_TEMPLATE.replace("{name}", "{module}.{name}"),
        "modules": modules,
    }


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
