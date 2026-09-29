# Writing plugins

Plugins are ordinary Python callables wrapped by `@trestle`. Authors use **PluginSurface only** — no Kernel, FastMCP, or ledger imports.

## Shape

```python
from trestle.plugin.surface import Context, trestle


@trestle
def echo(ctx: Context, message: str = "hello") -> dict[str, str]:
    ctx.log(f"echo: {message}")
    return {"message": message}
```

Worked example: [`examples/plugins/echo.py`](../examples/plugins/echo.py).

<!-- K-5 -->
The decorator has two forms with the same runtime effect: bare `@trestle`, and the call form
`@trestle(deadline=..., summary_fields=..., packages=..., env_arg=..., secrets=...)`. The call form
is read statically from the source at publication, so every value must be a literal; an unknown
keyword or a non-literal value is refused at publication (`publication.validation_failed`). The
entry point must be a plain `def`: an `async def` entry is refused at publication with a message
saying so, never accepted and left to fail at run time.
<!-- /K-5 -->

### Declared metadata

<!-- K-9 -->
`deadline` (seconds, or a `timedelta`) is the plugin's declared deadline. A plugin that declares
none keeps the 300 s default. The declared value is what admission mints as the run's deadline
(`ctx.deadline`), and it is enforced like any other, so a plugin that declares 310 s and works for
305 s is not stopped at 300 s. A declared deadline above the ceiling (`deadline_ceiling` in
`trestle.common.clock`, 3600 s) is refused at admission with `admission.budget_does_not_fit`,
before any run id exists. `describe_plugin` shows the deadline in force (`deadline_s`) and whether it was
`declared` or the `default` (`deadline_source`); `timeout_s` carries the same duration, rounded up
to whole seconds.
<!-- /K-9 -->

`summary_fields`, `env_arg` and `secrets` are declared and recorded in the snapshot's
`manifest.json` (`declared`) and count toward its identity; nothing acts on them yet.

`packages` names the modules or packages the plugin imports from outside its own file, and is
**recorded, not snapshotted** (R-J). Publication resolves each name on the import path the child
process will use, without importing it, and records a digest of its files in the manifest; the
digests are part of the snapshot's identity. Trestle does not copy the package into the snapshot:
the code that runs is whatever is on the import path when the run starts. At run start the child
compares each declared package with the recorded digest, before it loads the plugin, and a
mismatch (the package was edited, moved or removed after publication) stops the run with
`execution.provenance_mismatch` instead of running different code under the same snapshot id.
Republish to accept the edit. A module the plugin imports but does not declare is not covered:
it is neither recorded nor checked. `query(run_provenance)` lists the recorded `packages` and
their digests for each run.

- Return a small mapping that fits the default agent summary.
- Use `ctx.log` for structured events; wrapper stdout/`print` appears in `query(run_tail)`.
- Write keepers under `outputs/` — the foundation attaches them as artifacts.

## Deadlines, cancel and subprocesses

`ctx.deadline` is the run's deadline, fixed at admission (the plugin's declared `deadline`, else
300 s, counted from admission, queue time included), and it is **enforced**: when it passes, the supervisor stops the run's whole process
tree, whether or not the plugin ever reads `ctx.deadline` or `ctx.cancelled`. It is no longer a hint
a plugin may ignore. Reading `ctx.cancelled` still lets a plugin stop cooperatively and cleanly
first.

A stop sends SIGTERM to every process attributable to the run (the run's process group and every
descendant by parent id, in whatever session it has moved to), waits at most `grace` (default 10 s),
sends SIGKILL, and waits at most `kill` (default 5 s) for confirmation: `stop_bound` in all
(`grace` + `kill`, 15 s by default), plus at most one supervisor `poll_interval` (0.05 s) to notice a
cancel. The values are published in `trestle.common.clock` (`grace`, `kill`, `stop_bound`,
`poll_interval`); the operator sets `TRESTLE_CANCEL_GRACE_S` and `TRESTLE_CANCEL_KILL_S`. A plugin
that needs to clean up on SIGTERM has `grace` to do it.

Subprocesses a plugin starts run non-interactively: their stdin is `/dev/null`, never the MCP
transport. They are inside the run's cancellable scope, so a daemon a plugin leaves behind is
stopped when the run ends, on every terminal path. A process that double-forks between two looks of
the supervisor can escape attribution; that limit is disclosed, not hidden.

## Supported types

A plugin's parameters and return are described to agents by the JSON Schema Trestle derives from its type hints; plugins never author a schema. A hint outside this list is refused at publication with a pointer to this section. Every parameter needs a hint, and the entry point must declare a return hint.

