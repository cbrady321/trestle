"""K-15 (L.NW-1.2): `integration_pipeline` stops its owned stack exactly once on every path,
success included (WR-ENV-11:pipeline-one-teardown@fake, entry-points-callable).

The pipeline module is loaded from `examples/packs/` and its `StackRunner` is monkeypatched to
run over the in-memory `FakeComposeBackend`; the pytest stage is a stub returning a fixed
`PytestResult`, so nothing here starts an engine or a nested pytest session."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

import pytest
from fake_compose_backend import FakeComposeBackend
from fake_pack_context import FakePackContext

from trestle_packs.docker.runner import StackRunner
from trestle_packs.pytest.runner import PytestResult

ROOT = Path(__file__).resolve().parents[4]
COMPOSE = "services:\n  web:\n    image: alpine:3.20\n"


class _Ctx(FakePackContext):
    """FakePackContext plus the `outputs` directory the pipeline writes its report under."""

    @property
    def outputs(self) -> Path:
        path = self.work / "outputs"
        path.mkdir(parents=True, exist_ok=True)
        return path


def _load_pipeline() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "integration_pipeline_k15", ROOT / "examples" / "packs" / "integration_pipeline.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, teardown: str, variant: str
) -> tuple[FakeComposeBackend, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    backend = FakeComposeBackend(fail_on_wave=0 if variant == "up_fails" else None)
    module = _load_pipeline()
    monkeypatch.setattr(module, "StackRunner", lambda ctx: StackRunner(ctx, backend=backend))
    exit_code = 1 if variant == "pytest_fails" else 0
    monkeypatch.setattr(
        module,
        "run_pytest",
        lambda spec, *, report_dir: PytestResult(exit_code=exit_code, collected=1, passed=1),
    )
    stack = module.PipelineStack(
        project="k15",
        teardown=teardown,
        waves=[module.PipelineWave(name="app", services=["web"], wait="started")],
    )
    raised = ""
    try:
        result = module.integration_pipeline(_Ctx(work=tmp_path), stack, workdir=str(tmp_path))
        assert result["ok"] is True and set(result["stages"]) == {"docker", "pytest"}
    except Exception as exc:  # noqa: BLE001 - the variant under test decides what is raised
        raised = f"{type(exc).__name__}: {exc}"
    return backend, raised


@pytest.mark.proves("WR-ENV-11", "WR-ENV-11:entry-points-callable", "B", "B", "LOGIC+PROC", "CI")
@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-15", "B", "B", "LOGIC+PROC", "CI")
@pytest.mark.parametrize("variant", ["up_fails", "pytest_fails", "success"])
def test_pipeline_tears_down_once(
    variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, raised = _drive(tmp_path, monkeypatch, teardown="down", variant=variant)
    assert bool(raised) == (variant != "success"), raised
    # exactly one teardown, and never one that removes volumes (K-6)
    assert backend.down_calls == [False]
    assert backend.stop_calls == 0


@pytest.mark.parametrize("variant", ["up_fails", "pytest_fails", "success"])
def test_declared_stop_policy_stops_once_and_none_does_nothing(
    variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _ = _drive(tmp_path, monkeypatch, teardown="stop", variant=variant)
    assert (backend.stop_calls, backend.down_calls) == (1, [])
    backend, _ = _drive(tmp_path / "n", monkeypatch, teardown="none", variant=variant)
    assert (backend.stop_calls, backend.down_calls) == (0, [])


@pytest.mark.proves("WR-ENV-11", "WR-ENV-11:entry-points-callable", "B", "B", "LOGIC+PROC", "CI")
def test_pipeline_signature_and_schema_unchanged() -> None:
    module = _load_pipeline()
    params = inspect.signature(module.integration_pipeline).parameters
    assert list(params) == ["ctx", "stack_spec", "alembic_config", "pytest_path", "workdir"]
    assert params["pytest_path"].default == "tests" and params["workdir"].default is None
