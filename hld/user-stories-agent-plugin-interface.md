# User Stories — Agent Plugin Interface

**Initiative:** Make the plugin surface excellent for coding agents — discovery, supply, invocation, and recovery.  
**Status:** **Complete** @ `2375e3e` (2026-08-25) — APL-01–08 shipped; on `origin/master`.  
**Date:** 2026-08-25  
**Stage:** user-story-stage (writer + critic, one cycle)  
**Upstream:** [`trestle-requirements.md`](../trestle-requirements.md) G1, G3, R-MCP-1–3, R-REG-1–7; [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md); [`hld/agent-mcp-usability-assessment.md`](agent-mcp-usability-assessment.md)

**Relationship to existing set:** [`user-stories-from-requirements.md`](../user-stories-from-requirements.md) covers kernel guarantees (US-01–US-24). This document decomposes the **agent-facing plugin lifecycle** — how capabilities arrive on the server and how agents use them — without duplicating execution, bounding, or TTY overlay stories.

### Guiding light

Agents interact with plugins through a **fixed ten-tool porch**. Plugins are **not** MCP tools. Capabilities reach the server by **filesystem publication** into watched directories **or** MCP `publish_plugin`; the agent discovers them via `list_plugins` / `describe_plugin` and runs them via `run(plugin=…)`.

---

## Stories

### APL-01 — Attach to Trestle and know the catalog is usable

**JTBD:** When I start a session against a local Trestle install, I want to confirm MCP attach succeeded and whether any plugins are published, so I do not waste turns calling `run` against an empty or misconfigured catalog.

**Semantic contract**
- Actor: coding agent (Cursor, Claude Desktop, or scripted MCP client)
- Situation: first turn of a session; `TRESTLE_HOME` may be default, empty, or pre-populated
- Motivation: distinguish “server down” from “server up but no plugins” from “ready to work”
- Progress: I can name the next step — bootstrap plugins, fix host wiring, or proceed to discovery

**Acceptance criteria**
- [x] Host wiring exposes exactly ten MCP tools (`run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`, `unpin`, `list_plugins`, `describe_plugin`, `publish_plugin`) per freeze.
- [x] `list_plugins` returns `CatalogView` with `registry_version` and `items` (possibly empty).
- [x] Empty `items` is a **valid** catalog state — not a protocol error. Every `run` against an unknown name returns `admission.plugin_not_found` with **no** `run_id`.
- [x] Playbook documents bootstrap: create `$TRESTLE_HOME/plugins/`, copy or author at least one `.py` plugin, verify with `trestle doctor` or `list_plugins`.
- [x] `tools/list` payload stays under the G1 byte budget; plugin schemas are **not** inlined.

**Non-goals**
- Does not cover invoking work (APL-05).
- Does not cover operator HTTP attach (`trestle ops serve`) — agents use MCP only.

**Why this framing works**
Session A in the usability assessment showed empty `TRESTLE_HOME` looks like a broken server. This story makes “zero plugins” a diagnosable state with an explicit recovery path, not an ambiguous failure.

**Remaining assumptions**
- Agent host has `trestle` on PATH or an absolute `command` in MCP config.
- Bootstrap is `trestle init` (CLI), filesystem drop-in, `publish_plugin`, or `doctor`; there is no MCP `init` tool.

---

### APL-02 — Browse what I can run without paying schema cost

**JTBD:** When I need to pick a capability, I want a small catalog listing names, versions, and capability class only, so I can choose a plugin without loading every JSON Schema into context.

**Semantic contract**
- Actor: coding agent
- Situation: one or more plugins are published; I need to select `plugin` for `run`
- Motivation: G1 — tool definitions and catalog pages must not grow with plugin count or schema width
- Progress: I have a short list of callable names and can detect catalog freshness

**Acceptance criteria**
- [x] `list_plugins` returns `CatalogView` rows: `name`, `version`, `description` (teaser), `valid`, `capability_class` (always present; `null` = no TTY claim).
- [x] `list_plugins` **must not** return `input_schema` (R-MCP-2).
- [x] Paginated catalog: when `truncated` is true, continue with `next_cursor` until `truncated` is false before concluding a capability is absent (TTY overlay rule).
- [x] `tools/list` carries `registry_version` in `_meta`, mirroring `CatalogView.registry_version` (R-REG-6).
- [x] Core MCP tool count remains ten regardless of plugin count (R-MCP-1).