| Annotation | Accepted JSON | The plugin receives |
|------------|---------------|---------------------|
| `str`, `int`, `float`, `bool`, `None` | the same JSON type (a `bool` is not an `int`) | that value |
| `Path` | string | `pathlib.Path` (a filesystem path, not an artifact handle) |
| `datetime` | string: ISO 8601 date-time **with a timezone** | timezone-aware `datetime` |
| `date` | string: a valid ISO 8601 calendar date | `date` |
| `ArtifactRef` | string handle | the string |
| `Enum`, `StrEnum`, `IntEnum`, `Flag`, `IntFlag` classes | one of the member values (the member name when a member is not a constant) | the member |
| `Literal[...]` | one of the constants (no `bytes`) | that constant |
| `list[T]`, `List[T]`, `Sequence[T]` | array of `T` | `list` |
| `tuple[T, ...]`, `Tuple[T, ...]` | array of `T` | `tuple` |
| `set[T]`, `Set[T]` | array of unique `T` | `set` |
| `frozenset[T]`, `FrozenSet[T]` | array of unique `T` | `frozenset` |
| `dict[str, T]`, `Dict[str, T]`, `Mapping[str, T]`, `MutableMapping[str, T]` | object with string keys | `dict` |
| `TypedDict` class | object with the declared keys | `dict` |
| `@dataclass` class, Pydantic `BaseModel` | object with the declared fields | `dict` |
| `Optional[T]`, `T \| None` | `T` or `null` | `T` or `None` |
| `Union[A, B]`, `A \| B` (at most two non-`None` types) | either | the option the value fits |
| `Annotated[T, ...]` | as `T` | as `T` |

Aliases (`TypeAlias`) and classes imported from a sibling file resolve to the forms above. A dict-annotated parameter always arrives as a `dict`; only an annotation naming one of the types above changes what the plugin receives.

Not supported, and refused at publication: `bytes`, `bytearray`, `memoryview`, `Any`, `object`, `Callable`, `*args` and `**kwargs`, unparameterized `list`, `dict`, `set` or `tuple`, `tuple` with fixed mixed element types, `dict` keys that are not `str`, unions of more than two non-`None` types, recursive types, records nested more than 5 deep, arbitrary classes, and missing hints.

### Dates and datetimes

A `datetime` argument must be an ISO 8601 date-time with a timezone (`2026-01-01T00:00:00Z`, `2026-01-01T09:00:00+02:00`); a `date` argument must be a valid ISO 8601 date (`2026-01-01`). A naive date-time (`2026-01-01T00:00:00`), a date-time sent where a date is declared, or a malformed value is refused with `admission.invalid_args` before a run id exists, so no run is recorded. Send the offset explicitly; Trestle does not assume a local timezone.

### Return values

A return is written as JSON, and only a value that has a JSON form is written. These encode: `None`, `bool`, `int`, `float` (finite), `str`, `list` and `tuple` (as arrays), `dict` with `str` keys, an `Enum` member (its value), a `Path` (its string), a `date` and a timezone-aware `datetime` (ISO 8601), and a `set` or `frozenset` **when the return hint names one** (`-> set[str]`), written as an array in sorted order so the bytes never depend on hash order.

Anything else ends the run as `execution.result_unencodable`, with a message naming the offending type: a `set` the return hint does not declare, a `dict` with non-`str` keys, a generator or other iterator (return a `list`), `bytes`, a naive `datetime`, a non-finite float, and, for now, a dataclass or Pydantic record. The run has no `result.json` and no partial file; before this rule such values could be written as invalid JSON and marked complete, or crash the run with no code. Results that already encoded (dicts, lists, scalars) keep the same bytes.

---

## Publish paths

### Filesystem drop-in

```bash
cp my_tool.py ~/.trestle/plugins/
# or
trestle serve --plugin-dir ./tools
```

Persist search paths in `$TRESTLE_HOME/config.toml`:

```toml
[plugins]
paths = ["~/.trestle/plugins", "/abs/path/to/repo/tools"]
```

Hot reload: drop a new `.py` file or call `publish_plugin`; `list_plugins` shows a bumped `registry_version`.

### Runtime (MCP)

```json
{
  "source": "from trestle.plugin.surface import Context, trestle\n\n@trestle\ndef my_tool(ctx: Context) -> dict[str, str]:\n    return {\"ok\": \"yes\"}\n",
  "name": null
}
```

Success → `PublishView` with `name`, `registry_version`. Failure → `RequestOutcome` with `origin: "publication"` (no `run_id`).

After publish: `list_plugins` → optional `describe_plugin` → `run(plugin=…)`.

## Agent discovery

| Step | Tool |
|------|------|
| Names only | `list_plugins` |
| One schema | `describe_plugin(plugin_id=…)` |
| Execute | `run(plugin=…, args={…})` |

Plugin JSON schemas are **not** on `tools/list`. Empty catalog → `admission.plugin_not_found`; run `trestle init` or publish a plugin.

## Workflow packs

Pre-built Docker / pytest / migration plugins live under `examples/packs/`. Install `pip install -e ".[packs]"` and point `--plugin-dir` at that folder. Guide: [`packs.md`](packs.md).

## Related

- Agent workflow: [`agents.md`](agents.md)
- Full retrieval playbook: [`agent-console-mcp.md`](agent-console-mcp.md)
