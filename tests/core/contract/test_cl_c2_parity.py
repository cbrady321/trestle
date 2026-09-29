"""L.CL-C2.2 (BFD-13, BFD-16, BFD-20; WR-PLAN-7): what publication admits, the
child hydrates to the annotation; dict-annotated parameters still get dicts;
the codec is not importable by plugins (exact allowlist)."""

from __future__ import annotations

import collections.abc
import enum
import itertools
import json
import textwrap
import typing
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.pins.b_contract.kit import run_plugin
from trestle.child.main import _bind_args
from trestle.child.validate import forbidden_import_error
from trestle.plugin import _codec
from trestle.server.plugin_schema import ArgsError, schemas_from_source, validate_args

_PRELUDE = textwrap.dedent(
    """
    from __future__ import annotations

    import enum
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime
    from pathlib import Path
    from typing import Annotated, Literal, Optional, TypedDict

    from trestle.plugin.surface import Context, trestle


    class Color(enum.Enum):
        RED = "red"
        BLUE = "blue"


    class Level(enum.IntEnum):
        LOW = 1
        HIGH = 2


    class Rec(TypedDict):
        n: int
    """
)

# One annotation per admitted-type kind (plus containers of each), as source text.
ANNOTATIONS = [
    "str",
    "int",
    "float",
    "bool",
    "Path",
    "datetime",
    "date",
    "Color",
    "Level",
    "Literal['a', 'b']",
    "Literal[1, 2]",
    "set[str]",
    "set[Color]",
    "frozenset[int]",
    "tuple[int, ...]",
    "tuple[Path, ...]",
    "list[Path]",
    "Sequence[date]",
    "Mapping[str, datetime]",
    "dict[str, int]",
    "dict[str, list[Level]]",
    "Optional[Path]",
    "Path | None",
    "int | str",
    "datetime | date",
    "date | None",
    "Sequence[Color] | None",
    "Annotated[Path, 'x']",
    "Rec",
    "list[Rec]",
]

_STRINGS = [
    "",
    "abc",
    "red",
    "blue",
    "a",
    "b",
    "/tmp/x y",
    "2026-01-01",
    "2026-02-30",
    "2026-01-01T00:00:00Z",
    "2026-01-01T00:00:00+02:00",
    "2026-01-01T00:00:00",
]
_ATOMS: list[Any] = [None, True, False, 0, 1, 2, 7, -3, 1.5, *_STRINGS]


def _candidates() -> list[Any]:
    """Deterministic JSON candidates: atoms, arrays and objects built from them."""
    out: list[Any] = list(_ATOMS)
    out.extend([[], *([a] for a in _ATOMS), *([a, b] for a, b in itertools.pairwise(_ATOMS))])
    out.extend([a, a] for a in (1, "a", "red"))  # duplicates: a set must not lose meaning
    out.extend([{}, *({"k": a} for a in _ATOMS), {"n": 1}, {"n": "x"}, {"a": 1, "b": 2}])
    out.extend([{"k": [1, 2]}, {"k": [1]}, [{"n": 1}], [{"n": 1}, {"n": 2}]])
    return out


def _source(annotation: str) -> str:
    return (
        _PRELUDE
        + f"\n\n@trestle\ndef fn(ctx: Context, p: {annotation}) -> dict[str, int]:"
        + "\n    return {}\n"
    )


def _plugin_fn(source: str) -> Any:
    namespace: dict[str, Any] = {"__name__": "codec_parity_plugin"}
    exec(compile(source, "codec_parity_plugin.py", "exec"), namespace)  # noqa: S102
    return namespace["fn"]


