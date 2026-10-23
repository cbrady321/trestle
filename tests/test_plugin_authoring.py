"""The authoring resources stay small, layered and true to the build that serves them.

Everything lives under `trestle://guide`: its root lists the view catalog
(`trestle://guide/views`), `trestle://guide/plugins` (an index), `trestle://guide/plugins/{topic}`,
`trestle://guide/api` (an index), `trestle://guide/api/{name}` and
`trestle://guide/examples/{name}`. They are how an agent learns to write plugins without reading
Trestle's own source or its home (decision record service-and-credentials, Decision 3, as P-6 of
the packs record amends it: a pack's source is open), one small read
at a time. These checks hold them to that and
to the code: a byte budget per resource, the index smallest; every topic reachable from the index;
the API lists exactly the public exports; no resource names an internal module or repository
path; the types and refusal codes match plugin_schema and codes; every pointer a refusal carries
names one served resource; and the server serves them under both profiles, naming the root in
its instructions, with the tool surface unchanged. That each example publishes and runs is
`tests/tree/test_tree_docs.py`'s."""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastmcp import Client

import trestle.workflow as workflow_package
from tests.proof import mcp_host
from trestle.child import plugin_api as plugin_api_child
from trestle.child import validate
from trestle.common import codes
from trestle.query.catalog import VIEW_CATALOG_URI
from trestle.server import authoring, plugin_schema

REPO = Path(__file__).resolve().parents[1]
TRESTLE = REPO / "trestle"

# Byte budgets: the index is the smallest read, a topic or one name stays a few KiB, and the API
# index lists names, not their documentation.
INDEX_BYTES = 1536
TOPIC_BYTES = 3072
API_INDEX_BYTES = 9216
API_NAME_BYTES = 4096
EXAMPLE_BYTES = 6144

EXAMPLE_NAMES = {
    "echo",
    "exec",
    "exec_with_blocked",
    "artifact_status",
    "composite_workflow",
    "pack_wrapper",
}

# What a plugin may import from Trestle, plus the test module its tests may import.
PUBLIC_MODULES = set(validate.ALLOWED_TRESTLE_PREFIXES) | {"trestle.plugin.testing"}
# Words that would send an agent into Trestle's source, the repository or the home.
FORBIDDEN_WORDS = (
    "trestle/",
    "docs/",
    "tests/",
    "examples/",
    "packages/",
    "skills/",
    ".trestle",
    "TRESTLE_HOME",
    "service.log",
    "config.toml",
    "plugin_schema",
    "plugin_validate",
    "registry.py",
    "_codec",
)
URI = re.compile(r"trestle://[a-z][a-z0-9-]*(?:/[{}A-Za-z0-9_.-]+)*")


def _uris(text: str) -> list[str]:
    """The `trestle://` URIs in `text`, without a sentence's closing period."""
    return [uri.rstrip(".") for uri in URI.findall(text)]


def _texts(restricted: bool = False) -> dict[str, str]:
    """Every markdown resource, by URI."""
    texts = {
        authoring.GUIDE_URI: authoring.guide_root(),
        authoring.PLUGIN_GUIDE_URI: authoring.plugin_guide(restricted=restricted),
    }
    for topic in authoring.topic_names():
        texts[authoring.topic_uri(topic)] = authoring.guide_topic(topic, restricted=restricted)
    return texts


# The static guide resources: the parent and its children with no parameter.
GUIDE_STATIC = (
    authoring.GUIDE_URI,
    authoring.VIEWS_GUIDE_URI,
    authoring.PLUGIN_GUIDE_URI,
    authoring.PLUGIN_API_URI,
)


def _served(uri: str) -> bool:
    """`uri` names a resource the server serves (a template's own spelling included)."""
    if uri in {VIEW_CATALOG_URI, *GUIDE_STATIC}:
        return True
    if uri in {
        authoring.PLUGIN_GUIDE_TOPIC_TEMPLATE,
        authoring.PLUGIN_API_NAME_TEMPLATE,
        authoring.PLUGIN_EXAMPLES_TEMPLATE,
        authoring.PLUGIN_API_NAME_TEMPLATE.replace("{name}", "{module}.{name}"),
    }:
        return True
    return (
        uri in {authoring.topic_uri(t) for t in authoring.topic_names()}
        or uri in authoring.example_uris()
        or uri in {f"{authoring.PLUGIN_API_URI}/{n}" for n in plugin_api_child.api_names()}
    )


def _topic(name: str) -> str:
    return authoring.guide_topic(name)


