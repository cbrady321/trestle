"""G-B4 (BFD-19): the validator's PYTHONPATH adds `packages/trestle-packs`;
the child's (and the conductor's) does not. Pin records the difference; the
target requires equal import roots (WR-PLAN-6, validate == execute)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.proof.markers import target_check
from trestle.server import conductor, plugin_validate
from trestle.wrapper import spawn


def _roots(env: dict[str, str]) -> list[str]:
    return [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]


def _validator_roots(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    return _roots(plugin_validate._subprocess_env())


def _child_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Import roots the child is spawned with, read off a Popen spy (no
    process is started)."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    seen: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kwargs: Any) -> object:
        seen["env"] = kwargs["env"]
        return object()

    monkeypatch.setattr(spawn.subprocess, "Popen", fake_popen)
    spawn.spawn_child(tmp_path)
    return _roots(seen["env"])


def _conductor_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    return _roots(conductor._subprocess_env(tmp_path))


def _has_packs(roots: list[str]) -> bool:
    return any(Path(r).parts[-2:] == ("packages", "trestle-packs") for r in roots)


@pytest.mark.pin("G-B4")
def test_pin_validator_path_has_packs_child_lacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _has_packs(_validator_roots(monkeypatch))
    assert not _has_packs(_child_roots(monkeypatch, tmp_path))
    assert not _has_packs(_conductor_roots(monkeypatch, tmp_path))


@pytest.mark.target("G-B4")
@pytest.mark.proves(
    "WR-PLAN-6", "WR-PLAN-6:validate-equals-execute-imports", "core", "core", "must", "CI"
)
@pytest.mark.xfail(strict=True, reason="defect:G-B4")
def test_target_import_roots_equal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    validator = _validator_roots(monkeypatch)
    child = _child_roots(monkeypatch, tmp_path)
    target_check(
        sorted(validator) == sorted(child),
        "G-B4",
        f"validator import roots {validator} != child import roots {child}",
    )
