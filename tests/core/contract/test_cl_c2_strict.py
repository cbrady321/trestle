"""L.CL-C2.4 (WR-EVID-5, A9.3): a plugin's return is encoded by one strict encoder.

A value with no JSON form ends the run as `execution.result_unencodable` (recorded through
the child error path) and leaves no `result.json`, no `.tmp`. A declared set is written
sorted, whatever the hash seed. Enum, Path, date and aware datetime values encode to their
JSON form. Today's accepted shapes keep their bytes (WR-COMPAT-4).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import harness, tolerances
from trestle.child.serialize import ResultTooLarge, write_result
from trestle.common import codes
from trestle.common.types import RunView
from trestle.plugin import _codec
from trestle.server.ledger import run_dir_for

REPO = Path(__file__).resolve().parents[3]

_PRELUDE = textwrap.dedent(
    """
    from __future__ import annotations

    import enum
    from dataclasses import dataclass
    from datetime import UTC, date, datetime
    from pathlib import Path

    from trestle.plugin.surface import Context, trestle


    class Color(enum.Enum):
        RED = "red"


    class Level(enum.IntEnum):
        HIGH = 3


    @dataclass
    class Point:
        x: int
    """
)


def _run(tmp_path: Path, hint: str, body: str) -> tuple[RunView, Path]:
    """Publish `def fn(ctx) -> <hint>` with `body` as its return statement and run it."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    source = _PRELUDE + f"\n\n@trestle\ndef fn(ctx: Context) -> {hint}:\n    {body}\n"
    (plugin_dir / "fn.py").write_text(source, encoding="utf-8")
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    view = kernel.control.run(plugin="fn", args={}, wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(view, RunView), view
    return view, run_dir_for(kernel.home, view.run_id)


def _evidence(run_dir: Path) -> Path:
    return run_dir / "evidence"


@pytest.mark.proves(
    "WR-EVID-5",
    "WR-EVID-5:non-json-never-complete",
    "core",
    "core",
    "PROC",
    "CI",
)
@pytest.mark.proves(
    "WR-EVID-5",
    "WR-EVID-5:no-tmp-remains",
    "core",
    "core",
    "PROC",
    "CI",
)
@pytest.mark.parametrize(
    ("hint", "body"),
    [
        pytest.param("list[int]", "return {1, 2, 3}", id="undeclared-set"),
        pytest.param("dict[str, int]", "return {1: 2}", id="int-keyed-dict"),
        pytest.param("list[int]", "return (i for i in range(3))", id="generator"),
        pytest.param("list[int]", "return b'raw'", id="bytes"),
        pytest.param("dict[str, int]", "return {'k': {1, 2}}", id="nested-undeclared-set"),
        pytest.param("datetime", "return datetime(2026, 1, 1)", id="naive-datetime"),
        pytest.param("Point", "return object()", id="arbitrary-object"),
        pytest.param("float", "return float('nan')", id="nan"),
    ],
)
def test_non_json_results_are_execution_errors_without_tmp(
    tmp_path: Path, hint: str, body: str
) -> None:
    view, run_dir = _run(tmp_path, hint, body)
    assert view.state == "failed", view
    (row,) = support.rows_of(run_dir, "error_record")
    assert row["code"] == codes.EXECUTION_RESULT_UNENCODABLE and row["phase"] == "encode"
    assert view.error is not None and view.error["code"] == codes.EXECUTION_RESULT_UNENCODABLE
    evidence = _evidence(run_dir)
    assert not (evidence / "result.json").exists()
    assert not (evidence / "result.index").exists()
    assert not list(evidence.glob("*.tmp"))


@pytest.mark.proves(
    "WR-EVID-5",
    "WR-EVID-5:set-order-deterministic",
    "core",
    "core",
    "PROC",
    "CI",
)
def test_declared_set_order_stable(tmp_path: Path) -> None:
    view, run_dir = _run(tmp_path, "set[str]", "return {'pear', 'apple', 'fig', 'banana'}")
    assert view.state == "succeeded", view
    written = (_evidence(run_dir) / "result.json").read_bytes()
    assert written == b'["apple","banana","fig","pear"]'
    # the bytes do not depend on the interpreter's hash seed
    probe = (
        "from trestle.plugin import _codec\n"
        "import sys\n"
        "members = {'pear', 'apple', 'fig', 'banana', 'kiwi', 'plum', 'lime'}\n"
        "sys.stdout.buffer.write(_codec.encode(members, set[str]))\n"
    )
    outputs = set()
    for seed in ("0", "1", "2", "12345"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(REPO)
        done = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            env=env,
            cwd=REPO,
            timeout=tolerances.HARNESS_WAIT_MS / 1000,
            check=True,
        )
        outputs.add(done.stdout)
    assert outputs == {b'["apple","banana","fig","kiwi","lime","pear","plum"]'}


def test_declared_sets_inside_containers_and_frozenset() -> None:
    assert _codec.encode({"b": {3, 1, 2}}, dict[str, set[int]]) == b'{"b":[1,2,3]}'
    assert _codec.encode([{2, 1}], list[set[int]]) == b"[[1,2]]"
    assert _codec.encode(frozenset({"y", "x"}), frozenset[str]) == b'["x","y"]'
    assert _codec.encode({"z", 1}, set[int | str]) == b'["z",1]'
    with pytest.raises(_codec.Unencodable):
        _codec.encode({"b": {1}}, dict[str, list[int]])
    with pytest.raises(_codec.Unencodable):
        _codec.encode({1, 2}, None)


@pytest.mark.parametrize(
    ("hint", "body", "expected"),
    [
        pytest.param("Color", "return Color.RED", b'"red"', id="enum"),
        pytest.param("Level", "return Level.HIGH", b"3", id="int-enum"),
        pytest.param("Path", "return Path('/tmp/x')", b'"/tmp/x"', id="path"),
        pytest.param("date", "return date(2026, 1, 2)", b'"2026-01-02"', id="date"),
        pytest.param(
            "datetime",
            "return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)",
            b'"2026-01-02T03:04:05+00:00"',
            id="datetime",
        ),
        pytest.param(
            "dict[str, list[Color]]",
            "return {'b': [Color.RED], 'a': []}",
            b'{"a":[],"b":["red"]}',
            id="nested",
        ),
    ],
)
def test_enum_path_datetime_returns_encode(
    tmp_path: Path, hint: str, body: str, expected: bytes
) -> None:
    view, run_dir = _run(tmp_path, hint, body)
    assert view.state == "succeeded", view
    evidence = _evidence(run_dir)
    assert (evidence / "result.json").read_bytes() == expected
    assert not list(evidence.glob("*.tmp"))
    assert "error_record" not in support.kinds(run_dir)


_SHAPES: list[Any] = [
    None,
    True,
    42,
    3.14,
    -0.0,
    10**20,
    "héllo ☃",
    {"b": 2, "a": 1},
    [1, 2, 3],
    (1, "two", None),
    {"nested": {"items": [1, {"k": "v"}], "t": (1, 2)}},
    {"z": [], "y": {}, "x": ""},
]


@pytest.mark.parametrize("value", _SHAPES)
def test_encoder_and_streaming_writer_agree_byte_for_byte(tmp_path: Path, value: Any) -> None:
    """The one encoder and the file writer produce the same bytes, and for a value
    that never needed conversion they are what json.dumps' canonical form gives."""
    path = tmp_path / "result.json"
    write_result(path, value)
    assert path.read_bytes() == _codec.encode(value)
    if not isinstance(value, float):  # the writer keeps float's default separators
        plain = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        assert path.read_bytes() == plain


def test_failed_write_leaves_neither_result_nor_tmp(tmp_path: Path) -> None:
    for value in ({1: 2}, {"late": [1, float("inf")]}, math.nan, {"g": (i for i in ())}):
        path = tmp_path / "result.json"
        with pytest.raises(Exception):  # noqa: B017 - NonCanonical or Unencodable, either fails
            write_result(path, value)
        assert not path.exists() and not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ResultTooLarge):
        write_result(tmp_path / "result.json", ["x" * 100], max_bytes=10)
    assert not list(tmp_path.iterdir())
