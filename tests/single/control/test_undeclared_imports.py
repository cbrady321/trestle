"""R6a: publication warns, and never refuses, when a plugin imports a non-stdlib package it does
not list in `packages=`. The shipped examples declare `trestle_packs`; as they were before (a bare
`@trestle`), they warn."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trestle.common.importscan import undeclared_import_warning, undeclared_imports
from trestle.common.types import PublishView
from trestle.server.main import create_kernel

PACKAGES = Path(__file__).resolve().parents[3] / "packages"
# the tool plugins of the bundled packs
EXAMPLES = sorted(
    path
    for pack in ("docker", "pytest", "migrate", "stack")
    for path in (PACKAGES / f"trestle-pack-{pack}" / "plugins").glob("*.py")
)


def test_the_rule() -> None:
    source = (
        "from __future__ import annotations\n"
        "import os, json\n"
        "import yaml\n"
        "from requests.adapters import HTTPAdapter\n"
        "from trestle.plugin import trestle\n"
        "from trestle_packs.docker.runner import StackRunner\n"
        "from . import sibling\n"
    )
    assert undeclared_imports(source, ()) == ["requests", "trestle_packs", "yaml"]
    assert undeclared_imports(source, ("trestle_packs", "yaml", "requests")) == []
    # a declared subpackage covers only itself
    assert undeclared_imports(source, ("trestle_packs.core",)) == [
        "requests",
        "trestle_packs",
        "yaml",
    ]
    assert undeclared_imports(source, ("trestle_packs.docker",)) == ["requests", "yaml"]
    assert undeclared_import_warning("import os\n", ()) is None


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_examples_declare_their_package_and_warned_before(example: Path) -> None:
    fixed = example.read_text(encoding="utf-8")
    found = re.search(r"packages=\[([^\]]*)\]", fixed)
    assert found is not None, "the wrapper declares its pack libraries"
    declared = tuple(re.findall(r'"([a-z_]+)"', found.group(1)))
    assert declared and all(name.startswith("trestle_pack_") for name in declared)
    assert undeclared_import_warning(fixed, declared) is None
    old = re.sub(r"packages=\[[^\]]*\],?\s*", "", fixed)
    warning = undeclared_import_warning(old, ())
    assert warning is not None and all(f"'{name}'" in warning for name in declared)


# an installed non-stdlib module the plugin does not declare
PLUGIN = """
import pluggy

from trestle.plugin import Context, trestle


@trestle{decorator}
def warns_{n}(ctx: Context) -> int:
    return 1
"""


def test_publish_and_describe_show_the_warning_and_still_publish(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    kernel = create_kernel(home=home, plugin_dirs=[plugins], skip_recovery=True)
    view = kernel.control.publish_plugin(PLUGIN.format(n=1, decorator=""))
    assert isinstance(view, PublishView), view  # published: a warning, not a refusal
    payload = view.to_dict()
    assert "'pluggy'" in payload["warnings"][0]
    described = kernel.control.describe_plugin("warns_1")
    assert isinstance(described, dict)
    assert described["warnings"] == payload["warnings"]
    assert "'pluggy'" in (home / "service.log").read_text("utf-8")
    declared = kernel.control.publish_plugin(PLUGIN.format(n=2, decorator='(packages=["pluggy"])'))
    assert isinstance(declared, PublishView) and "warnings" not in declared.to_dict()