def test_every_resource_fits_its_budget() -> None:
    assert INDEX_BYTES == min(INDEX_BYTES, TOPIC_BYTES, API_INDEX_BYTES, API_NAME_BYTES)
    assert len(authoring.guide_root().encode()) <= INDEX_BYTES
    for restricted in (False, True):
        index = authoring.plugin_guide(restricted=restricted)
        assert len(index.encode()) <= INDEX_BYTES
        for topic in authoring.topic_names():
            body = authoring.guide_topic(topic, restricted=restricted)
            assert len(body.encode()) <= TOPIC_BYTES, topic
        api = json.dumps(authoring.plugin_api(version="test", restricted=restricted))
        assert len(api.encode()) <= API_INDEX_BYTES
    for name in plugin_api_child.api_names():
        described = plugin_api_child.plugin_api_name(name)
        assert len(json.dumps(described).encode()) <= API_NAME_BYTES, name
    for name in authoring.example_names():
        assert len(authoring.plugin_example(name).encode()) <= EXAMPLE_BYTES, name


def test_all_guidance_is_under_the_guide_and_listed_by_its_root() -> None:
    root = authoring.guide_root()
    children = (
        authoring.VIEWS_GUIDE_URI,
        authoring.PLUGIN_GUIDE_URI,
        authoring.PLUGIN_API_URI,
        authoring.PLUGIN_EXAMPLES_TEMPLATE,
    )
    for child in children:
        assert child.startswith(f"{authoring.GUIDE_URI}/"), child
        assert f"`{child}`" in root, f"the root does not list {child}"
    for template in (authoring.PLUGIN_GUIDE_TOPIC_TEMPLATE, authoring.PLUGIN_API_NAME_TEMPLATE):
        assert template.startswith(f"{authoring.GUIDE_URI}/"), template
    for pointer in (plugin_schema.SUBSET_POINTER, plugin_schema.SHAPE_POINTER):
        assert pointer.startswith(f"{authoring.GUIDE_URI}/"), pointer


def test_every_topic_is_reachable_from_the_index() -> None:
    index = authoring.plugin_guide()
    listed = re.findall(r"^- `([a-z-]+)`: \S", index, re.MULTILINE)
    assert listed == list(dict.fromkeys(listed)), "a topic is listed twice"
    assert set(listed) == set(authoring.topic_names())
    assert authoring.PLUGIN_GUIDE_TOPIC_TEMPLATE in index
    assert authoring.PLUGIN_API_URI in index and authoring.PLUGIN_EXAMPLES_TEMPLATE in index
    for name in EXAMPLE_NAMES:
        assert name in index
    with pytest.raises(KeyError):
        authoring.guide_topic("../index")


def test_examples_are_the_named_set() -> None:
    assert set(authoring.example_names()) == EXAMPLE_NAMES
    for name in EXAMPLE_NAMES:
        source = authoring.plugin_example(name)
        entry = [n.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)]
        assert name in entry, f"{name}: its entry point is not named after the example"
    with pytest.raises(KeyError):
        authoring.plugin_example("../index")


def test_api_modules_are_what_publication_admits() -> None:
    assert set(plugin_api_child.API_MODULES) == PUBLIC_MODULES - {"trestle.plugin.surface"}
    assert set(plugin_api_child.API_MODULES) >= {
        f"trestle.workflow.{name}" for name in workflow_package.PUBLIC_MODULES
    }


def test_plugin_api_covers_exactly_the_public_exports() -> None:
    import trestle.plugin
    import trestle.plugin.testing

    api = authoring.plugin_api(version="test")
    assert api["server_version"] == "test" and api["runtime_publishing"] is True
    assert [m["module"] for m in api["modules"]] == list(plugin_api_child.API_MODULES)
    listed: dict[str, list[str]] = {}
    for module in api["modules"]:
        names = list(module["names"])
        for home, reexported in module.get("reexports", {}).items():
            assert home in plugin_api_child.API_MODULES
            names += reexported
        expected = plugin_api_child.public_names(importlib.import_module(module["module"]))
        assert sorted(names) == sorted(expected), module["module"]
        listed[module["module"]] = names
    assert sorted(listed["trestle.plugin"]) == sorted(trestle.plugin.__all__)
    assert sorted(listed["trestle.plugin.testing"]) == sorted(trestle.plugin.testing.__all__)
    assert sorted(listed["trestle.workflow"]) == sorted(workflow_package.__all__)
    # a re-export is listed by name where it is re-exported and described where it is defined
    for module in api["modules"]:
        for home, reexported in module.get("reexports", {}).items():
            home_names = next(m["names"] for m in api["modules"] if m["module"] == home)
            assert set(reexported) <= set(home_names), (module["module"], home)
    assert set(plugin_api_child.api_names()) == {
        f"{module}.{name}" for module, names in listed.items() for name in names
    }


