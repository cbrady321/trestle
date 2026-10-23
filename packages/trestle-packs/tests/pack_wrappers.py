"""Shared by the legacy and live tests here: load a wrapper from its bundled pack
(`packages/trestle-pack-<pack>/plugins/`), and a `FakeContext` whose
`docker` is the fake CLI (`trestle_pack_docker.fake_cli`) over a JSON state file.

Commands run for real through `FakeContext.exec`: the fake docker is an executable script, and
pytest, Alembic and Yoyo run on this test interpreter, passed as the project's `python`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from trestle.plugin.testing import FakeContext
from trestle_pack_docker.fake_cli import FakeDocker, write_fake_docker

REPO = Path(__file__).resolve().parents[3]
# the tool plugins' bundled packs, by wrapper (their own tests live beside them)
WRAPPERS = {
    "docker_stack": REPO / "packages" / "trestle-pack-docker" / "plugins",
    "pytest_run": REPO / "packages" / "trestle-pack-pytest" / "plugins",
    "migrate_apply": REPO / "packages" / "trestle-pack-migrate" / "plugins",
    "stack_pipeline": REPO / "packages" / "trestle-pack-stack" / "plugins",
}
PYTHON = sys.executable


def load_wrapper(name: str) -> ModuleType:
    """The wrapper module `<its pack>/plugins/<name>.py`, freshly loaded."""
    spec = importlib.util.spec_from_file_location(f"wrapper_{name}", WRAPPERS[name] / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass annotations resolve through sys.modules
    spec.loader.exec_module(module)
    return module


def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **state: object) -> FakeDocker:
    """A fake docker CLI the wrappers find through `TRESTLE_DOCKER_PATH`."""
    fake = FakeDocker(tmp_path / "docker-state.json")
    fake.set(**state)
    script = write_fake_docker(tmp_path / "bin", fake.state_path)
    monkeypatch.setenv("TRESTLE_DOCKER_PATH", str(script))
    return fake


def context(tmp_path: Path) -> FakeContext:
    return FakeContext(tmp_path / "ctx")


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
