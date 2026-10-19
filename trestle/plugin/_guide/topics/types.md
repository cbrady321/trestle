# Supported types

Parameters and the return are described to agents by a JSON Schema derived from the hints.
Never write a schema.

| Annotation | Accepted JSON | The plugin receives |
|---|---|---|
| `str`, `int`, `float`, `bool`, `None` | the same JSON type (a `bool` is not an `int`) | that value |
| `Path` | string | `pathlib.Path` |
| `datetime` | ISO 8601 date-time **with a timezone** | aware `datetime` |
| `date` | ISO 8601 date | `date` |
| `ArtifactRef` | string handle | the string |
| `Enum`, `StrEnum`, `IntEnum`, `Flag`, `IntFlag` classes | a member value | the member |
| `Literal[...]` | one of the constants | that constant |
| `list[T]`, `List[T]`, `Sequence[T]` | array | `list` |
| `tuple[T, ...]`, `Tuple[T, ...]` | array | `tuple` |
| `set[T]`, `Set[T]`, `frozenset[T]`, `FrozenSet[T]` | array of unique items | `set` / `frozenset` |
| `dict[str, T]`, `Dict[str, T]`, `Mapping[str, T]`, `MutableMapping[str, T]` | object | `dict` |
| `TypedDict` class, Pydantic `BaseModel` | object with the declared keys | `dict` |
| `@dataclass` class | object with the declared fields | an instance |
| `Optional[T]`, `T \| None` | `T` or `null` | `T` or `None` |
| `Union[A, B]`, `A \| B` (at most two non-`None` types) | either | the option the value fits |
| `Annotated[T, ...]` | as `T` | as `T` |

A `TypeAlias` and classes imported from a sibling file resolve to the forms above. These are
refused at publication: `bytes`, `bytearray`, `memoryview`, `Any`, `object`, `Callable`,
`*args`, `**kwargs`, bare `list`, `dict`, `set` or `tuple`, fixed mixed tuples, non-`str` dict
keys, unions of more than two types, recursive types, records nested more than 5 deep,
arbitrary classes and missing hints. A naive datetime argument is refused at admission
(`admission.invalid_args`).

A return must have a JSON form: `None`, `bool`, `int`, a finite `float`, `str`, `list`, `tuple`,
`dict` with `str` keys, an `Enum` member, a `Path`, a `date`, an aware `datetime`, a dataclass
instance, and a `set` or `frozenset` only when the return hint names one. Anything else ends the
run with `execution.result_unencodable`. Return a `list`, not a generator.
