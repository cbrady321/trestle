# Trestle — Agent Plugin Interface contract

Scope: agent plugin lifecycle (APL-01–APL-08)  
Node: trestle  
Mode: complement  
Authority: this file is the consumer contract for **how agents discover, supply, and invoke plugins**. [Interfaces Architecture](hld-interface-architecture-trestle.md) remains freeze for Kernel ports and envelope families. **Amendment `agent-plugin-publish`:** adds one fixed MCP tool `publish_plugin` (R-MCP-1 still holds — count does not grow with plugin count).

**Upstream stories:** [`user-stories-agent-plugin-interface.md`](user-stories-agent-plugin-interface.md)  
**Agent playbook:** [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md)

---

## Guiding light

Agents need a **stable porch** (ten fixed tools), a **cheap catalog** (names without schemas), a **pull-one schema** path, **runtime publish** via MCP, and optional **filesystem supply**. Operators need to **point the server at project folders** without duplicating plugin files.

---

## Exploration (settled direction)

### Alternatives considered

| ID | Boundary bet | Agent / operator outcome | Verdict |
|----|--------------|--------------------------|---------|
| **A** | **Catalog triad + `publish_plugin` + filesystem supply + publication config** | MCP publish at runtime; filesystem drop-in; discover/invoke unchanged | **Selected** |
| B | MCP `upload_plugin` only (no filesystem) | Agent publishes bytes without filesystem write | **Rejected** — filesystem watch remains for human/dev workflows |
| C | Per-plugin MCP tools (`echo`, `analyze_csv`, …) | Self-documenting tool list | **Rejected** — G1; `tools/list` floods with plugin count |
| D | Inline all `input_schema` on `list_plugins` | One-call discovery | **Rejected** — R-MCP-2 |
| E | Separate REST catalog beside MCP | Browser-native agents | **Rejected** — duplicate admission grammar; operator HTTP is read-only by spec |
| F | MCP Resources (`plugin://…`) for source files | URI-based supply | **Rejected** — does not replace filesystem watch; R-MCP-9 forbids second schema channel |
| G | Collapse `describe_plugin` into `list_plugins?include_schema=true` | Fewer tools | **Rejected** — opt-in schema pull is the G1 mechanism; flag invites over-fetch |

### Settled framing

**One job, three seams — do not merge them.**

| Seam | Medium | Consumer | Job |
|------|--------|----------|-----|
| **1. Publication config** | wire (config + CLI) | operator, agent host setup | Name which directories the server watches |
| **2. Agent catalog porch** | wire (MCP) | coding agent | Discover, publish, pull schema, invoke `run` |
| **3. Plugin supply** | class + filesystem **or** MCP `publish_plugin` | agent | Write `@trestle` source; hot reload into catalog |

Seam 2 gains **`publish_plugin`** (fixed tenth tool). Seam 3 accepts **either** filesystem write **or** MCP publish — same registry pipeline.

---

## Consumer and intent

| Role | Job this boundary serves | Explicit exclusions |
|------|--------------------------|---------------------|
| **Coding agent** (primary) | Attach; browse catalog; pull one schema; run work; detect freshness; recover from admission failures | Upload tool; per-plugin MCP tools; path-based fetch; operator `run` from HTTP |
| **Agent host** (Cursor, Claude) | Spawn `trestle serve`; pass `TRESTLE_HOME` and optional plugin paths via env/config | Replace stdio with operator HTTP for execution |
| **Operator** (adjacent) | Configure plugin paths; verify via `doctor`; inspect registry in browser | Admit or `run` from `/ops/v1` |
| **Plugin author** (same file as agent in APL-04) | Write one `.py` with PluginSurface only | Import Kernel, FastMCP, ledger |

---

## Interface 1 — PluginPublicationConfig (setup wire)

**Stories:** APL-01 (bootstrap), APL-04 (writable path), APL-08 (multi-folder)  
**Medium:** wire — `config.toml` + CLI flags + env override  
**Consumer:** operator and agent-host setup (not per-turn MCP)

### Contract (signature first)