**Non-goals**
- Does not cover per-plugin MCP tools or dynamic `tools/list` growth.
- Does not cover pulling one schema (APL-03).
- Does not cover invalid-plugin diagnostics beyond `valid` flag (APL-07).

**Why this framing works**
The porch stays cheap; discovery is a separate job from learning args. Collapsing schemas into `list_plugins` would violate G1 and recreate the “one tool per plugin” anti-pattern.

**Remaining assumptions**
- Description teasers are sufficient to disambiguate similarly named plugins; depth lives in `describe_plugin`.

---

### APL-03 — Learn one plugin’s args before I call it

**JTBD:** When I have chosen a plugin name but do not know its parameters, I want to pull that plugin’s input schema and metadata once, so I can construct a valid `run` call without over-fetching the whole catalog.

**Semantic contract**
- Actor: coding agent
- Situation: `plugin` name known from APL-02; `args` shape unknown or uncertain
- Motivation: minimize context while still admitting valid work
- Progress: I have `input_schema` (and version/snapshot metadata) for exactly one plugin

**Acceptance criteria**
- [x] `describe_plugin(plugin_id=…)` returns `input_schema`, `name`, `version`, `snapshot_id`, `source_sha256`, budgets/timeouts when published.
- [x] Unknown `plugin_id` returns a refusal outcome — not an empty success.
- [x] Schema is derived from type hints per R-PLUG-1; agent does not hand-author JSON Schema for publication.
- [x] Param naming documented: `run` uses `plugin`; `describe_plugin` uses `plugin_id` — same string value.
- [x] Agent workflow ≤5 turns for typical success: `list_plugins` → optional `describe_plugin` → `run` → `fetch`/`query`.

**Non-goals**
- Does not inline schema into `tools/list` or `run`’s MCP `inputSchema`.
- Does not cover execution or retrieval (APL-05, US-05).
- Does not validate args — Admit does that at `run` time (`admission.invalid_args`).

**Why this framing works**
Pull-one is the designed balance between discoverability and G1. Agents that skip `describe_plugin` when args are obvious save a turn; agents that need schema pay for exactly one plugin.

**Remaining assumptions**
- Type-hint subset in R-PLUG-7–10 is expressive enough for common agent-generated plugins.

---

### APL-04 — Publish a capability I wrote into the live catalog

**JTBD:** When I need a new local tool that does not exist yet, I want to add one Python plugin and see it become callable without restarting the server or adding MCP plumbing, so I can extend what Trestle can run during the same session.

**Semantic contract**
- Actor: coding agent (authoring via host file tools, shell, **or** MCP `publish_plugin`)
- Situation: task requires a reusable capability; existing catalog has no suitable `name`
- Motivation: G3 — extend by writing one file at the plugin surface, not editing the server
- Progress: new `name` appears in `list_plugins`; `run(plugin=name, …)` admits within R-REG-7 latency when deps exist

**Acceptance criteria**
- [x] **Filesystem path:** agent writes a single `.py` file under a watched `plugins/` directory with `@trestle` entry point and PluginSurface-only imports (R-BOUND-2, R-PLUG-6).
- [x] **MCP path:** agent calls `publish_plugin(source=<full python>, name=<optional>)` → `PublishView` on success or `publication.*` refusal with no `run_id`.
- [x] Registry watches filesystem (debounced 250 ms) and refreshes after `publish_plugin`; validate → snapshot → publish → `registry_version` increments (R-REG-1, R-REG-2).
- [x] No server restart required; no per-plugin MCP tools added (R-MCP-1).
- [x] Write-to-callable &lt; 2 s when third-party deps already in Trestle’s Python env (R-REG-7, R-G3-1). Missing dep → validation/`import_failed` outcome, not silent publish.
- [x] Validation failure leaves previous version serving for that name (R-REG-5, R-PLUG-19).
- [x] After publish, agent verifies via `list_plugins` then `describe_plugin` then `run`.

**Non-goals**
- Does not cover Python package layouts (`src/`, `pyproject.toml` entry points) — v0.1 is flat `*.py` per directory (see APL-08).
- Does not cover plugin author unit testing with stub Context (US-24) — same file, different actor job.
- Does not auto-install dependencies (R-PLUG-21).
- Does not cover `unpublish_plugin` — updates and overwrites only in v0.1.

**Why this framing works**
This is the agent-facing “how they get there” story. US-07 states the same mechanism for plugin authors; this story names the **agent mid-session** job: supply source (filesystem or MCP) → catalog updates → `run`. Sandboxed MCP hosts that cannot write the filesystem use `publish_plugin`; hosts with file access may use either path.