def test_the_server_reads_the_api_in_a_child() -> None:
    """The host never imports the workflow package: the server's answers are the child's."""
    api = authoring.plugin_api(version="test")
    assert api["modules"] == plugin_api_child.api_index()
    assert authoring.plugin_api_name("trestle.workflow.Acted") == (
        plugin_api_child.plugin_api_name("trestle.workflow.Acted")
    )
    with pytest.raises(KeyError):
        authoring.plugin_api_name("trestle.server.main.Kernel")


def test_plugin_api_name_describes_one_name() -> None:
    context = plugin_api_child.plugin_api_name("trestle.plugin.Context")
    assert context["name"] == "trestle.plugin.Context" and context["kind"] == "class"
    assert "exec" in {member["name"] for member in context["members"]}
    acted = plugin_api_child.plugin_api_name("trestle.workflow.Acted")
    assert acted["name"] == "trestle.workflow.units.Acted" and acted["kind"] == "class"
    exec_doc = plugin_api_child.plugin_api_name("trestle.plugin.testing.call_plugin")
    assert exec_doc["kind"] == "function" and exec_doc["signature"]
    for bad in ("trestle.server.main.Kernel", "trestle.plugin._codec", "trestle.plugin.nope"):
        with pytest.raises(KeyError):
            plugin_api_child.plugin_api_name(bad)


def test_no_resource_names_an_internal_module_or_repo_path() -> None:
    for restricted in (False, True):
        for uri, text in _texts(restricted).items():
            for dotted in re.findall(r"\btrestle(?:\.[a-z_]+)+", text):
                assert dotted in PUBLIC_MODULES, f"{uri} names {dotted}"
            bare = URI.sub("", text)  # a served resource's URI is a pointer, not a path
            for word in FORBIDDEN_WORDS:
                assert word not in bare, f"{uri} names {word!r}"
            assert "{examples}" not in text
    for name in EXAMPLE_NAMES:
        source = authoring.plugin_example(name)
        for dotted in re.findall(r"\btrestle(?:\.[a-z_]+)+", source):
            assert dotted in PUBLIC_MODULES, f"example {name} names {dotted}"


def test_refusal_codes_are_the_defined_publication_codes() -> None:
    defined = {
        value
        for value in vars(codes).values()
        if isinstance(value, str) and value.startswith("publication.")
    }
    refusals = _topic("refusals")
    assert set(re.findall(r"`(publication\.[a-z_]+)`", refusals)) == defined
    every = {value for value in vars(codes).values() if isinstance(value, str)}
    for uri, text in _texts().items():
        for code in re.findall(r"`((?:admission|execution|projection)\.[a-z_]+)`", text):
            assert code in every, f"{uri} names an undefined code {code}"
        if uri != authoring.topic_uri("refusals"):
            for code in re.findall(r"`(publication\.[a-z_]+)`", text):
                assert code in defined, f"{uri} names an undefined code {code}"


def test_keywords_topic_lists_the_decorator_keywords() -> None:
    from trestle.plugin import trestle as decorator

    keywords = [p for p in inspect.signature(decorator).parameters if p != "fn"]
    rows = re.findall(r"^\| `([a-z_]+)` \|", _topic("shape-and-keywords"), re.MULTILINE)
    assert rows == keywords


