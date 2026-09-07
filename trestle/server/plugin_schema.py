"""JSON Schema from @trestle type hints.

Canonical path: annotation AST → type AST → JSON Schema → schema hash.
Derivation lives in the foundation; plugins do not author schemas.
"""

from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn, cast

from trestle.common.canonical import canonical_json
from trestle.common.fsutil import sha256_bytes

SUBSET_POINTER = "docs/plugins.md"
MAX_OBJECT_DEPTH = 5

_SCALAR_NAMES = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
}
_PATH_NAMES = frozenset({"Path"})
_DATETIME_NAMES = frozenset({"datetime"})
_DATE_NAMES = frozenset({"date"})
_ARTIFACT_NAMES = frozenset({"ArtifactRef"})
_CONTEXT_NAMES = frozenset({"Context"})
_REJECT_NAMES = frozenset({"bytes", "bytearray", "memoryview", "Any", "object", "Callable"})
_LIST_NAMES = frozenset({"list", "List", "Sequence"})
_SET_NAMES = frozenset({"set", "Set", "FrozenSet", "frozenset"})
_DICT_NAMES = frozenset({"dict", "Dict", "Mapping", "MutableMapping"})
_TUPLE_NAMES = frozenset({"tuple", "Tuple"})
_ENUM_BASES = frozenset({"Enum", "StrEnum", "IntEnum", "Flag", "IntFlag"})
_PYDANTIC_BASES = frozenset({"BaseModel"})
_TYPED_DICT_BASES = frozenset({"TypedDict"})
_DATACLASS_DECOS = frozenset({"dataclass"})


class SchemaError(ValueError):
    """Plugin type is outside the documented subset."""


@dataclass(frozen=True)
class TScalar:
    kind: Literal[
        "str",
        "int",
        "float",
        "bool",
        "null",
        "path",
        "datetime",
        "date",
        "artifact_ref",
    ]


@dataclass(frozen=True)
class TLiteral:
    values: tuple[object, ...]


@dataclass(frozen=True)
class TEnum:
    values: tuple[object, ...]


@dataclass(frozen=True)
class TList:
    item: TypeNode


@dataclass(frozen=True)
class TSet:
    item: TypeNode


@dataclass(frozen=True)
class TDict:
    value: TypeNode


@dataclass(frozen=True)
class TOptional:
    inner: TypeNode


@dataclass(frozen=True)
class TUnion:
    options: tuple[TypeNode, ...]


@dataclass(frozen=True)
class TObject:
    properties: tuple[tuple[str, TypeNode], ...]
    required: tuple[str, ...]


type TypeNode = TScalar | TLiteral | TEnum | TList | TSet | TDict | TOptional | TUnion | TObject

type _FnDef = ast.FunctionDef | ast.AsyncFunctionDef


def input_schema_from_source(
    source: str,
    *,
    source_path: Path | None = None,
) -> dict[str, object]:
    """Return JSON Schema for one plugin's non-ctx parameters."""
    input_schema, _return_schema = schemas_from_source(source, source_path=source_path)
    return input_schema


def return_schema_from_source(
    source: str,
    *,
    source_path: Path | None = None,
) -> dict[str, object]:
    """Return JSON Schema for one plugin's return annotation (R-PLUG-9)."""
    _input_schema, return_schema = schemas_from_source(source, source_path=source_path)
    return return_schema


