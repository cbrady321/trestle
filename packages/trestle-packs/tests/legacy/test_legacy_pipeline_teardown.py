"""K-15 (L.NW-1.2): `stack_pipeline` (was `integration_pipeline`) stops its owned stack exactly
once on every path, success included (WR-ENV-11:pipeline-one-teardown@fake,
entry-points-callable).

The pipeline is loaded from `examples/packs/` and called with `FakeContext`; its docker is the
fake CLI (`trestle_packs.fakes.docker_cli`), and its pytest stage runs a one-test suite out of
process on this test interpreter, so nothing here starts an engine."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pack_wrappers import PYTHON, compose, context, fake_docker, load_wrapper
from trestle.plugin.testing import call_plugin

COMPOSE = "services:\n  web:\n    image: alpine:3.20\n"
PASSING = "def test_ok():\n    pass\n"
FAILING = "def test_bad():\n    assert False\n"


def _drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, teardown: str, variant: str
) -> tuple[dict, list[str]]:
    work = tmp_path / "work"
    compose(work, COMPOSE)
    (work / "tests").mkdir()
    body = FAILING if variant == "pytest_fails" else PASSING
    (work / "tests" / "test_stage.py").write_text(body, encoding="utf-8")
    fake = fake_docker(tmp_path, monkeypatch, fail=["web"] if variant == "up_fails" else [])
    args = {
        "stack_spec": {
            "project": "k15",
            "teardown": teardown,
            "waves": [{"name": "app", "services": ["web"], "wait": "started"}],
        },
        "workdir": str(work),
        "python": PYTHON,
    }
    answer = call_plugin(load_wrapper("stack_pipeline").stack_pipeline, args, ctx=context(tmp_path))
    assert isinstance(answer, dict)
    return answer, [c for c in fake.calls if c.split()[0] in ("down", "stop")]


@pytest.mark.proves("WR-ENV-11", "WR-ENV-11:entry-points-callable", "B", "B", "LOGIC+PROC", "CI")
@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-15", "B", "B", "LOGIC+PROC", "CI")
@pytest.mark.parametrize("variant", ["up_fails", "pytest_fails", "success"])
def test_pipeline_tears_down_once(
    variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer, teardowns = _drive(tmp_path, monkeypatch, teardown="down", variant=variant)
    assert answer["ok"] is (variant == "success"), answer
    expected = {"up_fails": "docker", "pytest_fails": "pytest", "success": None}[variant]
    assert answer["failed_stage"] == expected
    # exactly one teardown, and never one that removes volumes (K-6)
    assert teardowns == ["down"]


@pytest.mark.parametrize("variant", ["up_fails", "pytest_fails", "success"])
def test_declared_stop_policy_stops_once_and_none_does_nothing(
    variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, teardowns = _drive(tmp_path, monkeypatch, teardown="stop", variant=variant)
    assert teardowns == ["stop"]
    _, teardowns = _drive(tmp_path / "n", monkeypatch, teardown="none", variant=variant)
    assert teardowns == []


@pytest.mark.proves("WR-ENV-11", "WR-ENV-11:entry-points-callable", "B", "B", "LOGIC+PROC", "CI")
def test_pipeline_signature_and_schema_unchanged() -> None:
    module = load_wrapper("stack_pipeline")
    params = inspect.signature(module.stack_pipeline).parameters
    assert list(params)[:5] == ["ctx", "stack_spec", "alembic_config", "pytest_path", "workdir"]
    assert params["pytest_path"].default == "tests" and params["workdir"].default is None
    assert all(
        p.default is None
        for name, p in params.items()
        if name not in ("ctx", "stack_spec", "pytest_path")
    )
