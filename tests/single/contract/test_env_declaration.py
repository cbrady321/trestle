"""L.SV-2.4: the D-b publication rule. A plugin importing a port module must declare `env_arg`, and
a workflow root's `env_key_field` must equal it; both refusals are `publication.env_arg_missing`
(a disagreement between two declared names is `publication.declaration_invalid`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.single.contract import declared_fixtures as fx
from tests.single.contract.test_publish_workflow import (
    ECHO,
    ECHO_SNAPSHOT_ID,
    ECHO_SNAPSHOT_RUNTIME,
    WORKFLOW_SOURCE,
)
from trestle.child.validate import env_declaration_error, imported_port_modules
from trestle.common import codes
from trestle.common.plan import DeclaredTree
from trestle.common.types import PublishView, RequestOutcome
from trestle.server.main import create_kernel
from trestle.server.plugin_validate import PublicationRefused
from trestle.server.snapshots import load_declared_tree, materialize_snapshot
from trestle.workflow.extract import extract_declared_tree

PORT_IMPORTER = """
from __future__ import annotations

from trestle.plugin import Context, trestle
from trestle_packs.process import run_command  # noqa: F401


@trestle{decorator}
def porter(ctx: Context, env: str = "dev") -> dict[str, str]:
    return {{"env": env}}
"""


def _tree(env_key_field: str | None) -> DeclaredTree:
    return extract_declared_tree(fx.leaf_entry(env_key_field=env_key_field))


def _workflow_with_env(tmp_path: Path, name: str, *, env_arg: str | None, key: str | None) -> Path:
    decorator = "" if env_arg is None else f'(env_arg="{env_arg}")'
    key_text = "None" if key is None else f'"{key}"'
    source = (
        WORKFLOW_SOURCE.replace("BUDGET_S", "30")
        .replace("@trestle\n", f"@trestle{decorator}\n")
        .replace("max_attempts=1,", f"max_attempts=1,\n            env_key_field={key_text},")
    )
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.proves("WR-OWN-8", "WR-OWN-8:port-importer-declares-env", "A", "single", "PROC", "CI")
def test_port_importer_without_env_arg_refused_at_publication(tmp_path: Path) -> None:
    src = tmp_path / "porter.py"
    src.write_text(PORT_IMPORTER.format(decorator=""), encoding="utf-8")
    home = tmp_path / "home"
    with pytest.raises(PublicationRefused) as info:
        materialize_snapshot(src, "porter", home=home)
    assert info.value.code == codes.PUBLICATION_ENV_ARG_MISSING == "publication.env_arg_missing"
    assert "env_arg" in str(info.value) and "trestle_packs.process" in str(info.value)
    snap_root = home / "snapshots"
    assert not snap_root.exists() or not any(snap_root.iterdir())  # no snapshot promoted

    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = create_kernel(home=tmp_path / "trestle", plugin_dirs=[plugin_dir], skip_recovery=True)
    refused = kernel.control.publish_plugin(src.read_text(encoding="utf-8"))
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.PUBLICATION_ENV_ARG_MISSING
    assert "env_arg" in refused.message

    # the same importer that declares env_arg is not refused by this rule (the static half only
    # reads the AST; the import itself is the validator's ordinary check)
    ports = imported_port_modules(src.read_text(encoding="utf-8"))
    assert ports == ["trestle_packs.process", "trestle_packs.process.run_command"]
    assert env_declaration_error(ports, "env", None) is None


def test_port_module_scan() -> None:
    hit = [
        "from trestle_packs.fakes.marker import Marker",
        "import trestle_packs.container",
        "from trestle_packs import toolchain",
        "from trestle_packs.grant import G",
        "import trestle.workflow.ports",
        "from trestle_packs.provision import p",
        "from trestle_packs.testrun import t",
    ]
    miss = [
        "from trestle_packs.docker.runner import StackRunner",
        "from trestle_packs.core import dag",
        "from trestle.plugin import Context",
        "import trestle_packs",
        "from trestle_packs import docker",
        "import processing",
    ]
    for line in hit:
        assert imported_port_modules(line + "\n"), line
    for line in miss:
        assert imported_port_modules(line + "\n") == [], line


def test_env_key_field_must_match_declared_env_arg(tmp_path: Path) -> None:
    home = tmp_path / "home"
    # equal: publishes, and the declared tree carries the field
    ok = materialize_snapshot(
        _workflow_with_env(tmp_path, "ok", env_arg="env", key="env"), "wf", home=home
    )
    tree = load_declared_tree(ok)
    assert tree is not None and tree.nodes[""]["env_key_field"] == "env"

    def refused(name: str, *, env_arg: str | None, key: str | None) -> PublicationRefused:
        src = _workflow_with_env(tmp_path, name, env_arg=env_arg, key=key)
        with pytest.raises(PublicationRefused) as info:
            materialize_snapshot(src, "wf", home=tmp_path / f"home-{name}")
        assert not (tmp_path / f"home-{name}" / "snapshots").exists() or not any(
            (tmp_path / f"home-{name}" / "snapshots").iterdir()
        )
        return info.value

    # two different names: the declaration disagrees with itself
    different = refused("different", env_arg="env", key="other")
    assert different.code == codes.PUBLICATION_DECLARATION_INVALID
    assert "'other'" in str(different) and "'env'" in str(different)
    # the root names no env_key_field though the plugin declares env_arg: the field is missing
    no_key = refused("nokey", env_arg="env", key=None)
    assert no_key.code == codes.PUBLICATION_ENV_ARG_MISSING and "env_key_field" in str(no_key)
    # the root names env_key_field but the plugin declares no env_arg: env_arg is missing
    no_arg = refused("noarg", env_arg=None, key="env")
    assert no_arg.code == codes.PUBLICATION_ENV_ARG_MISSING and "env_arg" in str(no_arg)

    # the rule as a pure function, port importer included
    ports = ["trestle_packs.process"]
    assert env_declaration_error(ports, "env", _tree("env")) is None
    assert env_declaration_error(ports, None, _tree(None)) is not None
    error = env_declaration_error(ports, "env", _tree(None))
    assert error is not None and error[0] == codes.PUBLICATION_ENV_ARG_MISSING


def test_plugin_without_ports_needs_no_env_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a plain plugin publishes with the identity it has at wr-ckpt/core (under that runtime version)
    monkeypatch.setattr("trestle.__version__", ECHO_SNAPSHOT_RUNTIME)
    plain = materialize_snapshot(ECHO, "echo", home=tmp_path / "plain")
    assert plain.snapshot_id == ECHO_SNAPSHOT_ID
    # a workflow plugin that neither imports ports nor names an environment publishes
    src = _workflow_with_env(tmp_path, "noenv", env_arg=None, key=None)
    snap = materialize_snapshot(src, "wf", home=tmp_path / "wf")
    tree = load_declared_tree(snap)
    assert tree is not None and tree.nodes[""]["env_key_field"] is None
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = create_kernel(home=tmp_path / "trestle", plugin_dirs=[plugin_dir], skip_recovery=True)
    assert isinstance(kernel.control.publish_plugin(src.read_text(encoding="utf-8")), PublishView)