**Remaining assumptions**
- `publish_plugin` writes to primary `plugin_dirs[0]` when multiple dirs are configured (APL-08).
- One file = one entry point; multi-module packages are out of v0.1 scope.

---

### APL-05 — Invoke published work only through `run`, never as an MCP tool

**JTBD:** When I execute a plugin, I want a single stable `run(plugin=…, args=…)` call, so I do not confuse dynamic plugin names with the ten frozen MCP tools.

**Semantic contract**
- Actor: coding agent
- Situation: plugin is published; agent ready to execute
- Motivation: stable porch grammar — hosts inject `tools/list` every turn; plugin count must not change tool shape
- Progress: work is admitted or refused via `RequestOutcome`; admitted work returns `RunView` with `run_id`

**Acceptance criteria**
- [x] Calling a plugin name as an MCP tool fails as unknown-tool at the protocol level.
- [x] Correct invocation: `run(plugin="<name>", args={…}, wait_ms=…, idempotency_key=optional)`.
- [x] `admission.plugin_not_found` includes no `run_id`; message is actionable (“pick from `list_plugins`”).
- [x] `admission.invalid_args` includes no `run_id`; agent may re-call after `describe_plugin`.
- [x] Admitted runs execute from immutable snapshot captured at admit time (US-09) — later file edits do not change in-flight runs.

**Non-goals**
- Does not cover wait/join semantics (US-04).
- Does not cover evidence retrieval (US-05).
- Does not add per-plugin MCP tools “for convenience.”

**Why this framing works**
Mis-invocation was a top friction item in the usability assessment. This story makes the porch/agent contract explicit and testable independently of any one plugin’s behavior.

**Remaining assumptions**
- MCP host surfaces tool names clearly enough that agents can distinguish `run` from plugin names.

---

### APL-06 — Detect catalog changes without re-reading every schema

**JTBD:** When plugins are added, fixed, or removed during a long session, I want a monotonic `registry_version` I can compare across turns, so I know when to refresh `list_plugins` without polling schemas.

**Semantic contract**
- Actor: coding agent
- Situation: multi-turn session; filesystem may change under `plugins/` (agent-authored or human-edited)
- Motivation: cheap freshness signal aligned with G1
- Progress: agent refreshes catalog only when `registry_version` changes

**Acceptance criteria**
- [x] Every `list_plugins` response includes `registry_version`.
- [x] `tools/list` `_meta.registry_version` mirrors the same publication fact (R-REG-6).
- [x] File drop, fix, or removal that changes publication increments `registry_version`.
- [x] Validation-only failure that keeps previous version serving does not falsely imply new behavior for that name.
- [x] Agent playbook documents: after writing a plugin file, call `list_plugins` and check version bump before `run`.

**Non-goals**
- Does not guarantee push notifications — agent may poll `list_plugins` or compare `tools/list` meta.
- Does not cover run-level staleness (snapshot pinned at admit — US-09).

**Why this framing works**
Hot reload is a feature only if agents can detect it cheaply. Version mirroring ties catalog freshness to the same channel agents already see every turn.

**Remaining assumptions**
- `tools/list_changed` may exist as optimization; correctness does not depend on it (R-REG-2).

---

### APL-07 — Tell “not published,” “invalid source,” and “ran badly” apart

**JTBD:** When a plugin name fails, I want outcomes that tell me whether to fix the file, pick another name, or inspect a completed run, so I do not treat validation failure as execution failure or vice versa.

**Semantic contract**
- Actor: coding agent
- Situation: `run` or `describe_plugin` fails, or catalog shows `valid=false`
- Motivation: US-02 / US-18 distinguishability at the plugin boundary
- Progress: agent chooses bootstrap, edit plugin, different name, or `query(last_error)` on a real `run_id`

**Acceptance criteria**
- [x] Unknown name at `run` → `admission.plugin_not_found`, no `run_id`.
- [x] Known name, bad args → `admission.invalid_args`, no `run_id`.
- [x] Published name with `valid=false` in catalog → previous good snapshot still serves until fixed; `list_plugins` exposes `valid` (and `list_plugins(invalid=True)` when supported).
- [x] `describe_plugin` on missing name → refusal, not empty schema.
- [x] Post-admit failure → terminal `RunView` with `run_id`; use `query(last_error)` — not an admission code.
- [x] TTY unreadiness → `admission.tty_not_ready`, no `run_id` — distinct from `valid=false` (US-18).

**Non-goals**
- Does not add a catalog “health API” or daemon pulse fields.
- Does not cover hostile-plugin containment (US-08).

