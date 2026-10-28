"""Bindings of the toolchain family (L.RB-4.2): the stdlib `FakeToolchainResolver` and the real
`MiseToolchainResolver` over the real `CommandPort` and the absolute-path `stub_mise` (MC-B-06).

The family's cases (`tests/conformance/toolchain_cases.py`) run UNMODIFIED against both; what they
need beyond the port is the fixture contract in that module's docstring, built here as a small
world on disk: install trees whose executables are real (they print a version for `--version`),
a `stub_mise` wrapper (an absolute-path script whose interpreter is this interpreter, never named
`mise` and never on the search path), and the JSON configuration the stub reads.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from conformance import toolchain_cases as cases
from tests.proof.suites.ports import core

from trestle_packs.fakes.toolchain import FakeToolchainResolver
from trestle_packs.process.command import CommandPort
from trestle_packs.toolchain import MiseToolchainResolver

REPO = Path(__file__).resolve().parents[4]
STUB_MISE = REPO / "tests" / "fixtures" / "stubs" / "stub_mise.py"
PROJECT = "demo-py"
TOOLS = {  # tool -> (listed version, requested pin, the version its executable reports)
    "python": ("3.12.4", "3.12", "3.12.4"),
    "go": ("1.22.1", "1.22", "1.22.1"),
    "old": ("3.9.1", "3.11", "3.9.1"),  # installed, but not the pin
}
NEWER = "3.12.5"  # what `reinstall` installs in place of python's 3.12.4
LONG_PIN = "9" * 300


def executable_script(text: str) -> str:
    body = f"import sys\nprint({text!r} if sys.argv[1:] == ['--version'] else '')\n"
    return f"#!{sys.executable}\n{body}"


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class World:
    """The fixture world under one directory: installs, the stub's config, the envelope."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.installs = base / "installs"
        self.envelope = base / "envelope"
        (self.envelope / "cache").mkdir(parents=True)
        (self.envelope / "state").mkdir(parents=True)
        self.config = base / "world" / "mise.json"
        self.log = base / "world" / "mise.log"
        self.config.parent.mkdir()
        self.tools: dict[str, Any] = {}
        for name, (version, pin, _reported) in TOOLS.items():
            self.install(name, version, pin)
        self.tools["node"] = self.entry("node", "20.1.0", "20", installed=False)
        self.tools["hollow"] = self.entry("hollow", "1.0.0", "1", installed=True)  # no executable
        # minimal: the console excerpt the port returns is at most 512 bytes (B3-C14)
        self.tools["toolong"] = {"requested_version": LONG_PIN, "installed": False}
        self.mode = "normal"
        self.write_config()
        self.stub = base / "bin" / "stub_mise"
        write_executable(
            self.stub,
            f"#!{sys.executable}\nimport runpy, sys\n"
            f"sys.argv = ['stub_mise.py', *sys.argv[1:]]\n"
            f"runpy.run_path({str(STUB_MISE)!r}, run_name='__main__')\n",
        )
        self.saved_path = os.environ.get("PATH", "")

    def entry(self, tool: str, version: str, pin: str, installed: bool = True) -> dict[str, Any]:
        return {
            "version": version,
            "requested_version": pin,
            "install_path": str(self.installs / tool / version),
            "installed": installed,
            "active": True,
            "source": {"type": "stub_mise.toml", "path": "world"},
        }

    def install(self, tool: str, version: str, pin: str) -> None:
        reported = TOOLS.get(tool, (version, pin, version))[2]
        write_executable(
            self.installs / tool / version / "bin" / tool,
            executable_script(f"{tool} {reported}"),
        )
        self.tools[tool] = self.entry(tool, version, pin)

    def write_config(self) -> None:
        self.config.write_text(json.dumps({"mode": self.mode, "tools": self.tools}), "utf-8")

    def surface(self, mode: str) -> None:
        self.mode = mode
        self.write_config()

    def reinstall(self) -> None:
        write_executable(
            self.installs / "python" / NEWER / "bin" / "python",
            executable_script(f"python {NEWER}"),
        )
        self.tools["python"] = self.entry("python", NEWER, TOOLS["python"][1])
        self.write_config()

    def plant_decoy(self, name: str) -> str:
        path = self.base / "decoy" / name
        write_executable(path, executable_script(f"{name} 0.0.1"))
        os.environ["PATH"] = str(path.parent) + os.pathsep + os.environ.get("PATH", "")
        return str(path)

    def restore(self) -> None:
        os.environ["PATH"] = self.saved_path

    def extras(self, mise_calls: Callable[[], list[tuple[str, ...]]]) -> dict[str, Any]:
        return {
            "project": PROJECT,
            "tool": "python",
            "pin": TOOLS["python"][1],
            "installed_version": TOOLS["python"][2],
            "newer_version": NEWER,
            "other_tool": "go",
            "missing_tool": "node",
            "unlisted_tool": "ruby",
            "mismatch_tool": "old",
            "hollow_tool": "hollow",
            "long_pin_tool": "toolong",
            "install_root": str(self.installs),
            "surface": self.surface,
            "reinstall": self.reinstall,
            "mise_calls": mise_calls,
            "plant_decoy": self.plant_decoy,
        }


@contextlib.contextmanager
def stub_environment(config: Path) -> Iterator[None]:
    keep = {k: os.environ.get(k) for k in ("STUB_MISE_CONFIG", "STUB_MISE_MODE", "STUB_MISE_LOG")}
    os.environ["STUB_MISE_CONFIG"] = str(config)
    os.environ.pop("STUB_MISE_MODE", None)
    os.environ.pop("STUB_MISE_LOG", None)
    try:
        yield
    finally:
        for key, value in keep.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def stub_text(config: Path) -> str:
    """What the stub prints for `ls --current --json`, run in this process (the fake's source)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("stub_mise_inproc", STUB_MISE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = io.StringIO()
    with stub_environment(config), contextlib.redirect_stdout(out):
        module.main(list(cases.LIST_ARGV))
    return out.getvalue()


def _fresh(base: Path) -> Path:
    n = len(list(base.glob("run-*")))
    directory = base / f"run-{n}"
    directory.mkdir()
    return directory


def fake_toolchain(
    base: Path, resolver: type[FakeToolchainResolver] = FakeToolchainResolver
) -> Callable[[], core.Implementation]:
    def build() -> core.Implementation:
        world = World(_fresh(base))
        fake = resolver({PROJECT: lambda: stub_text(world.config)})
        if hasattr(fake, "envelope"):
            fake.envelope = world.envelope  # a planted defect writes where it may not
        return core.Implementation(
            fake,
            core.Reach(envelope=world.envelope),
            name="fake",
            extras=world.extras(lambda: list(fake.calls)),
            close=world.restore,
        )

    return build


def mise_stub_toolchain(
    base: Path, resolver: type[MiseToolchainResolver] = MiseToolchainResolver
) -> Callable[[], core.Implementation]:
    def build() -> core.Implementation:
        world = World(_fresh(base))
        environment = {"STUB_MISE_CONFIG": str(world.config), "STUB_MISE_LOG": str(world.log)}
        real = resolver(world.stub, CommandPort(), {PROJECT: environment}, envelope=world.envelope)

        def calls() -> list[tuple[str, ...]]:
            if not world.log.exists():
                return []
            lines = world.log.read_text(encoding="utf-8").splitlines()
            return [tuple(json.loads(line)["argv"]) for line in lines]

        return core.Implementation(
            real,
            core.Reach(envelope=world.envelope),
            name="mise-stub",
            extras=world.extras(calls),
            close=world.restore,
        )

    return build
