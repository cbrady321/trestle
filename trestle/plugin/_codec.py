"""Foundation codec: the publication subset, as the child sees it (MC-CORE-08).

Not part of the plugin surface. `child/validate.py` refuses a plugin import of
this module because its allowlist is exact (`trestle.plugin`,
`trestle.plugin.surface`); the child and the server import it, plugins do not.

`hydrate(annotation, value)` turns one admitted JSON value into its
annotation: Enum, Literal, set/frozenset, tuple, Path, date/datetime, two-way
unions, Sequence/Mapping, and (behind `TYPED_RECORDS`, K-3) dataclass records,
recursively. `dict` and `TypedDict` pass through as dicts, and so do Pydantic
records and, with `TYPED_RECORDS` cleared, dataclasses (S0). A value that does
not fit its annotation is returned unchanged: publication and admission decide
what is admitted, so hydration never invents a refusal of its own (a record's
own `__post_init__` is the plugin's code and may raise).

`encode(value, declared_return)` is the one encoder for a plugin's return
(WR-EVID-5). It accepts exactly JSON values plus the admitted-subset types that
have a JSON form: an Enum member (its value), a `Path` (its string), an aware
`datetime` and a `date` (ISO 8601), and a set/frozenset only where the
declared return names one (written as a sorted array, so the bytes never depend
on hash order). Anything else (a non-string key, a generator, an undeclared set,
`bytes`, a Pydantic record) raises `Unencodable`, which the child records as
`execution.result_unencodable`. Behind `TYPED_RECORDS` (K-4) a dataclass
instance also encodes, as the JSON object of its fields; with it cleared a
dataclass is refused like any other non-JSON value.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import enum
import json
import sys
import types
import typing
from datetime import date, datetime
from pathlib import Path, PurePath

_LIST_ORIGINS = (list, collections.abc.Sequence, collections.abc.MutableSequence)
_DICT_ORIGINS = (dict, collections.abc.Mapping, collections.abc.MutableMapping)
_SET_ORIGINS = (set, collections.abc.Set, collections.abc.MutableSet)

# MC-CORE-12 switch of the CK-3/4 merge (K-3, OQ-3 recorded default). Set: a
# dataclass-annotated argument arrives as that dataclass. Cleared: it arrives
# as a dict, as at S0. Every behaviour the merge adds here reads it.
TYPED_RECORDS: bool = True


class _NoFit(Exception):
    """Internal: this value is not a member of this annotation."""


def hydrate(annotation: object, value: object) -> object:
    """Return `value` hydrated to `annotation`; unchanged when it does not fit."""
    try:
        return _hydrate(annotation, value)
    except _NoFit:
        return value


def _hydrate(tp: object, value: object) -> object:
    tp = _unwrap(tp)
    if tp is None or tp is type(None):
        return _require(value is None, value)
    if tp is typing.Any or tp is object or isinstance(tp, typing.TypeVar):
        return value
    origin = typing.get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        return _union(typing.get_args(tp), value)
    if origin is typing.Literal:
        return _require(any(_same(value, lit) for lit in typing.get_args(tp)), value)
    if origin is not None:
        return _generic(origin, typing.get_args(tp), value)
    if isinstance(tp, type):
        return _plain(tp, value)
    return value


def _unwrap(tp: object) -> object:
    while True:
        if typing.get_origin(tp) is typing.Annotated:
            tp = typing.get_args(tp)[0]
        elif isinstance(tp, typing.TypeAliasType):
            tp = tp.__value__
        else:
            return tp


def _require(ok: bool, value: object) -> object:
    if not ok:
        raise _NoFit
    return value


def _same(value: object, literal: object) -> bool:
    return type(value) is type(literal) and value == literal


def _union(options: tuple[object, ...], value: object) -> object:
    for option in options:
        try:
            return _hydrate(option, value)
        except _NoFit:
            continue
    raise _NoFit


def _generic(origin: object, args: tuple[object, ...], value: object) -> object:
    if origin in _LIST_ORIGINS:
        items = _sequence(value)
        return [_item(args, item) for item in items]
    if origin in _SET_ORIGINS or origin is frozenset:
        items = _sequence(value)
        made = [_item(args, item) for item in items]
        try:
            return frozenset(made) if origin is frozenset else set(made)
        except TypeError:  # unhashable members (a set of records): keep the array
            return made
    if origin is tuple:
        items = _sequence(value)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_hydrate(args[0], item) for item in items)
        if len(args) != len(items):
            raise _NoFit
        return tuple(_hydrate(arg, item) for arg, item in zip(args, items, strict=True))
    if origin in _DICT_ORIGINS:
        if not isinstance(value, dict):
            raise _NoFit
        inner = args[1] if len(args) == 2 else typing.Any
        return {key: _hydrate(inner, item) for key, item in value.items()}
    return value


def _item(args: tuple[object, ...], item: object) -> object:
    return _hydrate(args[0], item) if args else item


def _sequence(value: object) -> list[object]:
    if not isinstance(value, (list, tuple)):
        raise _NoFit
    return list(value)


def _plain(tp: type, value: object) -> object:
    if tp is bool:
        return _require(isinstance(value, bool), value)
    if tp is int:
        return _require(isinstance(value, int) and not isinstance(value, bool), value)
    if tp is float:
        return _require(isinstance(value, (int, float)) and not isinstance(value, bool), value)
    if tp is str:
        return _require(isinstance(value, str), value)
    if issubclass(tp, enum.Enum):
        return _enum(tp, value)
    if issubclass(tp, datetime):
        return _datetime(value)
    if issubclass(tp, date):
        return _date(value)
    if issubclass(tp, Path):
        if not isinstance(value, str):
            raise _NoFit
        return Path(value)
    if tp is dict or typing.is_typeddict(tp):
        return _require(isinstance(value, dict), value)
    if tp is list:
        return list(_sequence(value))
    if tp is tuple:
        return tuple(_sequence(value))
    if TYPED_RECORDS and dataclasses.is_dataclass(tp):
        return _dataclass(tp, value)
    return value


def _dataclass(tp: type, value: object) -> object:
    """One admitted JSON object as the dataclass `tp`, each field hydrated to its annotation."""
    if not TYPED_RECORDS:
        return value
    if not isinstance(value, dict):
        raise _NoFit
    fields = {f.name: f for f in dataclasses.fields(tp) if f.init}
    if not set(value) <= set(fields):
        raise _NoFit
    for name, field in fields.items():
        no_default = field.default is dataclasses.MISSING
        if name not in value and no_default and field.default_factory is dataclasses.MISSING:
            raise _NoFit
    types_by_name = _field_types(tp)
    return tp(**{name: _hydrate(types_by_name[name], item) for name, item in value.items()})


def _field_types(tp: type) -> dict[str, object]:
    """Resolved annotation per init field; a field whose annotation cannot be resolved (a
    TYPE_CHECKING-only import) is `Any`, so its value arrives as admitted."""
    if not TYPED_RECORDS:
        return {}
    try:
        hints: dict[str, object] = dict(typing.get_type_hints(tp))
    except Exception:  # noqa: BLE001 - one bad forward reference must not fail the record
        hints = {}
    namespace = dict(vars(sys.modules[tp.__module__])) if tp.__module__ in sys.modules else {}
    resolved: dict[str, object] = {}
    for field in dataclasses.fields(tp):
        annotation = hints.get(field.name, field.type)
        if isinstance(annotation, str):
            try:
                annotation = eval(annotation, namespace, {tp.__name__: tp})  # noqa: S307
            except Exception:  # noqa: BLE001
                annotation = typing.Any
        resolved[field.name] = annotation
    return resolved


def _enum(tp: type[enum.Enum], value: object) -> object:
    try:
        return tp(value)
    except ValueError:
        pass
    if isinstance(value, str) and value in tp.__members__:
        return tp[value]
    raise _NoFit


def _datetime(value: object) -> datetime:
    # Same meaning as admission (plugin_schema.parse_iso_datetime): a zone is
    # required, so a bare date string belongs to a `date` option, never here.
    if not isinstance(value, str) or not value.isascii():
        raise _NoFit
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _NoFit from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _NoFit
    return parsed


def _date(value: object) -> date:
    if not isinstance(value, str) or not value.isascii():
        raise _NoFit
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _NoFit from exc


class Unencodable(TypeError):
    """A plugin's return value has no JSON form (MC-CORE-04 `execution.result_unencodable`)."""