```toml
# $TRESTLE_HOME/config.toml
[plugins]
paths = [
  "~/.trestle/plugins",
  "/abs/path/to/repo/tools",
]
```

```bash
trestle serve --plugin-dir /path/a --plugin-dir /path/b
trestle ops serve --plugin-dir /path/a --home ~/.trestle
```

```bash
# env alternative (colon-separated, POSIX paths)
export TRESTLE_PLUGIN_DIRS="$HOME/.trestle/plugins:/abs/repo/tools"
```

**Resolution order (normative):**

1. Repeated CLI `--plugin-dir` (if any) replaces configured list entirely for that process.
2. Else `config.toml` `[plugins].paths` if present.
3. Else env `TRESTLE_PLUGIN_DIRS` if set.
4. Else default `[{TRESTLE_HOME}/plugins]`.

**Invariants**

- Every path is expanded (`~` → home) and normalized to absolute before registry scan.
- Directories need not exist at startup; missing dirs are skipped (no error).
- Registry scans each path for `*.py` (non-recursive); `_*.py` ignored.
- **Name collision** across paths: first path in resolved order wins; later duplicate `name` is skipped with `service.log` warning (deterministic, not silent override of published snapshot).
- Changes require **process restart** for CLI flags; `config.toml` edits picked up on next `create_kernel` / server start (no mid-flight reload of path list in v0.1).

**Doctor projection**

`build_doctor_report` MUST include:

```
plugin_search_paths:
  - /abs/path/a (3 plugins)
  - /abs/path/b (0 plugins)
```

### Semantic behavior

| Event | Outcome |
|-------|---------|
| Valid path with plugins | Files appear in unified `list_plugins` catalog |
| Path missing | Skipped; doctor lists with count 0 |
| Duplicate plugin name | First path wins; warning logged |
| Agent writes new `.py` into any watched path | Hot reload within path (R-REG-1); `registry_version` bumps |

### Non-goals

- Recursive package tree scan (`src/`, `pyproject.toml` entry points).
- Auto-install of Python dependencies (R-PLUG-21).
- Runtime reload of `paths` list without restart (deferred).

---

## Interface 2 — AgentCatalogPort (MCP wire)

**Stories:** APL-01–APL-07  
**Medium:** wire — ten frozen MCP tools; this section owns **catalog subset only**  
**Consumer:** coding agent

### Shared freeze (cite, do not fork)

`CatalogView`, `PluginCatalogRow`, `RequestOutcome`, porch flattening of `run` → `RequestOutcome | RunView`, and `tools/list.registry_version` mirror live in the freeze HLD. **Do not duplicate** execution, query, or fetch contracts here.

### Catalog triad + publish (normative)

| Tool | Input | Success output | Failure |
|------|-------|----------------|---------|
| `list_plugins` | `invalid?: bool` (optional) | `CatalogView` | `RequestOutcome` (`projection.*`) |
| `describe_plugin` | `plugin_id: str` | `PluginDescription` | `projection.not_found` |
| `publish_plugin` | `source: str`, `name?: str` | `PublishView` | `RequestOutcome` (`publication.*`) |
| `run` | `plugin: str`, `args?: object`, … | `RunView` | `RequestOutcome` (`admission.*`; **no `run_id`**) |

**Param alias rule:** `plugin_id` in `describe_plugin` MUST equal `plugin` in `run` for the same capability. Implementations SHOULD accept `plugin` as alias on `describe_plugin` in a future minor bump; agents MUST use documented names until then.

### CatalogView — additive bump (APL-01)

Freeze `PluginCatalogRow` and pagination fields unchanged. **Add** publication context fields (fixed size — not per-plugin):

```python
class CatalogView:
    registry_version: int
    items: Sequence[PluginCatalogRow]
    next_cursor: Handle | None
    truncated: bool
    plugin_search_paths: Sequence[str]   # NEW — absolute paths server watches
    catalog_hint: str | None             # NEW — set when items empty; else omit or null
```

| Field | When present | Content |
|-------|--------------|---------|
| `plugin_search_paths` | always | Resolved absolute paths from PluginPublicationConfig |
| `catalog_hint` | `len(items)==0` | Single actionable sentence, e.g. `"No plugins published. Write a @trestle-decorated .py file into a plugin_search_paths directory, then call list_plugins again."` |
| `catalog_hint` | `len(items)>0` | `null` or omitted |