def _uri_literals() -> Iterator[tuple[str, str]]:
    for path in sorted(TRESTLE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for uri in _uris(node.value):
                    yield str(path.relative_to(REPO)), uri


def test_every_pointer_names_a_served_resource() -> None:
    found = list(_uri_literals())
    for pointer in (plugin_schema.SUBSET_POINTER, plugin_schema.SHAPE_POINTER):
        assert any(uri == pointer for _, uri in found)
    for where, uri in found:
        # an f-string's literal half (`trestle://guide/examples/`) is a prefix, not a pointer
        prefix = uri + "/" in (authoring.PLUGIN_EXAMPLES_PREFIX,)
        assert _served(uri) or prefix, f"{where} points at {uri}, which is not served"
    for uri, text in _texts().items():
        for pointer in _uris(text):
            assert _served(pointer), f"{uri} points at {pointer}"
    for pointer in _uris(json.dumps(authoring.plugin_api(version="x"))):
        assert _served(pointer), pointer


def test_refusals_point_at_one_topic() -> None:
    assert plugin_schema.SUBSET_POINTER == authoring.topic_uri("types")
    assert plugin_schema.SHAPE_POINTER == authoring.topic_uri("shape-and-keywords")
    message = validate.forbidden_import_error("import trestle.server.main\n")
    assert message is not None and authoring.topic_uri("imports") in message
    with pytest.raises(plugin_schema.SchemaError) as excinfo:
        plugin_schema.input_schema_from_source(
            "from trestle.plugin import trestle\n\n"
            "@trestle\nasync def f(ctx) -> dict[str, int]:\n    return {}\n"
        )
    assert plugin_schema.SHAPE_POINTER in str(excinfo.value)


@pytest.fixture
def serve_home() -> Iterator[Path]:
    home = Path(tempfile.mkdtemp(prefix="trestle-authoring-"))
    (home / "plugins").mkdir()
    shutil.copy(REPO / "examples" / "plugins" / "echo.py", home / "plugins" / "echo.py")
    yield home
    shutil.rmtree(home, ignore_errors=True)


def _read_served(home: Path) -> dict[str, object]:
    async def exercise() -> dict[str, object]:
        proc, port = mcp_host.serve_http(home)
        try:
            async with Client(transport=mcp_host.http_transport(home, port)) as client:

                async def text(uri: str) -> str:
                    return str((await client.read_resource(uri))[0].text)

                return {
                    "tools": {tool.name for tool in await client.list_tools()},
                    "listed": {str(r.uri) for r in await client.list_resources()},
                    "templates": {t.uriTemplate for t in await client.list_resource_templates()},
                    "root": await text(authoring.GUIDE_URI),
                    "views": json.loads(await text(authoring.VIEWS_GUIDE_URI)),
                    "views_alias": json.loads(await text(VIEW_CATALOG_URI)),
                    "index": await text(authoring.PLUGIN_GUIDE_URI),
                    "publishing": await text(authoring.topic_uri("publishing")),
                    "api": json.loads(await text(authoring.PLUGIN_API_URI)),
                    "context": json.loads(
                        await text(f"{authoring.PLUGIN_API_URI}/trestle.plugin.Context")
                    ),
                    "echo": await text(f"{authoring.PLUGIN_EXAMPLES_PREFIX}echo"),
                    "instructions": client.initialize_result.instructions,
                }
        finally:
            mcp_host.stop_http(proc)

    return asyncio.run(exercise())


def _check_common(served: dict[str, object], *, restricted: bool) -> None:
    listed = served["listed"]
    assert isinstance(listed, set)
    assert listed >= {VIEW_CATALOG_URI, *GUIDE_STATIC}
    # every resource but the pinned `trestle://views` alias is under the guide
    for uri in listed - {VIEW_CATALOG_URI}:
        assert uri.startswith(f"{authoring.GUIDE_URI}"), uri
    assert served["root"] == authoring.guide_root()
    assert served["views"] == served["views_alias"]
    assert served["templates"] == {
        authoring.PLUGIN_GUIDE_TOPIC_TEMPLATE,
        authoring.PLUGIN_API_NAME_TEMPLATE,
        authoring.PLUGIN_EXAMPLES_TEMPLATE,
    }
    assert served["index"] == authoring.plugin_guide(restricted=restricted)
    assert served["publishing"] == authoring.guide_topic("publishing", restricted=restricted)
    api = served["api"]
    assert isinstance(api, dict) and api["runtime_publishing"] is not restricted
    assert api["server_version"]
    context = served["context"]
    assert isinstance(context, dict) and context["members"]
    assert served["echo"] == authoring.plugin_example("echo")
    instructions = served["instructions"]
    assert isinstance(instructions, str) and authoring.GUIDE_URI in instructions
    # the instructions point at the root, never the whole set
    assert authoring.PLUGIN_API_URI not in instructions
    assert ("publishing is unavailable" in instructions) is restricted


def test_served_under_the_full_profile(serve_home: Path) -> None:
    served = _read_served(serve_home)
    tools = served["tools"]
    assert isinstance(tools, set) and len(tools) == 10  # the tool surface is unchanged
    _check_common(served, restricted=False)


def test_served_under_the_restricted_profile(serve_home: Path) -> None:
    (serve_home / "config.toml").write_text(
        '[profile]\nmode = "restricted"\nallowlist = ["echo"]\n', encoding="utf-8"
    )
    served = _read_served(serve_home)
    tools = served["tools"]
    assert isinstance(tools, set) and "publish_plugin" not in tools and len(tools) == 9
    _check_common(served, restricted=True)
    for text in (served["index"], served["publishing"]):
        assert isinstance(text, str) and "runtime publishing is unavailable" in text