def schemas_from_source(
    source: str,
    *,
    source_path: Path | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Derive input and return JSON Schema from one @trestle function."""
    tree = ast.parse(source)
    fn = find_trestle_function(tree)
    if fn is None:
        _reject("no @trestle entry point found")
    assert fn is not None
    resolver = _Resolver(tree, source_path=source_path)
    properties: dict[str, TypeNode] = {}
    required: list[str] = []
    for name, annotation, has_default in _iter_params(fn):
        if _is_ctx_param(name, annotation, resolver):
            continue
        if annotation is None:
            _reject(f"missing type hint for parameter {name!r}")
        node = resolver.resolve(annotation, depth=MAX_OBJECT_DEPTH, stack=())
        properties[name] = node
        if not has_default:
            required.append(name)
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {key: type_node_to_schema(val) for key, val in properties.items()},
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = required
    if fn.returns is None:
        _reject("missing return type hint")
    return_node = resolver.resolve(fn.returns, depth=MAX_OBJECT_DEPTH, stack=())
    return input_schema, type_node_to_schema(return_node)


def schema_digest(schema: dict[str, object]) -> str:
    return sha256_bytes(canonical_json(schema))


class ArgsError(ValueError):
    """Instance does not match the derived input_schema."""


_DESCRIBE_HINT = "call describe_plugin"


def validate_args(args: dict[str, object], schema: dict[str, object]) -> None:
    """Refuse values that do not match snapshot JSON Schema (Admit after pick)."""
    _match(args, schema, path="")


def _match(value: object, schema: dict[str, object], *, path: str) -> None:
    loc = path if path else "args"
    raw_any = schema.get("anyOf")
    if isinstance(raw_any, list):
        for option in raw_any:
            if isinstance(option, dict):
                try:
                    _match(value, option, path=path)
                    return
                except ArgsError:
                    continue
        raise ArgsError(f"invalid {loc}; {_DESCRIBE_HINT}")
    raw_enum = schema.get("enum")
    if isinstance(raw_enum, list):
        if value not in raw_enum:
            raise ArgsError(f"invalid {loc}: not in enum; {_DESCRIBE_HINT}")
        return
    expected = schema.get("type")
    if expected == "object":
        _match_object(value, schema, path=path)
        return
    if expected == "array":
        _match_array(value, schema, path=path)
        return
    if expected == "string":
        if not isinstance(value, str):
            raise ArgsError(f"invalid {loc}: expected string; {_DESCRIBE_HINT}")
        return
    if expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ArgsError(f"invalid {loc}: expected integer; {_DESCRIBE_HINT}")
        return
    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ArgsError(f"invalid {loc}: expected number; {_DESCRIBE_HINT}")
        return
    if expected == "boolean":
        if not isinstance(value, bool):
            raise ArgsError(f"invalid {loc}: expected boolean; {_DESCRIBE_HINT}")
        return
    if expected == "null":
        if value is not None:
            raise ArgsError(f"invalid {loc}: expected null; {_DESCRIBE_HINT}")
        return
    raise ArgsError(f"invalid {loc}; {_DESCRIBE_HINT}")


def _match_object(value: object, schema: dict[str, object], *, path: str) -> None:
    loc = path if path else "args"
    if not isinstance(value, dict):
        raise ArgsError(f"invalid {loc}: expected object; {_DESCRIBE_HINT}")
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if not isinstance(key, str) or key not in value:
                label = key if isinstance(key, str) else loc
                raise ArgsError(f"missing required arg {label!r}; {_DESCRIBE_HINT}")
    props = schema.get("properties", {})
    additional = schema.get("additionalProperties", True)
    prop_map = props if isinstance(props, dict) else {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ArgsError(f"invalid {loc}: non-str key; {_DESCRIBE_HINT}")
        child = key if not path else f"{path}.{key}"
        nested = prop_map.get(key)
        if isinstance(nested, dict):
            _match(item, nested, path=child)
        elif additional is False:
            raise ArgsError(f"unexpected arg {key!r}; {_DESCRIBE_HINT}")
        elif isinstance(additional, dict):
            _match(item, additional, path=child)


def _match_array(value: object, schema: dict[str, object], *, path: str) -> None:
    loc = path if path else "args"
    if not isinstance(value, list):
        raise ArgsError(f"invalid {loc}: expected array; {_DESCRIBE_HINT}")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _match(item, items, path=f"{loc}[{index}]")
    if schema.get("uniqueItems") is True:
        keys = [canonical_json(item) for item in value]
        if len(keys) != len(set(keys)):
            raise ArgsError(f"invalid {loc}: values must be unique; {_DESCRIBE_HINT}")


def find_trestle_function(tree: ast.AST) -> _FnDef | None:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _has_trestle_decorator(node):
            return node
    return None


def type_node_to_schema(node: TypeNode) -> dict[str, object]:
    if isinstance(node, TScalar):
        return _scalar_schema(node.kind)
    if isinstance(node, TLiteral):
        return {"enum": list(node.values)}
    if isinstance(node, TEnum):
        return {"enum": list(node.values)}
    if isinstance(node, TList):
        return {"type": "array", "items": type_node_to_schema(node.item)}
    if isinstance(node, TSet):
        return {
            "type": "array",
            "items": type_node_to_schema(node.item),
            "uniqueItems": True,
        }
    if isinstance(node, TDict):
        return {
            "type": "object",
            "additionalProperties": type_node_to_schema(node.value),
        }
    if isinstance(node, TOptional):
        return {"anyOf": [type_node_to_schema(node.inner), {"type": "null"}]}
    if isinstance(node, TUnion):
        return {"anyOf": [type_node_to_schema(opt) for opt in node.options]}
    props = {key: type_node_to_schema(val) for key, val in node.properties}
    out: dict[str, object] = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }
    if node.required:
        out["required"] = list(node.required)
    return out


def _scalar_schema(kind: str) -> dict[str, object]:
    if kind == "str":
        return {"type": "string"}
    if kind == "int":
        return {"type": "integer"}
    if kind == "float":
        return {"type": "number"}
    if kind == "bool":
        return {"type": "boolean"}
    if kind == "null":
        return {"type": "null"}
    if kind == "path":
        return {"type": "string", "description": "filesystem path (not an artifact handle)"}
    if kind == "datetime":
        return {"type": "string", "format": "date-time"}
    if kind == "date":
        return {"type": "string", "format": "date"}
    return {"type": "string", "description": "ArtifactRef handle"}


def _reject(detail: str) -> NoReturn:
    raise SchemaError(f"{detail}; supported types: {SUBSET_POINTER}")


def _has_trestle_decorator(fn: _FnDef) -> bool:
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "trestle":
            return True
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Name)
            and dec.func.id == "trestle"
        ):
            return True
    return False


def _iter_params(fn: _FnDef) -> list[tuple[str, ast.expr | None, bool]]:
    if fn.args.vararg is not None or fn.args.kwarg is not None:
        _reject("unsupported *args/**kwargs")
    params: list[tuple[str, ast.expr | None, bool]] = []
    posonly = list(fn.args.posonlyargs)
    args = list(fn.args.args)
    all_pos = posonly + args
    defaults = fn.args.defaults
    default_start = len(all_pos) - len(defaults)
    for index, arg in enumerate(all_pos):
        params.append((arg.arg, arg.annotation, index >= default_start))
    for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
        params.append((arg.arg, arg.annotation, default is not None))
    return params


def _annotation_is_context(annotation: ast.expr, resolver: _Resolver) -> bool:
    expr = annotation
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        try:
            expr = ast.parse(expr.value, mode="eval").body
        except SyntaxError:
            return False
    name = _simple_name(expr)
    if name in _CONTEXT_NAMES:
        return True
    if isinstance(expr, ast.Attribute) and expr.attr in _CONTEXT_NAMES:
        return True
    imported = resolver.imported_name(expr)
    return imported in _CONTEXT_NAMES


def _simple_name(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    return None


class _Resolver:
    def __init__(self, tree: ast.Module, *, source_path: Path | None) -> None:
        self.source_path = source_path
        self.classes: dict[str, ast.ClassDef] = {}
        self.aliases: dict[str, ast.expr] = {}
        self.imports: dict[str, tuple[str, str | None]] = {}
        self._module_cache: dict[str, _Resolver | None] = {}
        self._index(tree)

    def _index(self, tree: ast.Module) -> None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self.classes[node.name] = node
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    local = alias.asname or alias.name
                    self.imports[local] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name
                    self.imports[local] = (alias.name, None)
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.simple
                and node.value is not None
                and _qual_head(node.annotation) == "TypeAlias"
            ):
                self.aliases[node.target.id] = node.value

    def imported_name(self, expr: ast.expr) -> str | None:
        if isinstance(expr, ast.Name):
            if expr.id in _CONTEXT_NAMES:
                return expr.id
            imported = self.imports.get(expr.id)
            if imported is None:
                return expr.id if expr.id in _CONTEXT_NAMES else None
            _mod, orig = imported
            return orig or expr.id
        if isinstance(expr, ast.Attribute):
            return expr.attr
        return None

    def resolve(
        self,
        expr: ast.expr,
        *,
        depth: int,
        stack: tuple[tuple[str, str], ...],
    ) -> TypeNode:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            quoted = expr.value
            try:
                expr = ast.parse(quoted, mode="eval").body
            except SyntaxError:
                _reject(f"unparseable annotation {quoted!r}")
        if _is_none_expr(expr):
            return TScalar("null")
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            return self._union([expr.left, expr.right], depth=depth, stack=stack)
        if isinstance(expr, ast.Subscript):
            return self._subscript(expr, depth=depth, stack=stack)
        if isinstance(expr, ast.Name):
            return self._name(expr.id, depth=depth, stack=stack)
        if isinstance(expr, ast.Attribute):
            return self._attribute(expr, depth=depth, stack=stack)
        _reject(f"unsupported annotation {ast.dump(expr)}")

    def _name(
        self,
        name: str,
        *,
        depth: int,
        stack: tuple[tuple[str, str], ...],
    ) -> TypeNode:
        well = _well_known(name)
        if well is not None:
            return well
        if name in self.aliases and name not in self.classes:
            return self.resolve(self.aliases[name], depth=depth, stack=stack)
        if name in self.classes:
            return self._expand_class(self.classes[name], depth=depth, stack=stack)
        if name in self.imports:
            return self._follow_import(name, depth=depth, stack=stack)
        _reject(f"unsupported type {name!r}")

    def _attribute(
        self,
        expr: ast.Attribute,
        *,
        depth: int,
        stack: tuple[tuple[str, str], ...],
    ) -> TypeNode:
        well = _well_known(expr.attr)
        if well is not None:
            return well
        _reject(f"unsupported type {expr.attr!r}")

    def _subscript(
        self,
        expr: ast.Subscript,
        *,
        depth: int,
        stack: tuple[tuple[str, str], ...],
    ) -> TypeNode:
        head = _qual_head(expr.value)
        slc = expr.slice
        if head in {"Optional"}:
            return TOptional(self.resolve(slc, depth=depth, stack=stack))
        if head in {"Literal"}:
            return TLiteral(_literal_values(slc))
        if head in {"Annotated"}:
            first = _tuple_elts(slc)[0] if isinstance(slc, ast.Tuple) else slc
            return self.resolve(first, depth=depth, stack=stack)
        if head in {"Union"}:
            return self._union(_tuple_elts(slc), depth=depth, stack=stack)
        if head in _LIST_NAMES:
            return TList(self.resolve(slc, depth=depth, stack=stack))
        if head in _SET_NAMES:
            return TSet(self.resolve(slc, depth=depth, stack=stack))
        if head in _DICT_NAMES:
            elts = _tuple_elts(slc)
            if len(elts) != 2:
                _reject("unparameterized dict")
            key_node = self.resolve(elts[0], depth=depth, stack=stack)
            if not (isinstance(key_node, TScalar) and key_node.kind == "str"):
                _reject("non-str dict keys")
            return TDict(self.resolve(elts[1], depth=depth, stack=stack))
        if head in _TUPLE_NAMES:
            elts = _tuple_elts(slc)
            if len(elts) == 2 and isinstance(elts[1], ast.Constant) and elts[1].value is Ellipsis:
                return TList(self.resolve(elts[0], depth=depth, stack=stack))
            _reject("heterogeneous tuples")
        if head in _REJECT_NAMES or head is None:
            _reject(f"unsupported type {head!r}")
        if _is_bare_generic(head):
            _reject(f"unparameterized generic {head!r}")
        _reject(f"unsupported type {head!r}")

    def _union(
        self,
        parts: list[ast.expr],
        *,
        depth: int,
        stack: tuple[tuple[str, str], ...],
    ) -> TypeNode:
        flat: list[ast.expr] = []
        for part in parts:
            if isinstance(part, ast.BinOp) and isinstance(part.op, ast.BitOr):
                flat.extend(_flatten_bitor(part))
            else:
                flat.append(part)
        non_none = [p for p in flat if not _is_none_expr(p)]
        has_none = len(non_none) != len(flat)
        if len(non_none) > 2:
            _reject("untagged unions of more than two non-None types")
        if not non_none:
            return TScalar("null")
        resolved = tuple(self.resolve(p, depth=depth, stack=stack) for p in non_none)
        inner: TypeNode
        if len(resolved) == 1:
            inner = resolved[0]
        else:
            inner = TUnion(resolved)
        if has_none:
            return TOptional(inner)
        return inner

    def _expand_class(
        self,
        cls: ast.ClassDef,
        *,
        depth: int,
        stack: tuple[tuple[str, str], ...],
    ) -> TypeNode:
        origin = str(self.source_path) if self.source_path is not None else "<source>"
        key = (origin, cls.name)
        if key in stack:
            _reject(f"recursive type {cls.name!r}")
        if _is_enum_class(cls):
            return TEnum(_enum_values(cls))
        if not (_is_dataclass_class(cls) or _is_pydantic_class(cls) or _is_typed_dict_class(cls)):
            _reject(f"arbitrary class {cls.name!r}")
        if depth <= 0:
            _reject(f"dataclass/Pydantic nested deeper than {MAX_OBJECT_DEPTH}")
        nested_stack = (*stack, key)
        properties: list[tuple[str, TypeNode]] = []
        required: list[str] = []
        for stmt in cls.body:
            target, annotation, has_default = _class_field(stmt)
            if target is None or annotation is None:
                continue
            if target.startswith("_"):
                continue
            node = self.resolve(annotation, depth=depth - 1, stack=nested_stack)
            properties.append((target, node))
            if not has_default:
                required.append(target)
        return TObject(tuple(properties), tuple(required))

    def _follow_import(
        self,
        local_name: str,
        *,
        depth: int,
        stack: tuple[tuple[str, str], ...],
    ) -> TypeNode:
        well = _well_known(local_name)
        if well is not None:
            return well
        module, orig = self.imports[local_name]
        attr = orig or local_name
        well_attr = _well_known(attr)
        if well_attr is not None:
            return well_attr
        other = self._load_module(module)
        if other is None:
            _reject(f"arbitrary class {local_name!r}")
        if attr in other.classes:
            return other._expand_class(other.classes[attr], depth=depth, stack=stack)
        if attr in other.aliases:
            return other.resolve(other.aliases[attr], depth=depth, stack=stack)
        _reject(f"arbitrary class {local_name!r}")

    def _load_module(self, module: str) -> _Resolver | None:
        if module in self._module_cache:
            return self._module_cache[module]
        path = _module_source_path(module, relative_to=self.source_path)
        if path is None or not path.is_file():
            self._module_cache[module] = None
            return None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            self._module_cache[module] = None
            return None
        resolver = _Resolver(tree, source_path=path)
        self._module_cache[module] = resolver
        return resolver


def _well_known(name: str) -> TypeNode | None:
    if name in _REJECT_NAMES:
        _reject(f"unsupported type {name!r}")
    if name in _SCALAR_NAMES:
        return TScalar(cast(Literal["str", "int", "float", "bool"], _SCALAR_NAMES[name]))
    if name in _PATH_NAMES:
        return TScalar("path")
    if name in _DATETIME_NAMES:
        return TScalar("datetime")
    if name in _DATE_NAMES:
        return TScalar("date")
    if name in _ARTIFACT_NAMES:
        return TScalar("artifact_ref")
    if name in _LIST_NAMES | _SET_NAMES | _DICT_NAMES | _TUPLE_NAMES:
        _reject(f"unparameterized generic {name!r}")
    return None


def _is_bare_generic(head: str | None) -> bool:
    return head in _LIST_NAMES | _SET_NAMES | _DICT_NAMES | _TUPLE_NAMES


def _qual_head(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _tuple_elts(expr: ast.expr) -> list[ast.expr]:
    if isinstance(expr, ast.Tuple):
        return list(expr.elts)
    return [expr]


def _flatten_bitor(expr: ast.expr) -> list[ast.expr]:
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return _flatten_bitor(expr.left) + _flatten_bitor(expr.right)
    return [expr]


def _is_none_expr(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Constant) and expr.value is None:
        return True
    return isinstance(expr, ast.Name) and expr.id in {"None", "NoneType"}


def _literal_values(expr: ast.expr) -> tuple[object, ...]:
    values: list[object] = []
    for elt in _tuple_elts(expr):
        if not isinstance(elt, ast.Constant):
            _reject("Literal values must be constants")
        if isinstance(elt.value, (bytes, bytearray)):
            _reject("unsupported type 'bytes'")
        values.append(elt.value)
    if not values:
        _reject("empty Literal")
    return tuple(values)


def _enum_values(cls: ast.ClassDef) -> tuple[object, ...]:
    values: list[object] = []
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            if not names or names[0].startswith("_"):
                continue
            if isinstance(stmt.value, ast.Constant) and not isinstance(
                stmt.value.value, (bytes, bytearray)
            ):
                values.append(stmt.value.value)
            else:
                values.append(names[0])
        elif (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and not stmt.target.id.startswith("_")
        ):
            if stmt.value is not None and isinstance(stmt.value, ast.Constant):
                values.append(stmt.value.value)
            else:
                values.append(stmt.target.id)
    if not values:
        _reject(f"empty Enum {cls.name!r}")
    return tuple(values)


def _class_field(stmt: ast.stmt) -> tuple[str | None, ast.expr | None, bool]:
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        if _is_class_var(stmt.annotation):
            return None, None, True
        return stmt.target.id, stmt.annotation, _has_default(stmt.value)
    return None, None, True


def _is_class_var(expr: ast.expr) -> bool:
    head = _qual_head(expr) if not isinstance(expr, ast.Subscript) else _qual_head(expr.value)
    return head == "ClassVar"


def _has_default(value: ast.expr | None) -> bool:
    if value is None:
        return False
    if isinstance(value, ast.Constant) and value.value is Ellipsis:
        return False
    if isinstance(value, ast.Call):
        func = _qual_head(value.func)
        if func in {"field", "Field"}:
            for kw in value.keywords:
                if kw.arg in {"default", "default_factory"}:
                    return True
            if value.args:
                first = value.args[0]
                if isinstance(first, ast.Constant) and first.value is Ellipsis:
                    return False
                return True
            return False
    return True


def _is_enum_class(cls: ast.ClassDef) -> bool:
    return bool(_base_names(cls) & _ENUM_BASES)


def _is_pydantic_class(cls: ast.ClassDef) -> bool:
    return bool(_base_names(cls) & _PYDANTIC_BASES)


def _is_typed_dict_class(cls: ast.ClassDef) -> bool:
    return bool(_base_names(cls) & _TYPED_DICT_BASES)


def _is_dataclass_class(cls: ast.ClassDef) -> bool:
    for dec in cls.decorator_list:
        head = _qual_head(dec.func) if isinstance(dec, ast.Call) else _qual_head(dec)
        if head in _DATACLASS_DECOS:
            return True
    return False


def _base_names(cls: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in cls.bases:
        head = _qual_head(base)
        if head:
            names.add(head)
    return names


def _module_source_path(module: str, *, relative_to: Path | None) -> Path | None:
    if relative_to is not None:
        sibling = relative_to.parent / f"{module.replace('.', '/')}.py"
        if sibling.is_file():
            return sibling
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ModuleNotFoundError, ValueError):
        return None
    if spec is None or spec.origin in {None, "built-in", "frozen"}:
        return None
    origin = Path(spec.origin)
    if origin.suffix != ".py":
        return None
    return origin


def _is_ctx_param(name: str, annotation: ast.expr | None, resolver: _Resolver) -> bool:
    if name == "ctx":
        return True
    if annotation is None:
        return False
    return _annotation_is_context(annotation, resolver)
