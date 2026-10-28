"""G-B4 (BFD-19), flipped by L.CL-C1.3: the validator's PYTHONPATH used to add the repo root and
`packages/trestle-packs`, the conductor's and the child's only the repo root, and the child ran
with its working directory on `sys.path`. Every plugin-code process now starts from
`trestle.common.pyenv` (WR-PLAN-6, validate == execute)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.pins.b_contract.kit import run_plugin
from tests.proof.markers import target_check
from trestle.server import plugin_validate
from trestle.wrapper import spawn

Launch = tuple[list[str], dict[str, str]]


def _roots(env: dict[str, str]) -> list[str]:
    return [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]


def _validator_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Launch:
    """Argv and env the validator subprocess is started with, read off a `run` spy."""
    seen: dict[str, Any] = {}
    real_run = subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "trestle.child.validate" not in cmd:
            return real_run(cmd, **kwargs)
        seen["launch"] = (cmd, kwargs["env"])
        seen["stdin"] = kwargs.get("stdin")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}\n', stderr="")

    with monkeypatch.context() as patch:
        patch.setattr(plugin_validate.subprocess, "run", fake_run)
        assert plugin_validate.validate_plugin(tmp_path / "plugin.py") is None
    assert seen["stdin"] is subprocess.DEVNULL
    launch: Launch = seen["launch"]
    return launch


def _child_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Launch:
    """Argv and env the child is spawned with, read off a Popen spy (no process is started)."""
    seen: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kwargs: Any) -> object:
        seen["launch"] = (cmd, kwargs["env"])
        return object()

    with monkeypatch.context() as patch:
        patch.setattr(spawn.subprocess, "Popen", fake_popen)
        spawn.spawn_child(tmp_path)
    launch: Launch = seen["launch"]
    return launch


def _conductor_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Launch:
    """Argv and env the conductor starts a run's wrapper with, recorded off a real run whose
    wrapper is replaced by a process that exits at once."""
    (tmp_path).mkdir(exist_ok=True)
    seen: list[Launch] = []
    real_popen = subprocess.Popen

    def spy_popen(cmd: list[str], **kwargs: Any) -> Any:
        if "trestle.wrapper.main" not in cmd:
            return real_popen(cmd, **kwargs)
        seen.append((cmd, kwargs["env"]))
        return real_popen([sys.executable, "-c", "pass"], **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", spy_popen)
        view, _run_dir = run_plugin(tmp_path, "echo", {"message": "g-b4"})
    assert view.state == "succeeded"
    assert len(seen) == 1
    return seen[0]


@pytest.mark.proves(
    "WR-PLAN-6", "WR-PLAN-6:validate-equals-execute-imports", "core", "core", "must", "CI"
)
def test_target_import_roots_equal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The operator's own PYTHONPATH reaches all three launches unchanged; nothing is added
    # (no repo root, no packs directory).
    inherited = os.pathsep.join([str(tmp_path / "operator-path"), *_roots(dict(os.environ))])
    monkeypatch.setenv("PYTHONPATH", inherited)
    launches = {
        "validator": _validator_launch(monkeypatch, tmp_path),
        "child": _child_launch(monkeypatch, tmp_path),
        "conductor": _conductor_launch(monkeypatch, tmp_path / "run"),
    }
    roots = {name: _roots(env) for name, (_argv, env) in launches.items()}
    target_check(
        all(env.get("PYTHONPATH") == inherited for _argv, env in launches.values()),
        "G-B4",
        f"an import path differs from the inherited PYTHONPATH {inherited!r}: {roots}",
    )
    for name, (argv, _env) in launches.items():
        target_check(argv[1] == "-P", "G-B4", f"{name} is not started with `python -P`: {argv[:3]}")