def encode(value: object, declared_return: object = None) -> bytes:
    """The canonical JSON bytes of a plugin return: compact, keys sorted, UTF-8."""
    try:
        return json.dumps(
            normalize(value, declared_return),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Unencodable:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise Unencodable(f"{type(exc).__name__}: {exc}") from exc


def normalize(value: object, declared_return: object = None) -> object:
    """`value` as plain JSON data (dict with str keys, list, str, number, bool, None).

    `declared_return` is the entry point's return annotation, or None when it
    is unknown; only a set the annotation names is written as an array.
    """
    return _norm(value, _unwrap(declared_return) if declared_return is not None else None)


def _norm(value: object, ann: object) -> object:
    if isinstance(value, enum.Enum):
        return _norm(value.value, None)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    ann = _resolve(ann, value)
    if TYPED_RECORDS and dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _record(value)
    if isinstance(value, dict):
        inner = _arg(ann, 1)
        for key in value:
            if not isinstance(key, str):
                raise Unencodable(f"dict key of type {type(key).__name__} is not a string")
        return {key: _norm(item, inner) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(item, inner) for item, inner in _elements(ann, value)]
    if isinstance(value, (set, frozenset)):
        if not _declares_set(ann):
            raise Unencodable(f"{type(value).__name__} is not declared as a set in the return hint")
        return _sorted([_norm(item, _arg(ann, 0)) for item in value])
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise Unencodable("naive datetime has no unambiguous JSON form; give it a timezone")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    raise Unencodable(f"{type(value).__name__} has no JSON form")


def _record(value: object) -> dict[str, object]:
    """A dataclass instance as the JSON object of its fields (K-4), each field written
    against its own annotation so a declared set field is written as a sorted array."""
    if not TYPED_RECORDS:
        raise Unencodable(f"{type(value).__name__} has no JSON form")
    types_by_name = _field_types(type(value))
    return {
        f.name: _norm(getattr(value, f.name), _unwrap(types_by_name.get(f.name)))
        for f in dataclasses.fields(value)  # type: ignore[arg-type]
    }


def _resolve(ann: object, value: object) -> object:
    """The option of a declared union that `value` belongs to (None when unknown)."""
    if ann is None:
        return None
    ann = _unwrap(ann)
    origin = typing.get_origin(ann)
    if origin is typing.Union or origin is types.UnionType:
        for option in typing.get_args(ann):
            option = _unwrap(option)
            head = typing.get_origin(option) or option
            try:
                if isinstance(value, head):  # type: ignore[arg-type]
                    return option
            except TypeError:
                continue
        return None
    return ann


def _arg(ann: object, index: int) -> object:
    args = typing.get_args(ann) if ann is not None else ()
    return args[index] if len(args) > index else None


def _elements(ann: object, value: list[object] | tuple[object, ...]) -> list[tuple[object, object]]:
    args = typing.get_args(ann) if ann is not None else ()
    if typing.get_origin(ann) is tuple and args and args[-1] is not Ellipsis:
        if len(args) == len(value):
            return list(zip(value, args, strict=True))
        return [(item, None) for item in value]
    inner = args[0] if args else None
    return [(item, inner) for item in value]


def _declares_set(ann: object) -> bool:
    head = typing.get_origin(ann) or ann
    return head is frozenset or head in _SET_ORIGINS


def _sorted(members: list[object]) -> list[object]:
    try:
        return sorted(members)  # type: ignore[type-var]
    except TypeError:  # mixed or unordered members: order by their canonical bytes
        return sorted(
            members, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False)
        )
