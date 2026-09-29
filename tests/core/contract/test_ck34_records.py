"""L.CK-3/4.1 (BFD-13; K-3, OQ-3 recorded default): a dataclass-annotated argument arrives as
that dataclass, recursively, behind the `_codec.TYPED_RECORDS` switch; dict-annotated plugins
are unchanged."""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.pins.b_contract.kit import PLUGINS, run_plugin
from tests.proof import harness
from trestle.child.main import _bind_args
from trestle.common.types import RunView
from trestle.plugin import _codec

REPO = Path(__file__).resolve().parents[3]


@dataclass
class Leg:
    name: str
    at: datetime | None = None


@dataclass
class Route:
    origin: str
    legs: list[Leg] = field(default_factory=list)
    extra: dict[str, Leg] = field(default_factory=dict)
    home: Leg | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pin:
    x: int
    y: int


def typed(route: Route, pins: set[Pin] | None = None) -> None:
    """Plugin-shaped function: postponed annotations, records nested in containers."""


def plain(payload: dict[str, int]) -> None: ...


ROUTE = {
    "origin": "a",
    "legs": [{"name": "l1", "at": "2026-01-01T10:00:00+00:00"}, {"name": "l2"}],
    "extra": {"k": {"name": "kx"}},
    "home": {"name": "h"},
    "tags": ["t1", "t2"],
}


def test_dataclass_argument_arrives_typed() -> None:
    route = _bind_args(typed, {"route": ROUTE})["route"]
    assert isinstance(route, Route)
    assert route.origin == "a"
    assert all(isinstance(leg, Leg) for leg in route.legs)
    assert route.legs[0].at == datetime.fromisoformat("2026-01-01T10:00:00+00:00")
    assert route.legs[1].at is None
    assert isinstance(route.extra["k"], Leg)
    assert isinstance(route.home, Leg)
    assert route.tags == ("t1", "t2")


def test_defaults_optional_and_hashable_sets() -> None:
    bound = _bind_args(typed, {"route": {"origin": "a"}, "pins": [{"x": 1, "y": 2}]})
    assert bound["route"] == Route(origin="a")
    assert bound["pins"] == {Pin(1, 2)}
    assert _bind_args(typed, {"route": {"origin": "a"}, "pins": None})["pins"] is None


def test_dict_plugins_unchanged() -> None:
    payload = {"b": 2, "a": 1}
    received = _bind_args(plain, {"payload": payload})["payload"]
    assert type(received) is dict
    assert list(received.items()) == list(payload.items())


def test_unfit_record_arrives_as_admitted() -> None:
    # hydration never refuses: a value that is not the record's shape is handed over unchanged
    for value in ({"origin": "a", "bogus": 1}, {"legs": []}, ["origin"], "x"):
        assert _codec.hydrate(Route, value) == value


def test_switch_cleared_delivers_dicts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_codec, "TYPED_RECORDS", False)
    route = _bind_args(typed, {"route": ROUTE})["route"]
    assert type(route) is dict
    assert route == ROUTE
    assert _codec.hydrate(Leg, {"name": "n"}) == {"name": "n"}


def test_dataclass_through_a_real_child(tmp_path: Path) -> None:
    view, _run_dir = run_plugin(tmp_path, "dataclass_plugin", {"point": {"x": 1, "y": 2}})
    assert view.state == "succeeded"
    assert isinstance(view.summary, dict)
    assert view.summary["received"] == "Point"


