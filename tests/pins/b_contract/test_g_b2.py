"""G-B2 (BFD-13): dict-annotated plugins get dicts and byte-identical JSON
(WR-COMPAT-4, permanent); dataclass-annotated params also get a dict today.
The target requires a typed instance (WR-PLAN-9, K-3 default)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pins.b_contract.kit import run_plugin
from tests.proof.markers import target_check


def _received(tmp_path: Path) -> str:
    view, _run_dir = run_plugin(tmp_path, "dataclass_plugin", {"point": {"x": 1, "y": 2}})
    assert view.state == "succeeded"
    assert isinstance(view.summary, dict)
    return str(view.summary["received"])


@pytest.mark.compat
@pytest.mark.proves("WR-COMPAT-4", "WR-COMPAT-4:preserved", "core", "core", "must", "CI")
def test_compat_dict_plugin_bytes_identical(tmp_path: Path) -> None:
    view, run_dir = run_plugin(tmp_path, "dict_plugin", {"payload": {"b": 2, "a": 1}})
    assert view.state == "succeeded"
    result = (run_dir / "evidence" / "result.json").read_bytes()
    # keys sorted at every level, compact separators; param arrived as a dict
    assert result == b'{"a":{"x":3,"y":2},"sum":{"isdict":1,"total":3},"z":{"n":1}}'


@pytest.mark.pin("G-B2")
def test_pin_dataclass_param_receives_dict(tmp_path: Path) -> None:
    assert _received(tmp_path) == "dict"


@pytest.mark.target("G-B2")
@pytest.mark.proves("WR-PLAN-9", "WR-PLAN-9:dataclass-arg-typed", "core", "core", "must", "CI")
@pytest.mark.xfail(strict=True, reason="defect:G-B2")
def test_target_dataclass_param_receives_instance(tmp_path: Path) -> None:
    received = _received(tmp_path)
    target_check(
        received == "Point",
        "G-B2",
        f"dataclass-annotated parameter received {received!r}, not a Point instance",
    )
