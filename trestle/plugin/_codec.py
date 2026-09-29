"""Foundation codec: the publication subset, as the child sees it (MC-CORE-08).

Not part of the plugin surface. `child/validate.py` refuses a plugin import of
this module because its allowlist is exact (`trestle.plugin`,
`trestle.plugin.surface`); the child and the server import it, plugins do not.

`hydrate(annotation, value)` turns one admitted JSON value into its
annotation: Enum, Literal, set/frozenset, tuple, Path, date/datetime, two-way
unions, Sequence/Mapping. `dict` and `TypedDict` pass through as dicts, and so
do dataclass and Pydantic records (a typed-record return is a later,
switch-guarded step). A value that does not fit its annotation is returned
unchanged: publication and admission decide what is admitted, so hydration
never invents a refusal of its own.
"""

from __future__ import annotations

import collections.abc
import enum
import types
import typing
from datetime import date, datetime
from pathlib import Path

_LIST_ORIGINS = (list, collections.abc.Sequence, collections.abc.MutableSequence)
_DICT_ORIGINS = (dict, collections.abc.Mapping, collections.abc.MutableMapping)
_SET_ORIGINS = (set, collections.abc.Set, collections.abc.MutableSet)


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
    return value


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