**Byte budget:** additive fields MUST NOT push `list_plugins` typical response near G1 porch limits; paths list is operator-scale (usually &lt;10 entries), not plugin-scale.

### PublishView (`publish_plugin` success body)

```python
class PublishView:
    name: str
    snapshot_id: str
    registry_version: int
    source_sha256: str
    created: bool  # True when a new file was written; False on update
```

| Code | When |
|------|------|
| `publication.invalid_source` | Python syntax error |
| `publication.no_entrypoint` | No `@trestle` decorated function |
| `publication.name_mismatch` | `name` param ≠ discovered entry point |
| `publication.source_too_large` | Source &gt; 512 KB |
| `publication.validation_failed` | Snapshot not published; previous kept if any |

**Pipeline:** `publish_plugin` → atomic write to primary `plugin_search_paths[0]` → `registry.refresh()` → same validate/snapshot path as filesystem drop-in (R-REG-1). In-flight runs keep their admit-time snapshot (US-09).

### PluginDescription (describe_plugin body)

```python
class PluginDescription:
    name: str
    version: str
    snapshot_id: str
    source_sha256: str
    summary_budget: int
    timeout_s: int
    input_schema: Mapping[str, object]   # JSON Schema from type hints (R-PLUG-1)
    # side_effect_hints: optional future; not v0.1
```

| Case | Wire behavior |
|------|---------------|
| Unknown `plugin_id` | `projection.not_found`, `isError: true`, no empty schema object |
| Known, valid | Full `PluginDescription` |
| Known, `valid=false` in catalog | Still describable if snapshot exists; schema reflects last good snapshot |

### Freshness channel (APL-06)

| Channel | Field | Rule |
|---------|-------|------|
| `list_plugins` | `registry_version` | Kernel publication fact |
| `tools/list` | `_meta.registry_version` | MUST equal `CatalogView.registry_version` on same publication |
| Agent compare | either channel | If changed since last turn → re-call `list_plugins` |

### Admission vocabulary (APL-05, APL-07)

| Agent mistake | Code | `run_id` | Next step |
|---------------|------|----------|-----------|
| Unknown plugin name | `admission.plugin_not_found` | no | `list_plugins` |
| Bad args | `admission.invalid_args` | no | `describe_plugin` |
| TTY not ready | `admission.tty_not_ready` | no | operator / leave-Trestle |
| Called `echo` as MCP tool | protocol unknown-tool | n/a | `run(plugin="echo", …)` |
| Run failed after admit | terminal `RunView.state` | yes | `query(last_error)` |

### Golden workflow (consumer scenario)

```
tools/list           → confirm 10 tools + registry_version
list_plugins         → names (+ plugin_search_paths when shipped)
publish_plugin       → optional: upsert source at runtime
describe_plugin(id)? → input_schema if args unknown
run(plugin, args)    → RunView | RequestOutcome
fetch / query        → evidence (US-05; out of scope here)
```

**Turn budget:** ≤5 MCP tools after bootstrap for typical success path.

---

## Interface 3 — PluginSurface supply (class + filesystem)

**Stories:** APL-04, APL-07 (validation)  
**Medium:** class — `trestle.plugin.surface`  
**Consumer:** agent authoring plugin files on disk

### Contract (signature first)

```python
@trestle
def capability_name(ctx: Context, arg: str, …) -> ReturnType:
    ...
```

```python
class Context(Protocol):
    tmp: Path
    outputs: Path
    cancelled: bool
    deadline: datetime
    def log(self, message: str) -> None: ...
    def progress(self, message: str, *, fraction: float | None = None) -> None: ...
    def artifact(self, name: str) -> Path: ...
    def attach(self, path: Path, *, name: str) -> str: ...
```

### Publication pipeline (agent-visible effects)

```
write plugins/foo.py
  → debounced watch (250ms)
  → validate in throwaway subprocess (R-PLUG-16)
  → snapshot immutable bytes (R-ID-2)
  → registry_version++
  → list_plugins shows name
```

