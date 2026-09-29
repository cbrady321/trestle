"""L.CK-3/4.1 (BFD-13; K-3, OQ-3 recorded default): a dataclass-annotated argument arrives as
that dataclass, recursively, behind the `_codec.TYPED_RECORDS` switch; dict-annotated plugins
are unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

from tests.pins.b_contract.kit import run_plugin
from trestle.child.main import _bind_args
from trestle.plugin import _codec


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
