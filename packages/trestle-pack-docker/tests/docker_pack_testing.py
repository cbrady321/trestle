"""Shared by the docker pack's tests: load the wrapper from `plugins/`, and a fake docker CLI
(`trestle_pack_docker.fake_cli`) over a JSON state file, run for real through `FakeContext.exec`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from trestle_pack_docker.fake_cli import FakeDocker, write_fake_docker

from trestle.plugin.testing import FakeContext

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
PYTHON = sys.executable  # passed to the wrappers as the project's interpreter


def load_wrapper(name: str) -> ModuleType:
    """The wrapper module `plugins/<name>.py` of this pack, freshly loaded."""
    spec = importlib.util.spec_from_file_location(f"wrapper_{name}", PLUGINS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass annotations resolve through sys.modules
    spec.loader.exec_module(module)
    return module


def context(tmp_path: Path) -> FakeContext:
    return FakeContext(tmp_path / "ctx")


def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **state: object) -> FakeDocker:
    """A fake docker CLI the wrappers find through `TRESTLE_DOCKER_PATH`."""
    fake = FakeDocker(tmp_path / "docker-state.json")
    fake.set(**state)
    script = write_fake_docker(tmp_path / "bin", fake.state_path)
    monkeypatch.setenv("TRESTLE_DOCKER_PATH", str(script))
    return fake


def compose(work: Path, body: str, name: str = "docker-compose.yml") -> Path:
    work.mkdir(parents=True, exist_ok=True)
    path = work / name
    path.write_text(body, encoding="utf-8")
    return path


STACK = """\
services:
  postgres:
    image: postgres:16
  redis:
    image: redis:7
  api:
    image: example/api
    volumes:
      - data:/data
  debug:
    image: alpine:3.20
    profiles: [debug]
volumes:
  data:
"""