def _conforms(value: object, tp: object) -> bool:
    """Independent statement of 'this value is an instance of the annotation'."""
    if typing.get_origin(tp) is typing.Annotated:
        return _conforms(value, typing.get_args(tp)[0])
    if tp is type(None):
        return value is None
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, type(int | str)):
        return any(_conforms(value, option) for option in args)
    if origin is typing.Literal:
        # admission's enum test is `value in enum`, so True/1.0 pass for Literal[1]: the
        # value arrives as it was admitted (recorded in CL-C2-PART1-RETURN.md)
        return any(value == lit for lit in args)
    if origin is tuple:
        return isinstance(value, tuple) and all(_conforms(item, args[0]) for item in value)
    if origin in (list, collections.abc.Sequence):
        return isinstance(value, list) and all(_conforms(item, args[0]) for item in value)
    if origin in (set, frozenset):
        return isinstance(value, origin) and all(_conforms(item, args[0]) for item in value)
    if origin in (dict, collections.abc.Mapping):
        return isinstance(value, dict) and all(_conforms(item, args[1]) for item in value.values())
    if tp is bool:
        return isinstance(value, bool)
    if tp is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if tp is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tp is datetime:
        return isinstance(value, datetime) and value.tzinfo is not None
    if tp is date:
        return type(value) is date
    if isinstance(tp, type) and typing.is_typeddict(tp):
        return isinstance(value, dict)
    if isinstance(tp, type):
        return isinstance(value, tp)
    raise AssertionError(f"checker has no rule for {tp!r}")


@pytest.mark.proves(
    "WR-PLAN-7",
    "WR-PLAN-7:admitted-values-same-meaning",
    "core",
    "core",
    "must",
    "CI",
)
@pytest.mark.parametrize("annotation", ANNOTATIONS)
def test_every_admitted_type_arrives_with_same_meaning(annotation: str) -> None:
    """Property: every value validate_args admits for the parameter reaches the
    plugin as an instance of the annotation, and admitted values that hold no
    typed meaning (primitives, dicts) arrive byte-identical."""
    source = _source(annotation)
    fn = _plugin_fn(source)
    schema, _ = schemas_from_source(source)
    declared = typing.get_type_hints(fn)["p"]
    admitted = 0
    for candidate in _candidates():
        args = {"p": candidate}
        try:
            validate_args(args, schema)
        except ArgsError:
            continue
        admitted += 1
        received = _bind_args(fn, args)["p"]
        assert _conforms(received, declared), (annotation, candidate, received)
        if isinstance(candidate, (str, int, float, bool, type(None))) and not _typed(declared):
            assert received == candidate
            assert json.dumps(received) == json.dumps(candidate)
    assert admitted > 0, f"no candidate admitted for {annotation}: the generator lost coverage"


def _typed(tp: object) -> bool:
    """True when the annotation names a type whose instances are not JSON primitives."""
    return bool(
        _contains_typed(tp)
        and not (isinstance(tp, type) and tp in (str, int, float, bool, type(None)))
    )


def _contains_typed(tp: object) -> bool:
    if typing.get_origin(tp) is typing.Annotated:
        return _contains_typed(typing.get_args(tp)[0])
    origin = typing.get_origin(tp)
    if origin is not None:
        return any(_contains_typed(arg) for arg in typing.get_args(tp))
    return isinstance(tp, type) and tp not in (str, int, float, bool, type(None))


def test_codec_hydrate_argument_order_and_unfit_values() -> None:
    assert _codec.hydrate(Path, "a/b") == Path("a/b")
    assert _codec.hydrate(date, "2026-01-01") == date(2026, 1, 1)
    # a value that does not fit is returned untouched, never refused here
    assert _codec.hydrate(int, "seven") == "seven"
    assert _codec.hydrate(datetime, "2026-01-01T00:00:00") == "2026-01-01T00:00:00"


def test_two_way_union_picks_the_option_the_value_belongs_to() -> None:
    stamp = _codec.hydrate(datetime | date, "2026-01-01T10:00:00+00:00")
    assert type(stamp) is datetime
    assert type(_codec.hydrate(datetime | date, "2026-01-01")) is date
    assert _codec.hydrate(int | str, "x") == "x"
    assert _codec.hydrate(int | str, 3) == 3


def test_enum_by_value_and_by_member_name() -> None:
    class Mode(enum.Enum):
        FAST = enum.auto()
        SLOW = enum.auto()

    assert _codec.hydrate(Mode, "FAST") is Mode.FAST  # a non-constant member is named by its name
    assert _codec.hydrate(enum.Enum("E", {"A": "a"}), "a").name == "A"


