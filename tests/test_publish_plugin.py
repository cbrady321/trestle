"""MCP publish_plugin — runtime catalog updates."""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.types import PublishView, RequestOutcome
from trestle.server.main import create_kernel

PLUGIN_SOURCE = '''\
from trestle.plugin.surface import Context, trestle

@trestle
def greeter(ctx: Context, message: str = "hi") -> dict[str, str]:
    return {"message": message}
'''

UPDATED_SOURCE = '''\
from trestle.plugin.surface import Context, trestle

@trestle
def greeter(ctx: Context, message: str = "hello") -> dict[str, str]:
    return {"message": message}
'''


@pytest.fixture
def publish_kernel(trestle_home: Path, tmp_path: Path) -> object:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    return create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)


def test_publish_plugin_creates_and_runs(publish_kernel) -> None:
    kernel = publish_kernel
    before = kernel.control.list_plugins()
    result = kernel.control.publish_plugin(PLUGIN_SOURCE)
    assert isinstance(result, PublishView)
    assert result.created is True
    assert result.name == "greeter"
    after = kernel.control.list_plugins()
    assert after["registry_version"] >= before["registry_version"]
    names = {row["name"] for row in after["items"]}
    assert "greeter" in names

    run = kernel.control.run(plugin="greeter", args={"message": "pub"}, wait_ms=5000)
    assert run.state == "succeeded"


def test_publish_plugin_updates_runtime(publish_kernel) -> None:
    kernel = publish_kernel
    first = kernel.control.publish_plugin(PLUGIN_SOURCE)
    assert isinstance(first, PublishView)
    version_after_create = first.registry_version

    second = kernel.control.publish_plugin(UPDATED_SOURCE)
    assert isinstance(second, PublishView)
    assert second.created is False
    assert second.registry_version >= version_after_create
    assert second.source_sha256 != first.source_sha256

    run = kernel.control.run(plugin="greeter", args={}, wait_ms=5000)
    assert run.state == "succeeded"
    assert run.summary == {"message": "hello"}


def test_publish_plugin_rejects_invalid_source(publish_kernel) -> None:
    kernel = publish_kernel
    result = kernel.control.publish_plugin("def broken(:\n")
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.PUBLICATION_INVALID_SOURCE
    assert result.origin == "publication"


def test_publish_plugin_requires_entrypoint(publish_kernel) -> None:
    kernel = publish_kernel
    result = kernel.control.publish_plugin("x = 1\n")
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.PUBLICATION_NO_ENTRYPOINT


def test_publish_plugin_name_mismatch(publish_kernel) -> None:
    kernel = publish_kernel
    result = kernel.control.publish_plugin(PLUGIN_SOURCE, name="other")
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.PUBLICATION_NAME_MISMATCH