# ---------------------------------------------------------------------------
# L.CK-3/4.3: the packs pipeline receives its typed stack spec; docs; the refuse variant
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Stands in for the docker StackRunner: records that `up` ran, touches no engine."""

    ups: list[object] = []

    def __init__(self, ctx: object) -> None:
        pass

    def up(self, stack: object, *, cwd: Path) -> Any:
        _FakeRunner.ups.append(stack)
        return _Result({"up": "ok"})

    def down(self, stack: object, *, cwd: Path) -> None:
        pass


class _Ctx:
    def __init__(self, work: Path) -> None:
        self.outputs = work

    def log(self, message: str) -> None:
        pass


class _Result:
    def __init__(self, payload: dict[str, Any], exit_code: int = 0) -> None:
        self.payload = payload
        self.exit_code = exit_code
        self.report_path = None

    def to_dict(self) -> dict[str, Any]:
        return self.payload


def _load_pipeline() -> ModuleType:
    path = REPO / "examples" / "packs" / "integration_pipeline.py"
    spec = importlib.util.spec_from_file_location("integration_pipeline_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _k_block(text: str, k: str) -> str:
    match = re.search(rf"<!-- {k} -->(.*?)<!-- /{k} -->", text, re.S)
    assert match is not None, f"no <!-- {k} --> block"
    return match.group(1)


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-3", "core", "core", "INSPECT", "CI")
@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-4", "core", "core", "INSPECT", "CI")
def test_pipeline_receives_typed_stack_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_pipeline()
    monkeypatch.setattr(module, "StackRunner", _FakeRunner)
    monkeypatch.setattr(module, "run_pytest", lambda spec, report_dir: _Result({"ok": True}))
    seen: list[dict[str, Any]] = []
    real_from_dict = module.StackSpec.from_dict
    monkeypatch.setattr(
        module.StackSpec,
        "from_dict",
        classmethod(lambda cls, raw: (seen.append(raw), real_from_dict(raw))[1]),
    )
    args = {
        "stack_spec": {
            "project": "p",
            "waves": [{"name": "app", "services": ["web"], "wait": "started", "timeout_s": 60}],
            "probes": {"db": {"kind": "command", "command": ["true"]}},
        },
        "workdir": str(tmp_path),
    }
    bound = _bind_args(module.integration_pipeline, args)
    assert isinstance(bound["stack_spec"], module.PipelineStack)
    assert isinstance(bound["stack_spec"].waves[0], module.PipelineWave)
    assert isinstance(bound["stack_spec"].probes["db"], module.PipelineProbe)

    result = module.integration_pipeline(_Ctx(tmp_path), **bound)
    assert result["ok"] is True
    assert len(_FakeRunner.ups) >= 1
    (raw,) = seen  # the plugin turned the typed record back into the spec it hands the runner
    assert raw["project"] == "p" and raw["waves"][0]["services"] == ["web"]

    # K-3 / K-4: one delimited block each in docs/plugins.md (MC-05)
    plugins = (REPO / "docs" / "plugins.md").read_text(encoding="utf-8")
    k3, k4 = _k_block(plugins, "K-3"), _k_block(plugins, "K-4")
    for needle in ("@dataclass", "instance", "dict"):
        assert needle in k3, needle
    for needle in ("dataclass", "JSON", "execution.result_unencodable"):
        assert needle in k4, needle
    # the workaround the typed arrival replaces is gone
    source = (REPO / "examples" / "packs" / "integration_pipeline.py").read_text(encoding="utf-8")
    assert "isinstance(stack_spec, dict)" not in source


@pytest.mark.proves("WR-PLAN-9", "WR-PLAN-9:variant-refuse-written", "core", "core", "PROC", "CI")
@pytest.mark.xfail(strict=True, reason="variant:OQ-3=refuse")
def test_variant_refuse(tmp_path: Path) -> None:
    """OQ-3 = refuse: publishing a dataclass-annotated parameter is refused rather than
    delivered. Written, not chosen (declining K-3 restores dict delivery, not this)."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    shutil.copy2(PLUGINS / "dataclass_plugin.py", plugin_dir / "dataclass_plugin.py")
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    outcome = kernel.control.run(plugin="dataclass_plugin", args={"point": {"x": 1, "y": 2}})
    assert not isinstance(outcome, RunView), "a dataclass parameter was delivered, not refused"