**Why this framing works**
Agents that conflate `plugin_not_found` with `failed` waste turns on fetch/query. This story encodes the same vocabulary law as US-02/US-18 for the plugin-specific confusion classes observed in onboarding.

**Remaining assumptions**
- `import_failed` surfaces through validation/`valid=false`, not as a run state.

---

### APL-08 — Register capabilities from folders the project already uses

**JTBD:** When my team keeps scripts in a repo or package tree outside `~/.trestle/plugins/`, I want Trestle to watch those locations too, so I do not have to copy files into a second plugins home to run them.

**Semantic contract**
- Actor: coding agent or operator configuring the local server
- Situation: canonical plugin sources live in project paths (`tools/`, `packages/foo/scripts/`, monorepo dirs)
- Motivation: G3 at project scale — one logical capability set, no duplicate maintenance
- Progress: plugins from all configured directories appear in one `list_plugins` catalog; agent uses same APL-02–05 flow

**Acceptance criteria**
- [x] Server accepts **multiple** plugin directory paths in configuration (CLI flag, `config.toml`, or env — product choice).
- [x] Registry scans each configured directory for `*.py` entry points; unified catalog with stable `name` keys.
- [x] Name collision across directories has deterministic resolution or explicit validation error — not silent override.
- [x] `trestle doctor` (or equivalent) lists configured paths and plugin count per path.
- [x] Agent playbook documents how to set paths for Cursor MCP (`TRESTLE_HOME` + config) in project repos.

**Non-goals**
- Does not require Python package discovery (`pyproject.toml` entry points, namespace packages) in v0.1.
- Does not mount arbitrary import trees — still one `@trestle` function per published file.
- Does not change MCP tool surface.
- **v0.1 status:** shipped — `--plugin-dir`, `config.toml [plugins].paths`, `TRESTLE_PLUGIN_DIRS`, doctor `plugin_search_paths`.

**Why this framing works**
The “point Trestle at my folder” job is real for local web-service deployments and project repos. Multi-path config ships via CLI, `config.toml`, and env without duplicating APL-04’s author job.

**Remaining assumptions**
- Symlinks into `plugins/` remain an acceptable v0.1 workaround for exotic layouts.
- Package-layout plugins remain a future amendment.

---

## Set-level map

| Phase | Story | MCP / filesystem |
|-------|-------|------------------|
| Attach | APL-01 | `tools/list`, `list_plugins` |
| Discover | APL-02 | `list_plugins` |
| Learn args | APL-03 | `describe_plugin` |
| Supply | APL-04 | write `plugins/*.py` → hot reload |
| Invoke | APL-05 | `run` |
| Freshness | APL-06 | `registry_version` |
| Recover | APL-07 | admission vs terminal codes |
| Configure | APL-08 | multi-path config |

**Suggested golden path (≤6 turns after bootstrap):**  
APL-01 → APL-02 → APL-03 (if needed) → APL-05 → US-05 fetch/query.

**Dependency notes**
- APL-04 depends on APL-01’s writable plugin path existing.
- APL-08 enhances APL-04 but is not required for single-directory installs.
- APL-05–07 assume US-02/US-09 kernel stories already hold.

---

## Open Questions (resolved)

1. **`trestle init`** — **Resolved:** shipped; creates home, `plugins/`, seeds `echo.py`.
2. **APL-08 wiring** — **Resolved:** all three — `--plugin-dir`, `config.toml [plugins].paths`, `TRESTLE_PLUGIN_DIRS` (CLI replaces list when set).
3. **Package-shaped plugins** — Deferred; separate initiative.
4. **Agent upload without filesystem write** — **Resolved:** `publish_plugin` (fixed tenth tool; R-MCP-1 still holds).
5. **Streamable HTTP vs stdio** — Both documented; stdio remains default (R-FMC-1).

---

## Critic rollup (cycle 1)

| ID | Severity | Finding | Resolution |
|----|----------|---------|------------|
| C1 | minor | APL-01 and APL-02 both touch `list_plugins` | Kept — different situations (attach vs steady browse); bounded with non-goals |
| C2 | minor | APL-04 overlaps US-07 | Kept — different actor (agent vs author); cross-referenced |
| C3 | major | APL-08 not testable on main today | **Resolved** — `tests/test_plugin_publication_config.py` |
| C4 | minor | APL-05 thin | Kept — distinct mis-invocation class with clear acceptance tests |

**Gate:** pass — no unresolved major findings after C3 bound.