| Failure | Agent-visible signal | NOT |
|---------|---------------------|-----|
| Syntax / import / validation | `valid=false` or absent from default catalog; previous snapshot kept | `run_id` |
| Missing third-party dep | validation failure / `import_failed` diagnosis | auto-install |
| Success | `name` in `list_plugins`, `registry_version` bumped | new MCP tool |

### Non-goals

- MCP upload of source bytes.
- Multi-file package as one plugin identity (v0.1).
- Writable `evidence/` paths (R-STORE-3).

---

## Consumer Scenario Proof Pack

| # | Scenario | Seam | Steps | Expected contract outcome |
|---|----------|------|-------|---------------------------|
| P1 | First attach, empty home | 2 | `tools/list` → `list_plugins` | 10 tools; `items=[]`; `catalog_hint` set; `plugin_search_paths` lists default dir |
| P2 | Browse without schemas | 2 | `list_plugins` | `items[*]` have name/version/class; no `input_schema` key anywhere |
| P3 | Learn args for one plugin | 2 | `describe_plugin("echo")` | `input_schema` present; `snapshot_id` present |
| P4 | Agent publishes new tool | 3→2 | write `plugins/fresh.py` → `list_plugins` → `run` | `registry_version` increases; `run` returns `RunView` with `run_id` |
| P5 | Mis-invoke plugin as tool | 2 | MCP call tool `echo` | Protocol error; playbook says use `run` |
| P6 | Unknown plugin | 2 | `run(plugin="nope")` | `admission.plugin_not_found`; no `run_id` |
| P7 | Catalog freshness | 2 | note `registry_version`; drop file; `list_plugins` | version increments; new name appears |
| P8 | Project folder plugins | 1→2 | config `paths=[repo/tools]`; `list_plugins` | plugins from repo appear; `plugin_search_paths` includes repo path |
| P9 | Duplicate name across dirs | 1 | same `name` in two paths | first path wins; single catalog row |
| P10 | Invalid plugin file | 3→2 | write broken `.py` | previous version still runs; `valid=false` when listed with `invalid=true` |

---

## Implementation map (stories → interfaces)

| Story | Primary interface | Shipped today? |
|-------|-------------------|----------------|
| APL-01 | CatalogView bump + playbook | **Yes** |
| APL-02 | `list_plugins` / `CatalogView` | **Yes** |
| APL-03 | `describe_plugin` / `PluginDescription` | **Yes** (schema stub in code) |
| APL-04 | PluginSurface + filesystem + `publish_plugin` | **Yes** |
| APL-05 | `run` porch grammar | **Yes** |
| APL-06 | `registry_version` mirror | **Yes** |
| APL-07 | admission vs terminal codes | **Yes** |
| APL-08 | PluginPublicationConfig | **Yes** |

### Recommended build order

1. ~~**PluginPublicationConfig**~~ — shipped.
2. ~~**CatalogView bump**~~ — shipped.
3. ~~**Playbook + Cursor rule**~~ — playbook updated.
4. ~~**`trestle init`**~~ — shipped.

**Do not build:** per-plugin tools, REST execution API, package entry-point scanner (separate initiative).

---

## Convergence (cycle 1)

| Finding | Severity | Resolution |
|---------|----------|------------|
| Three interfaces vs one | minor | Intentional — single-purpose boundaries per CAFE |
| CatalogView bump needs freeze note | major | Documented as additive CatalogView column; nine ViewRows untouched |
| `describe_plugin` / `run` param naming | minor | Alias rule documented; no breaking change required |
| PluginPublicationConfig path reload | minor | Bounded: restart required in v0.1 |

**Gate:** pass — no open major consumer-breaking findings.

---

## References

- [`hld/hld-interface-architecture-trestle.md`](hld-interface-architecture-trestle.md) — freeze
- [`hld/user-stories-agent-plugin-interface.md`](user-stories-agent-plugin-interface.md) — stories
- [`hld/interface-design-tty-class-trestle.md`](interface-design-tty-class-trestle.md) — overlay pattern
- [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md) — agent SSOT