def test_dict_annotated_gets_dict() -> None:
    fn = _plugin_fn(_source("dict[str, int]"))
    payload = {"b": 2, "a": 1}
    received = _bind_args(fn, {"p": payload})["p"]
    assert type(received) is dict
    assert list(received.items()) == list(payload.items())


@pytest.mark.proves(
    "WR-PLAN-7",
    "WR-PLAN-7:dict-annotated-gets-dict",
    "core",
    "core",
    "must",
    "CI",
)
def test_dict_and_typeddict_params_stay_dicts_through_the_child(tmp_path: Path) -> None:
    """End to end: the child process hands a dict-annotated plugin a dict."""
    view, run_dir = run_plugin(tmp_path, "dict_plugin", {"payload": {"b": 2, "a": 1}})
    assert view.state == "succeeded"
    result = json.loads((run_dir / "evidence" / "result.json").read_bytes())
    assert result["sum"]["isdict"] == 1


def test_typed_values_arrive_typed_through_the_child(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    source = _PRELUDE + textwrap.dedent(
        """

            @trestle
            def kinds(
                ctx: Context,
                color: Color,
                where: Path,
                when: datetime,
                day: date,
                tags: set[str],
                pair: tuple[int, ...],
                maybe: Optional[Path] = None,
            ) -> dict[str, str]:
                return {
                    "color": type(color).__name__,
                    "where": type(where).__name__,
                    "when": type(when).__name__ + str(when.utcoffset()),
                    "day": type(day).__name__,
                    "tags": type(tags).__name__,
                    "pair": type(pair).__name__,
                    "maybe": type(maybe).__name__,
                }
            """
    )
    (plugin_dir / "kinds.py").write_text(source, encoding="utf-8")
    from tests.proof import harness, tolerances
    from trestle.common.types import RunView
    from trestle.server.ledger import run_dir_for

    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    view = kernel.control.run(
        plugin="kinds",
        args={
            "color": "red",
            "where": "/tmp/x",
            "when": "2026-01-01T00:00:00+02:00",
            "day": "2026-01-01",
            "tags": ["a", "b"],
            "pair": [1, 2],
            "maybe": "/tmp/y",
        },
        wait_ms=tolerances.HARNESS_WAIT_MS,
    )
    assert isinstance(view, RunView), view
    assert view.state == "succeeded", view
    result = json.loads(
        (run_dir_for(kernel.home, view.run_id) / "evidence" / "result.json").read_bytes()
    )
    assert result == {
        "color": "Color",
        "where": "PosixPath",
        "when": "datetime2:00:00",
        "day": "date",
        "tags": "set",
        "pair": "tuple",
        "maybe": "PosixPath",
    }


@pytest.mark.parametrize(
    "line",
    [
        "from trestle.plugin._codec import hydrate",
        "import trestle.plugin._codec",
        "import trestle.plugin._codec as codec",
        "from trestle.plugin import _codec",
        "from trestle.plugin import _codec as codec, Context",
    ],
)
def test_codec_module_unimportable_by_plugins(line: str) -> None:
    error = forbidden_import_error(line + "\n")
    assert error is not None, line
    assert "_codec" in error


@pytest.mark.parametrize(
    "line",
    [
        "from trestle.plugin import Context, trestle",
        "from trestle.plugin import surface",
        "from trestle.plugin.surface import Context, trestle",
        "import trestle.plugin",
        "import trestle.plugin.surface as surface",
    ],
)
def test_surface_imports_still_allowed(line: str) -> None:
    assert forbidden_import_error(line + "\n") is None, line


@pytest.mark.parametrize(
    "line",
    [
        "from trestle.workflow import x",
        "import trestle.server.admission",
        "from trestle import plugin",
    ],
)
def test_other_trestle_modules_still_refused(line: str) -> None:
    assert forbidden_import_error(line + "\n") is not None, line
