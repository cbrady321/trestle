"""The authoring resources: how to write plugins and packs, served by the running build.

All of it lives under one parent, `trestle://guide` (`trestle/plugin/_guide/root.md`), which
lists its children: the view catalog at `trestle://guide/views` and the authoring resources. They
come in layers an agent reads on demand, so no read floods it with authoring context:

- `trestle://guide/plugins`, a short index (`trestle/plugin/_guide/index.md`): what a plugin and a
  pack are, the rules that matter most, and one line per topic saying when to read it;
- `trestle://guide/plugins/{topic}`, one small self-contained topic (`_guide/topics/<topic>.md`);
- `trestle://guide/api`, every public name with a one-line summary, and
  `trestle://guide/api/{name}`, one name's signature and docstring, both read with `inspect` at
  request time by `trestle.child.plugin_api` in a throwaway interpreter (the host never imports
  the workflow package);
- `trestle://guide/examples/{name}`, one complete example plugin.

Agents learn the surface from these, never from Trestle's source or its home (decision record
service-and-credentials, Decision 3)."""

from __future__ import annotations

import json
import subprocess
from importlib import resources
from typing import Any

from trestle.common.pyenv import build_child_env, python_argv

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

PLUGIN_API_TIMEOUT_S = 10.0

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
        "home directory, or modify it. For what this service allows, query view status."
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


def _plugin_api_child(*args: str) -> Any:
    """Run `trestle.child.plugin_api` in a throwaway interpreter (the host never imports the
    workflow package): its value, or `KeyError` with its error."""
    proc = subprocess.run(
        python_argv("-m", "trestle.child.plugin_api", *args),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=PLUGIN_API_TIMEOUT_S,
        env=build_child_env(),
        start_new_session=True,
        check=False,
    )
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        raise RuntimeError(f"plugin api child failed: {(proc.stderr or proc.stdout)[-300:]}")
    answer = json.loads(lines[-1])
    if not answer.get("ok"):
        raise KeyError(answer.get("error", "unknown name"))
    return answer["value"]


def plugin_api(*, version: str, restricted: bool = False) -> dict[str, Any]:
    """The index of the plugin surface: each public module and its names, one line each. A
    name's signature and docstring are at `trestle://guide/api/{module}.{name}`."""
    return {
        "server_version": version,
        "runtime_publishing": not restricted,
        "detail": PLUGIN_API_NAME_TEMPLATE.replace("{name}", "{module}.{name}"),
        "modules": _plugin_api_child(),
    }


def plugin_api_name(qualified: str) -> dict[str, Any]:
    """One name (`trestle.plugin.Context`): its signature and docstring. `KeyError` for a name
    that is not public."""
    value: dict[str, Any] = _plugin_api_child("--name", qualified)
    return value
