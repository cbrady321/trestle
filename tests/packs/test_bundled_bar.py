"""Every bundled pack in `packages/trestle-pack-<name>/` meets the bundled-pack bar that can be read
from its source (P-2, P-8, P-16, P-18; the O and H criteria in
docs/proposals/2026-10-22-pack-developer-experience.md):

- its `pack.toml` names the folder, and it installs (wrappers under `plugins/`);
- its library carries Trestle's version (lockstep, P-18) and depends on nothing pip would fetch:
  `trestle pack install` installs a bundled library with `--no-deps`, so it imports only the
  standard library, `trestle` and itself;
- the library's package exports its API through `__all__` (H3);
- no wrapper runs a command around `ctx.exec` (O1): no `subprocess` or `os.system`;
- each wrapper has a `FakeContext` test beside it in the pack's `tests/` (O6, H5).
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

import trestle
from trestle.packs.bundled import BUNDLED_PREFIX, available_bundled
from trestle.packs.manifest import PLUGINS_DIR, load_manifest, read_library, wrapper_names

PACKAGES = Path(__file__).resolve().parents[2] / "packages"
BUNDLED = available_bundled(PACKAGES)
TOOL_PACKS = ("docker", "pytest", "migrate", "stack")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module.split(".", 1)[0])
    return out


def test_the_tool_packs_are_bundled() -> None:
    assert set(TOOL_PACKS) <= set(BUNDLED)


def test_stack_requires_the_tool_packs() -> None:
    assert load_manifest(BUNDLED["stack"]).packs == ("docker", "pytest", "migrate")


@pytest.mark.parametrize("name", sorted(BUNDLED))
def test_manifest_names_the_folder_and_has_plugins(name: str) -> None:
    pack = BUNDLED[name]
    assert pack.name == f"{BUNDLED_PREFIX}{name}"
    assert load_manifest(pack).name == name
    assert wrapper_names(pack / PLUGINS_DIR)


@pytest.mark.parametrize("name", sorted(BUNDLED))
def test_library_is_lockstep_and_needs_nothing_fetched(name: str) -> None:
    pack = BUNDLED[name]
    library = read_library(pack, load_manifest(pack))
    if library is None:
        return  # wrappers only: the pack carries Trestle's version at install
    assert library.version == trestle.__version__, f"{name}: {library.version}"
    project = tomllib.loads((library.path / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"].get("dependencies", []) == [], name
    packages = project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    allowed = set(sys.stdlib_module_names) | {"trestle", *packages}
    for package in packages:
        init = library.path / package / "__init__.py"
        assert "__all__" in init.read_text(encoding="utf-8"), f"{package} exports no __all__"
        for module in sorted((library.path / package).rglob("*.py")):
            extra = _imports(module) - allowed
            assert not extra, f"{module.relative_to(PACKAGES)} imports {sorted(extra)}"


@pytest.mark.parametrize("name", sorted(BUNDLED))
def test_wrappers_run_commands_through_ctx_exec(name: str) -> None:
    for plugin, path in wrapper_names(BUNDLED[name] / PLUGINS_DIR).items():
        assert "subprocess" not in _imports(path), plugin
        assert "os.system" not in path.read_text(encoding="utf-8"), plugin


@pytest.mark.parametrize("name", sorted(BUNDLED))
def test_every_wrapper_has_a_fake_context_test(name: str) -> None:
    tests = [
        path.read_text(encoding="utf-8")
        for path in sorted((BUNDLED[name] / "tests").glob("test_*.py"))
    ]
    for plugin in wrapper_names(BUNDLED[name] / PLUGINS_DIR):
        assert any(plugin in text and "call_plugin" in text for text in tests), (
            f"{name}: no FakeContext test calls {plugin}"
        )
