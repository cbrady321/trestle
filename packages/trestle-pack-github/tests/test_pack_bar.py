"""The github pack against the bundled-pack bar (P-8, P-9, P-18, and the criteria in
`docs/proposals/2026-10-22-pack-developer-experience.md` § 4): a valid `pack.toml`, lockstep
version, wrappers that carry the recipe over the library's `__all__`, a test per wrapper, and a
library with no third-party dependency and no subprocess of its own."""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest
from trestle.packs.manifest import load_manifest, read_library, wrapper_names
from trestle.server.plugin_schema import schemas_from_source

import trestle_pack_github

PACK = Path(__file__).resolve().parents[1]
REPO = PACK.parents[1]
WRAPPERS = sorted((PACK / "plugins").glob("*.py"))
LIBRARY = sorted((PACK / "trestle_pack_github").rglob("*.py"))
ALLOWED_TOP = {"trestle", "trestle_pack_github"} | set(sys.stdlib_module_names)


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [(alias.name, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, [alias.name for alias in node.names]))
    return found


def test_the_manifest_names_the_pack_its_library_tools_and_policy() -> None:
    manifest = load_manifest(PACK)
    assert manifest.name == "github"
    assert manifest.tools == ("gh", "git")
    assert manifest.config_files == ("TRESTLE_GITHUB_POLICY",)  # P-20
    library = read_library(PACK, manifest)
    assert library is not None and library.dist == "trestle-pack-github"
    assert set(wrapper_names(PACK / "plugins")) == {
        "github_policy", "github_pr_status", "github_pr_comments", "github_pr_open",
    }  # fmt: skip


def test_the_version_is_trestles(tmp_path: Path) -> None:  # P-18, lockstep
    trestle = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    pack = tomllib.loads((PACK / "pyproject.toml").read_text(encoding="utf-8"))
    assert pack["project"]["version"] == trestle["project"]["version"]


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.stem)
def test_each_wrapper_carries_its_recipe_over_the_public_api(wrapper: Path) -> None:
    source = wrapper.read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 200, "H2: a wrapper reads top to bottom"
    assert ast.get_docstring(ast.parse(source)), "the recipe is the module docstring"
    schemas_from_source(source, source_path=wrapper)  # publishes: arguments and answer typed
    for module, names in _imports(wrapper):
        top = module.split(".")[0]
        assert top in ALLOWED_TOP, f"{wrapper.name} imports {module}"
        assert module not in {"subprocess", "os"}, "commands go through ctx.exec"
        if module == "trestle_pack_github":
            assert set(names) <= set(trestle_pack_github.__all__), "H3: the exported API only"
        if top == "trestle" and module != "trestle.plugin":
            pytest.fail(f"{wrapper.name}: only trestle.plugin is a plugin's surface")
    assert (PACK / "tests" / f"test_{wrapper.stem}.py").is_file(), "O6/H5: a test per wrapper"


@pytest.mark.parametrize("module", LIBRARY, ids=lambda p: str(p.relative_to(PACK)))
def test_the_library_needs_no_third_party_package_and_runs_no_subprocess(module: Path) -> None:
    for name, _ in _imports(module):
        top = name.split(".")[0]
        assert top in ALLOWED_TOP, f"{module.name} imports {name}: bundled installs are --no-deps"
        if module.parent.name != "fakes":
            assert top != "subprocess", "commands go through ctx.exec"
            if top == "trestle":
                assert name == "trestle.plugin", f"{module.name}: only trestle.plugin"


def test_every_exported_name_exists() -> None:
    for name in trestle_pack_github.__all__:
        assert hasattr(trestle_pack_github, name), name


def test_installs_by_name_as_a_bundled_pack(tmp_path: Path) -> None:
    import io

    from trestle.packs.install import Installer, Runners

    pip_calls: list[list[str]] = []
    runners = Runners(
        git=lambda argv, cwd: pytest.fail(f"unexpected git {argv}"),
        pip=pip_calls.append,
        which=lambda tool: f"/usr/bin/{tool}" if tool in {"gh", "git"} else None,
        live_runs=lambda _home: {},
        sleep=lambda _s: None,
    )
    home = tmp_path / "home"
    home.mkdir()
    entry = Installer(home=home, runners=runners, out=io.StringIO(), bundled=REPO / "packages")
    locked = entry.install("github")
    assert locked.name == "github" and locked.owner == "trestle" and locked.version == "0.5.2"
    copy = Path(locked.pack_dir)
    assert copy == home / "packs" / "trestle" / "github"
    assert (copy / "plugins" / "github_pr_open.py").is_file()
    assert (copy / "trestle_pack_github" / "policy.py").is_file()
    assert any("--no-deps" in argv for argv in pip_calls), pip_calls
