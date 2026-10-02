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
process will use, without importing it, and records a digest of its `.py` files (a package's other files are not covered) in the manifest; the
digests are part of the snapshot's identity. Trestle does not copy the package into the snapshot:
the code that runs is whatever is on the import path when the run starts. At run start the child
compares each declared package with the recorded digest, before it loads the plugin, and a
mismatch (the package was edited, moved off the import path or removed after publication) stops the run with
`execution.provenance_mismatch` instead of running different code under the same snapshot id.
Republish to accept the edit. A module the plugin imports but does not declare is not covered:
it is neither recorded nor checked. Neither is anything a declared package itself imports, the
interpreter, or the installed distributions: only the Trestle version is recorded, and none of
these is checked at run start. `query(run_provenance)` lists the recorded `packages` and
their digests for each run.

- Return a small mapping that fits the default agent summary.
- Use `ctx.log` for structured events; wrapper stdout/`print` appears in `query(run_tail)`.
- Use `ctx.event(kind, **fields)` to record an event of your own kind. It shares the limits of
  `ctx.log` and `ctx.progress` (per-event size, count, rate; over a limit the event is dropped and
  the run's `limits_exceeded` names it) and the same secret scrubbing. A kind the runtime records
  itself (`log`, `progress`, `artifact_available`, `error`) or uses for a run's lane record
  (`plan`, `issue`, `confirmation`, `result`, `released`, `step`, `node_end`) is refused with
  `ValueError`, as is an empty kind.
- Write keepers under `outputs/` — the foundation attaches them as artifacts.

## Artifacts and their limits

A run produces artifacts three ways, and the same limits hold on all three (K-17):

- **`outputs/`.** Every file under `ctx.outputs` is promoted to an artifact when the run ends, whatever
  the run's class.
- **`ctx.attach(path, name=...)`** copies a file under the work directory into the run's evidence
  now and returns its handle.
- **`ctx.artifact(name)`** returns a staging path (`<name>.partial`). A staged file that is never
  attached is promoted when the run ends, under `name`; if it is attached, the attach takes it
  and it is not promoted again.

A run may hold at most 1 000 artifacts and 2 GiB of artifact bytes in all (10 and 64 KiB under
`TRESTLE_TEST_LIMITS=1`). An artifact that would pass either limit is **not** stored, and the run's
`limits_exceeded` names the limit once (`stream: "artifacts"`, `limit: "max_artifact_count"` or
`"max_artifact_bytes"`, with the bytes left out): a refused promotion leaves the file where the plugin
wrote it, and a refused `attach` returns the empty string, so a handle a plugin was given always
fetches. Every promoted artifact is scrubbed of declared secret values first; a binary artifact that
holds one is refused with a `secret_in_binary` marker. Limit markers are one per (stream, limit) for
the whole run, however many things were dropped: the marker's byte total counts them all.

## Deadlines, cancel and subprocesses

`ctx.deadline` is the run's deadline, fixed at admission (the plugin's declared `deadline`, else
300 s, counted from admission, queue time included), and it is **enforced**: when it passes, the supervisor stops the run's whole process
tree, whether or not the plugin ever reads `ctx.deadline` or `ctx.cancelled`. It is no longer a hint
a plugin may ignore. Reading `ctx.cancelled` still lets a plugin stop cooperatively and cleanly
first.

The supervisor is the only thing that signals a run. A cancel request writes a flag and nothing
else; the supervisor sees the flag (or, for the deadline, the run's release point), records one
stop row naming the cause and the lane's committed length, and the run's class is the cause of the
first stop row. A stop then gives a run that declares a release walk its release slice to release
cooperatively (a run that declares none has slice 0), and sends SIGTERM to every process
attributable to the run (the run's process group and every descendant by parent id, in whatever
session it has moved to), waits at most `grace` (default 10 s), sends SIGKILL, and waits at most
`kill` (default 5 s) for confirmation: `stop_bound` in all (`release_slice` + `grace` + `kill`,
25 s by default), plus at most one supervisor `poll_interval` (0.05 s) to notice a cancel. A plain
plugin's slice is 0, so its own stop takes `grace` + `kill`. The values are published in
`trestle.common.clock` (`release_slice`, `grace`, `kill`, `stop_bound`, `poll_interval`); the
operator sets `TRESTLE_RELEASE_SLICE_S`, `TRESTLE_CANCEL_GRACE_S` and `TRESTLE_CANCEL_KILL_S`. The
finalization margin (`finalization_margin`, 35 s by default: the stop bound plus the
`FINALIZATION_RESERVE_S` of 10 s) is how long after the deadline a call may still be answered; it
covers the kill and the release sweep of every root Trestle admits. The release slice and the
reserve are plan defaults disclosed for the maintainer to set (10 s each), not requirements. A
plugin that needs to clean up on SIGTERM has `grace` to do it. A run cancelled, or past its
deadline, while it still waits in the queue is finalized at once with no process to stop.

Subprocesses a plugin starts run non-interactively: their stdin is `/dev/null`, never the MCP
transport. They are inside the run's cancellable scope, so a daemon a plugin leaves behind in the run's
process group, or under a process the run still owns, is stopped when the run ends, on every
terminal path. A daemon that daemonizes by the classic double fork leaves attribution: it can
outlive the run, and the answer does not report it (`cleanup.processes` can still read `released`).
Do not rely on Trestle to stop such a process; see
[What stopping a run does not cover](security.md#what-stopping-a-run-does-not-cover).

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

<!-- K-3 -->
### Typed records

A parameter annotated with a `@dataclass` class arrives as an instance of that class, not as a `dict`: each field is converted to its annotation by the rules in the table above, and a record nested in a list, a mapping, an `Optional` or another record is converted the same way. This overrides the `dict` in the `@dataclass` row of the table. A Pydantic `BaseModel` and a `TypedDict` still arrive as a `dict`, and so does a dict-annotated parameter. A value that is not the record's shape is refused at admission, never handed to the plugin half-converted.
<!-- /K-3 -->

### Return values

A return is written as JSON, and only a value that has a JSON form is written. These encode: `None`, `bool`, `int`, `float` (finite), `str`, `list` and `tuple` (as arrays), `dict` with `str` keys, an `Enum` member (its value), a `Path` (its string), a `date` and a timezone-aware `datetime` (ISO 8601), and a `set` or `frozenset` **when the return hint names one** (`-> set[str]`), written as an array in sorted order so the bytes never depend on hash order.

Anything else ends the run as `execution.result_unencodable`, with a message naming the offending type: a `set` the return hint does not declare, a `dict` with non-`str` keys, a generator or other iterator (return a `list`), `bytes`, a naive `datetime`, a non-finite float, and a Pydantic record. The run has no `result.json` and no partial file; before this rule such values could be written as invalid JSON and marked complete, or crash the run with no code. Results that already encoded (dicts, lists, scalars) keep the same bytes.

<!-- K-4 -->
A plugin may return a dataclass instance (or a container of them) too: the run succeeds and records the instance as its JSON object, its fields written recursively over the supported types above. Any other value that is not JSON is refused as `execution.result_unencodable`.
<!-- /K-4 -->

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

## Composite workflows

A workflow plugin can declare a tree instead of one leaf: an `AllDeclaration` root whose children
(`ChildBinding`s, each naming a unit and the siblings it `needs`) are leaves or further composites.
The declaration is read at publication, and the whole tree is admitted
as one run. Publication refuses a tree the declaration alone shows to be defective, with a code of
its own and no snapshot: `publication.unit_unresolved` (a child or `needs` entry names a unit the
plugin does not declare), `publication.dependency_cycle`, `publication.declaration_conflict` and
`publication.plan_precondition_uncovered`; the codes are listed in
[`agents.md`](agents.md#composite-workflows-trees). Budgets are not checked at publication but at
admission, on every run (see Deadline and budgets below). A tree plugin imports
`trestle.workflow` beside `trestle.plugin`.

The example below runs three steps over the fake marker of `trestle_packs.fakes`: `build` and
`lint` start together, `package` needs both. It is a complete plugin: publish it as it stands.
A plugin that imports the port module must name its environment argument (`env_arg`), which is
also the root's lease key (`env_key_field`); a request must give that argument a value
(`args={"env": "dev"}`), because a default is not read at admission
(`admission.lease_set_undecidable` otherwise).

<!-- tree-example -->
```python
from datetime import timedelta
from typing import Any

from trestle_packs.fakes import FakeMarker

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads, ResourceSpec
from trestle.workflow.units import Acted
from trestle.workflow.values import CheckResult, FoundRef, Observation


class Step:
    """One leaf: create a marker, wait until it is ready, release it on the way out."""

    def __init__(self, unit: str) -> None:
        self.unit = unit
        self.spec = ResourceSpec(unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "demo", None)

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self.unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=6)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(
                EffectDeclaration(
                    "up",
                    EffectFacetClass.CREATE,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=2),
                ),
                EffectDeclaration(
                    "stop",
                    EffectFacetClass.OWNED,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=2),
                    is_release=True,
                ),
            ),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=10),
            max_attempts=2,
        )

    def observe(self, params: Any, reads: Any, ctx: Any) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(self.spec, ctx.lineage, "up")
        target = seen.selector_ref
        checked = resource.check("ready", target) if target is not None else None
        ready = checked is not None and checked.satisfied
        return Observation(
            present=seen.selector_present,
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(ready, None, ""),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Acted:
        effects.create(ResourceCreate).create(self.spec, "up")
        return Acted()

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Acted:
        effects.owned(ResourceOwned).stop(handle, "stop")
        return Acted()


ENTRY = WorkflowEntry(
    root="release",
    units={
        "release": AllDeclaration(
            unit="release",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="build", params={}, needs=()),
                ChildBinding(unit="lint", params={}, needs=()),
                ChildBinding(unit="package", params={}, needs=("build", "lint")),
            ),
            concurrency=2,
            budget=timedelta(seconds=30),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "build": Step("build"),
        "lint": Step("lint"),
        "package": Step("package"),
    },
    deadline=timedelta(seconds=60),
)


@trestle(deadline=60, env_arg="env")
def release(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
```
<!-- /tree-example -->

**Deadline and budgets.** `WorkflowEntry.deadline` is not read in this release. The deadline that
is admitted and enforced is `@trestle(deadline=)`; keep the two equal (the example does). Budgets
are checked at admission, on every run, not at publication: a tree whose budgets cannot fit
publishes, and every `run` of it is then refused `admission.budget_does_not_fit` with a message
naming the node, before a run id. The rules (each parent holds back a 10 s reserve, and a workflow
root with a release walk a 10 s release slice):

- the root's budget plus the release slice must be at most the deadline;
- each child's budget must be at most its parent's budget less the reserve;
- an `AllDeclaration`'s budget must be at least its longest `needs` chain of child budgets, and at
  least ceil(children / `concurrency`) × its largest child's budget;
- every alternative of a `ChoiceNode` must fit, since any one of them may run.

The example fits: 30 + 10 ≤ 60; each leaf's 10 ≤ 30 − 10; the chain `build` → `package` is
10 + 10 = 20 ≤ 30; ceil(3 / 2) × 10 = 20 ≤ 30. At run time a carved leaf still working when its
slice ends stops `timed_out` with `execution.carve_exceeded`. The loop checks the slice between
the unit's calls, so a unit that blocks inside one call is stopped only by the root deadline. The
root is carved nothing: a single-vertex plugin still working at its release point is stopped with
the whole run (`timed_out`, its last verdict's code), never `execution.carve_exceeded`.

**What a leaf returns.** `advance` and `release` return one of the steps in
`trestle.workflow.units`:

| Step | Meaning |
|------|---------|
| `Acted()` | An effect was issued through a facet (`effects.create(...)`, `effects.owned(...)`). `Acted` from a call that issued nothing is a defect (`execution.unit_raised`) |
| `NoAction(reason)` | The call issued nothing; `reason` is a stable code |
| `Blocked(code, human_action, resend)` | A person must act first. The node ends `blocked`; `human_action` (required, at most 1024 bytes) and `resend` (`Resend.SUCCEEDS_AFTER_ACTION`, `WILL_NOT_SUCCEED` or `UNKNOWN`, from `trestle.workflow.values`) reach the agent on `answer.primary` |
| `Failed(code, detail)` | The node ends `failed` with `code`; the nodes that need it are not started, independent siblings finish |

An exception raised from `observe`, `advance` or `release` ends the node `execution_error` with
`execution.unit_raised` and stops the whole tree.

**Gates.** `gates` names children that only read. Before the first effect anywhere in the tree,
each gate is observed once; if it is not satisfied, its composite is stopped with the code the
gate observed, else `execution.declaration_stale`. `needs` orders siblings at run time; `gates` are
checked once, up front. A gate must be one of the composite's children
(`publication.unit_unresolved` otherwise, "a gates entry of '<path>' names none of its children").

**Selecting part of a tree from the request.** Declare the allowed names in an `AllDeclaration`'s
`identifier_sets` and bind a request argument to one of them with
`ArgBinding(arg, identifier_set, filters_children)`. Every binding checks the argument's values
against the set (`admission.unknown_identifier` for a value outside it, before a run id). With
`filters_children=True` the values also select the children: only the named ones run, together
with every sibling they `need`.

An `AllDeclaration` root and a `ChoiceNode` root are both admitted (the loop selects one alternative
of a `ChoiceNode` from what it observes before the first effect). The wire code
`admission.plan_multi_vertex_unsupported` stays defined but is retired: nothing produces it. The agent-facing behaviour (one
answer, child views, cancel addressed to the root) is in [`agents.md`](agents.md#composite-workflows-trees),
and how an agent runs a tree and reads its answer is in
[`agents.md` § Large tasks: the tree](agents.md#large-tasks-the-tree).

## Workflow packs

Pre-built Docker / pytest / migration plugins live under `examples/packs/`. Install `pip install -e ".[packs]"` and point `--plugin-dir` at that folder. Guide: [`packs.md`](packs.md).

## Related

- Agent workflow: [`agents.md`](agents.md)
- Full retrieval playbook: [`agent-console-mcp.md`](agent-console-mcp.md)
