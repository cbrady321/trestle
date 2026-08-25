# Plan — Trestle console access & operator telemetry (scope)

Status: **Phase C complete** (agent MCP assess + playbook + verify). **Porch is OUT** — no `console/porch/`, no `/porch/v1` HTTP adapter, no web Console MVP from [`spec-console-lens-mvp.md`](spec-console-lens-mvp.md) (superseded).  
**Shipped:** agent MCP usability assessment, playbook, host wiring, smoke tests. **Future (unspecified):** human **sessions / telemetry** inspection — **not** named “porch” or “Console product.”  
**Daytona study:** closed — almost nothing to port; public web docs only. **No AGPL vendor source.**

## Guiding light

### Nearer decisions

- **Parent (SSOT):** `trestle-requirements.md` — `## 1. Goals` — Minimize agent context consumed per unit of work done.
- **Parent (SSOT):** `trestle-requirements.md` — `## 1. Goals` — Extend capability by writing one Python file — no restart, registry edit, or MCP plumbing for new plugins.
- **Parent (SSOT):** `trestle-requirements.md` — `## 1. Goals` — Bounded, backend-independent query interface over run history (nine named views).
- **Parent (SSOT):** `hld/hld-interface-architecture-trestle.md` — `## Goals & Non-Goals` — Nine MCP tools only; `RequestOutcome` refusals have no `run_id`; only Project emits toward MCP.

### Larger picture

- **Grandparent (SSOT):** `hld/hld-interface-architecture-trestle.md` — `## Goals & Non-Goals` — CLI may be richer than MCP; same verb must not have different admission rules across transports.

### Why we believe that

- Agents need bounded retrieval over run evidence via MCP (G1/G7). Humans may eventually inspect **sessions and telemetry** — a separate, later surface; not a dashboard port and **not** called porch.

## Study conclusion — Daytona (Phase A closed)

**Owner conclusion (2026-08-25):** After public-architecture review, **almost nothing in Daytona is worth clean-room porting** for Trestle. The product is a remote-compute control plane (sandboxes, runners, toolbox, org/billing); Trestle is a local execution ledger with nine MCP tools. Overlap is superficial (“operators look at runs”) — the implementations solve different problems.

| From public Daytona material | Verdict | For Trestle |
|------------------------------|---------|-------------|
| Compute plane (runners, daemon, snapshots, volumes, GPU) | **Drop** | User does not want sandboxing; G3 is one Python file |
| Dashboard (lists, log panes, playground, terminal iframe) | **Drop** | Anti-G1/G4; strategic correction killed log viewer |
| Org, billing, webhooks, OIDC | **Drop** | Localhost single-user v0.1 |
| Thick MCP / toolbox APIs | **Drop** | Nine tools only |
| **Interface-plane patterns** (extra HTTP/UI in front of MCP) | **Drop for now** | Public web informed category only; **Porch HTTP adapter cancelled** (owner 2026-08-25) |
| Public OpenAPI / SDK shapes from that product | **Do not port** | N/A — no human HTTP layer in current scope |

**Implication:** No clean-room **implementation** track remains from Daytona. Agent path = existing MCP. Human path = **future sessions/telemetry** (name TBD), not Porch/Console web.

**Banned product names (human UI):** `porch`, `Porch`, `/porch/v1`, `console/porch/`, “Console product” as a dashboard. Freeze HLD may still say **MCP porch** for `trestle serve` — that is the agent adapter, not a human product.

## Evidence vs human inspection — do not conflate

| Surface | What | Who | How (now) |
|---------|------|-----|-----------|
| **`console/*` evidence** | Wrapper stdout/stderr on disk per run (R-LIM-7). | **Agents** | **Nine MCP tools** after `evidence_finalized`: `query(run_tail)`, `fetch` (grep/tail/range), `last_error`; TTY terminus = `fetch(art_…)`. |
| **OperatorContract (CLI)** | Ops diagnostics and retention (`doctor`, `pin`, `unpin`, `recover`). | **Humans** | `trestle` CLI — v0.1; **`query` / `fetch` are MCP-only** (not CLI subcommands yet). |
| **Sessions / telemetry UI** | Human inspection of run history, bounded telemetry — **not** a log dashboard. | **Humans (future)** | **Not scoped.** Likely sessions + telemetry vocabulary; **no porch naming.** Spec TBD when requirements exist. |

**Owner requirements (2026-08-25):**

1. **Must:** Agents reach `console/*` evidence through **MCP** (`trestle serve`).
2. **Out:** Porch HTTP adapter, `console/web/` MVP, any human product named porch.
3. **Later:** Way for humans to inspect **sessions / telemetry** — design separately; retrieval-first like agents, not Daytona-shaped UI.

### Agent MCP console support (Must — kernel)

Agents use **`trestle serve` MCP only** (no HTTP BFF):

| Agent job | MCP tool | Console-related use |
|-----------|----------|---------------------|
| Wait for run to finish | `run` + `wait_ms`, `await_runs` | G6 — no live `run_tail` while running (R-QB-28) |
| See wrapper pipe lines | `query` view `run_tail` | After finalize only; bounded rows |
| Find a line / pattern | `fetch` `grep` / `tail` / `range` | Minimal context (G1) |
| See why it failed | `query` view `last_error` | Often sufficient alone |
| TTY-class output | `fetch` on `art_…` | Not `run_tail` for terminus |
| Gaps / honesty | `RunView.limits_exceeded` | Counted suppressions |

**Phase C deliverables (complete):**

1. **Assess** — `hld/agent-mcp-usability-assessment.md` ✓
2. **Document** — `docs/agent-console-mcp.md` ✓
3. **Verify** — pytest + `scripts/smoke_agent_mcp.py` ✓
4. **Do not** build Porch, `console/`, or web UI under this plan ✓

**Cancelled (do not implement):** [`spec-console-lens-mvp.md`](spec-console-lens-mvp.md), [`handoff-console-lens-implementer.md`](handoff-console-lens-implementer.md) Porch/web scope, [`console-port-worthiness-verification.md`](console-port-worthiness-verification.md) **Proceed to implement** Porch rows.

## Goals & Non-Goals

**This-node goal (complete):** **Assessed and shipped** agent MCP usability (document + verify nine-tool retrieval). **Defer** human sessions/telemetry UI to a future spec — **no porch naming.**

**Non-goals:** Porch HTTP; `console/porch/`; `console/web/`; ninth MCP tool; log dashboard; live tail; sandbox/toolbox; vendor source; relitigating Daytona.

### Phase status

| Step | Status | Deliverable |
|------|--------|-------------|
| A Study | Done | § Study conclusion |
| B Spec (Porch/web) | **Cancelled** | superseded by owner decision |
| C0 Worthiness (Porch) | **Obsolete** | verification doc historical only |
| **C1 Assess agent MCP** | **Done** | `hld/agent-mcp-usability-assessment.md` |
| **C2 Agent console MCP** | **Done** | `docs/agent-console-mcp.md` + README + `.cursor/mcp.json` |
| D Sessions/telemetry UI | **Not started** | future requirements + spec |

---

## 0. Phase C — agent MCP console (complete)

### 0.1 Mission

**First:** determine the best way for agents to use `trestle serve` MCP in real hosts (Cursor, Claude Desktop, etc.) — wiring, onboarding, workflow, and friction — without reopening the nine-tool freeze unless assessment proves a gap.

**Then:** agents reach run **`console/*` evidence** through that path — `query` + `fetch` after finalize, bounded slices, retrieval-first. Operators use **`trestle doctor` / `pin` / `recover`** on the CLI today; **`query` / `fetch` require MCP** (`trestle serve`). A future **sessions / telemetry** human surface is out of scope here and will **not** use the name porch.

### 0.2 Read order

1. [`hld/hld-interface-architecture-trestle.md`](hld-interface-architecture-trestle.md) — nine tools, ViewRow, fetch windows, mis-invocation table, R-QB-28, thin `tools/list` (R-MCP-1–3).
2. [`trestle-requirements.md`](../trestle-requirements.md) — `## 1. Goals`, R-LIM-7, R-QB-28.
3. `trestle/server/control.py` — ControlSurface mapping; `trestle serve` entry.
4. Host MCP config in repo (if any) — `.cursor/mcp.json`, README examples.

### 0.3 Build order

| Step | Deliverable | Done when |
|------|-------------|-----------|
| **1** | `hld/agent-mcp-usability-assessment.md` | Assessment memo: host wiring options, agent session workflow, friction inventory, ranked remediation (docs / host snippet / skill / tests / freeze-amendment last resort); **recommendation** for playbook shape; gates step 2 |
| **2** | `docs/agent-console-mcp.md` | Playbook per assessment: agent flow for console evidence; `run_tail` vs `fetch`; grep; TTY `art_…`; not-finalized; `limits_exceeded`; mis-invocation table; host attach instructions |
| **3** | Root README pointer | One paragraph + link to playbook; `trestle serve` MCP wiring per assessment |
| **4** | Smoke verification | Documented commands: sample run → `query(last_error)` / `fetch` tail on a handle; matches freeze |

#### Step 1 — assessment dimensions (score each; evidence required)

Run at least one **real agent session** (or scripted MCP client) against `trestle serve` before writing the playbook. Evaluate:

| Dimension | Question |
|-----------|----------|
| **Host attach** | How does the agent process spawn or attach to `trestle serve` stdio? What config snippet works in Cursor / Claude Desktop / other? |
| **Cold start** | What does the agent see on first turn (`tools/list` size, descriptions)? Is G1 honored in practice? |
| **Run workflow** | `run` → `wait_ms` / `await_runs` → terminal frame — where do agents stall or mis-call? |
| **Console retrieval** | After finalize: `last_error` vs `run_tail` vs `fetch` grep/tail — which path is discoverable without dumping context? |
| **Refusals & honesty** | `projection.not_finalized`, `admission.*` without `run_id`, `limits_exceeded` — are errors actionable? |
| **Plugin discovery** | `list_plugins` + `describe_plugin` pull model — do agents over-fetch schemas? |
| **Remediation fit** | For each friction: docs-only, copy-paste host JSON, Cursor skill/rule, example project, pytest harness, or **freeze amendment** (last resort — needs explicit owner sign-off). |

**Assessment output must include:** ranked recommendation (one primary path + alternates), explicit **out of scope** list (no tenth tool, no HTTP BFF), and a short **playbook outline** for step 2.

### 0.4 Verify locally

```bash
pip install -e ".[dev]"
pytest -q                                    # 104 tests incl. MCP stdio smoke
python scripts/smoke_agent_mcp.py            # ControlSurface golden path
trestle serve                                # MCP — agent path (query/fetch via tools)
trestle doctor                               # CLI operator path
```

### 0.5 Done-when (Phase C)

- [x] `hld/agent-mcp-usability-assessment.md` published with recommendation and playbook outline.
- [x] `docs/agent-console-mcp.md` published (implements assessment).
- [x] README links agent MCP path per assessment.
- [x] No `console/` tree added.
- [x] Kernel diff docs-only (or test fixes if assessment finds real gaps).
- [x] 104 pytest tests pass; MCP blocking-run contract locked in `tests/test_mcp_stdio_smoke.py`.

---

## 0-legacy. ~~Porch / web kickoff~~ (CANCELLED 2026-08-25)

The following sections are **archived intent only** — **do not implement.** Owner: Porch OUT; human inspection will be **sessions/telemetry** (name TBD), not porch/Console web.

<details>
<summary>Collapsed: former §0.2–§0.9 Porch/web (obsolete)</summary>

Former deliverables: `console/openapi.yaml`, `console/porch/`, `console/web/`, `/porch/v1/*`, `/bind`, `/ledger`, `/ledger/{handle}/ask`. All cancelled.

See git history or [`spec-console-lens-mvp.md`](spec-console-lens-mvp.md) for archaeology only.

</details>

---

## How the clean-room process works (archived — Porch track cancelled)

Phase A study is **closed** (§ Study conclusion). Phase B Porch/web spec is **superseded** (owner 2026-08-25). **Phase C complete** — agent MCP console docs + verify. Human **sessions/telemetry** inspection is a **future** spec — not porch, not `console/`.

The discipline below remains valid if a future human surface is specified; **do not implement** `console/porch/` or `console/web/` under this plan.

### Three phases (sequential gates)

```
Phase A — Study (closed)       Phase B — Spec (cancelled)         Phase C — Complete
────────────────────────       ─────────────────────────          ───────────────────────────
Public docs only               Porch + Console spec (obsolete)    C1 assess → C2 playbook + verify
Verdict: almost nothing        superseded by owner decision       agent-mcp-usability-assessment.md
to port from vendor                                               docs/agent-console-mcp.md + smoke tests
```

| Phase | Status | Deliverable |
|-------|--------|-------------|
| **A — Study** | Done | § Study conclusion |
| **B — Spec (Porch/web)** | **Cancelled** | [`spec-console-lens-mvp.md`](spec-console-lens-mvp.md) — historical only |
| **C — Agent MCP usability** | **Done** | Assessment memo + playbook + README + smoke tests; no `console/` tree |
| **D — Sessions/telemetry UI** | **Not started** | Future requirements; name TBD (not porch) |

### The Chinese wall (non-negotiable)

| Side of the wall | Role | Allowed | Forbidden |
|------------------|------|---------|-----------|
| **Before the wall** | Spec authors | Translate operator jobs into Trestle verbs; name screens and HTTP paths in **our** vocabulary | Using their file paths, DTO names, or component tree as a template; checking out their repo “to see layout” |
| **After the wall** | Implementers | Implement spec §2–§5; ask spec owner if UX is ambiguous | Clone/fetch/submodule their repos; web-search their code; `vendor/` copy “for reference”; copy-paste their UI or OpenAPI client |

If implementers are stuck, they **change the spec** or ask — they do **not** peek at the other product to resolve ambiguity. That peek breaks the wall.

### What “perfect” clean-room means here

1. **Independent expression** — Routes, JSON field names, folder layout, and UI copy speak Trestle (runs, handles, views, lens). Not sandboxes, runners, toolbox, playground.
2. **Contract fidelity** — Porch returns the same pictures MCP would (`BoundedView`, `FetchSlice`, `RequestOutcome`, etc.) with the **same admission rules**. Console is a human porch, not a second kernel.
3. **No contamination** — Zero AGPL source in the customer-facing tree. Fresh shadcn init. Client generated from **our** OpenAPI.
4. **Honest UX** — No fake live logs while Trestle says “not finalized yet.” No path pickers. No tenth MCP tool.
5. **Review against spec, not familiarity** — Reviewers ask “does it match `spec-console-lens-mvp.md`?” not “does it look like their dashboard?”
6. **Naming wall** — Console/Porch uses **our** HTTP paths, Python modules, and wire envelopes (§ Naming wall below). Kernel MCP tool names (`query`, `fetch`, …) stay frozen — they are Trestle’s own design, not a vendor transcription. The legal risk is similarity at the **porch/UI** layer; that layer gets distinct names by spec.

### Naming wall (distinct surface, same kernel pictures)

Implementers **must** use the names in [`spec-console-lens-mvp.md`](spec-console-lens-mvp.md) §3 (HTTP **Porch** API) and §3.4 (Python). Do not “align” with another product’s route or method names for familiarity.

| Layer | Rename? | Rule |
|-------|---------|------|
| **Kernel / MCP** (`query`, `fetch`, `list_plugins`, …) | **No** — freeze | Already Trestle-native. Changing them needs a freeze amendment, not clean-room. |
| **HTTP Porch** (`/porch/v1/…`) | **Yes — spec owns** | Ledger/evidence/registry vocabulary; no `/sandboxes`, `/toolbox`, `/sessions`, camelCase DTOs. |
| **Python `console/porch/`** | **Yes — idiomatic** | `read_ledger_rows`, `read_evidence_chunk`, `PorchGateway` — not `get_sandbox_logs`. |
| **React UI** | **Yes** | `/bind`, `/ledger`, `/ledger/{id}/ask` — not `/dashboard`, `/playground`. |
| **Wire envelope** | **Yes** | `{ "issued": bool, "body": {…freeze picture…} }` — not `{ "ok", "data" }` REST clones. |

**Inside** `body`, freeze field names (`run_id`, `next_cursor`, `registry_version`, …) pass through unchanged — that is contract fidelity, not copying another API.

**Reviewer check:** grep the Console tree for forbidden nouns (§3.1 in spec) and for camelCase resource names on the Porch wire. Zero hits required.

### Artifact chain (where truth lives)

| Artifact | Purpose |
|----------|---------|
| This plan §8 | **What** operator jobs we clean-room and **why** (value to G1/G3/G7) |
| [`spec-console-lens-mvp.md`](spec-console-lens-mvp.md) | **How** to build (Lens HTTP, UI routes, protocol, similarity test) — implementer SSOT |
| [`handoff-console-lens-implementer.md`](handoff-console-lens-implementer.md) | Fresh-agent packet; binds parent goals; repeats the wall |
| `console/openapi.yaml` (to be written) | Machine-readable Lens surface; feeds generated TS client |
| `console/` + `console/porch/` (to be written) | The product — Python package **`porch`**, not `lens` |

Kernel freeze (`hld-interface-architecture-trestle.md`) is unchanged by this process. Console does not reopen nine tools or spawn-per-run.

### When something fails the process

| Symptom | Response |
|---------|----------|
| Implementer opened another vendor’s source | Stop. Discard contaminated work. Restart Phase C in fresh context from spec only. |
| UI “feels like” a sandbox dashboard | Fail similarity test. Rename routes, nouns, and API shapes per spec §3. |
| Lens accepts a query MCP would refuse (or vice versa) | Fail freeze. Fix Porch mapping; do not patch Kernel in this hop. |
| Need live terminal to match competitor | Out of scope until requirements amend live observation. Use post-finish logs. |
| Legal review asks for evidence | Point to: public-doc study only, written spec predating implementation, Chinese wall in handoff, MIT stack, no their tree in git history. |

**This plan’s bar is engineering discipline, not legal sign-off.** Corporate counsel may still want a formal memo; the process above is what engineering can prove.

---

## 1. How Daytona actually complements Trestle

Daytona and Trestle look alike in a slide: “agents run work; operators look at it.” They are not the same product.

| | **Trestle (shipped)** | **Daytona (public architecture)** |
|--|------------------------|-----------------------------------|
| Job | Durable **local** execution ledger; evidence on disk; thin MCP | Elastic **remote** composable computers (sandboxes) |
| Agent aperture | Nine stable tools; plugins do not grow `tools/list` | MCP + SDKs aimed at sandbox lifecycle and in-box Toolbox |
| Human aperture | CLI richer than MCP (`doctor`, `--sql`, recover) | Dashboard + Playground + generated SDK snippets |
| Store | Ledger ndjson; named views | Postgres/Redis metadata; snapshot registry; volumes |
| Execution | Spawn-per-run child on the host | Runners + in-sandbox daemon (Toolbox API) |

**Complement, not substitute.** Daytona’s interface-plane patterns informed category thinking only. Trestle’s **agent** path is MCP (`trestle serve`). **Human** inspection may later be **sessions/telemetry** — not a porch-named HTTP adapter or dashboard port.

---

## 2. Daytona planes — keep vs drop (by construction)

Public Daytona split:

1. **Interface plane** — SDK, CLI, Dashboard, MCP, SSH  
2. **Control plane** — NestJS API, preview proxy, snapshot builder, sandbox manager, Auth0/OIDC, SMTP, PostHog, Redis, Postgres  
3. **Compute plane** — runners, sandbox daemon / Toolbox, snapshot store, volumes  

**Rule:** Compute plane is out. Control-plane *sandbox orchestration* is out. What remains is “how do clients talk to a control plane” and “what does a human UI show.”

```
Daytona                          Trestle analog                    Verdict
---------                        --------------                    -------
Compute: runners/daemon          spawn-per-run child               DROP
Control: sandbox manager         Conductor + Scheduler             DROP (already have local)
Control: snapshot builder        plugin file on disk (G3)          DROP
Control: volumes / registry      work/ + outputs/ + evidence/      DROP
Control: Auth0 / org / SMTP      localhost, single user            DROP
Control: Redis / Postgres        ndjson ledger                     DROP
Interface: Dashboard             Console (does not exist)          STUDY → port *behavior*
Interface: OpenAPI + SDKs        no HTTP API                       STUDY → generate, don't copy
Interface: MCP                   trestle serve (nine tools)        DO NOT thicken
Interface: CLI                   trestle CLI (OperatorContract)    already richer than MCP
Interface: SSH / VNC / terminal  G9 overlay; no live tail v0.1     DEFER (conflicts R-QB-28)
Control: preview proxy           none                              PARK (web-plugin later)
```

---

## 3. Scoring gate (how we decide a port)

Apply in order. A slice that fails a gate is not a clean-room candidate for Console MVP.

| Gate | Question | Fail means |
|------|----------|------------|
| **G0 Compute** | Is this sandbox lifecycle, isolation, snapshot, volume, runner, daemon, Toolbox FS/git/exec, GPU, pause? | Drop. User-stated non-goal + not Trestle-shaped. |
| **G1 Context** | Does a human using this reduce agent tokens/turns, or does it grow `tools/list`? | Drop if it implies more MCP tools or inlined plugin schemas. |
| **G3 Plugins** | Does it help *authors/operators* see and invoke one-file plugins without server edits? | Nice if yes; do not invent a snapshot/image bake to replace G3. |
| **G7 Views** | Can it be served from the nine named views + `fetch` windows after `evidence_finalized`? | If it needs live tail of a running run, it is not v0.1 (R-QB-28). |
| **Freeze** | Does it map to existing ControlSurface verbs (`run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`/`unpin`, `list_plugins`, `describe_plugin`)? | If it needs a new Kernel port, HTTP semantics that diverge from MCP admission, or a fourth transport with different rules — **new unit**, do not sneak it into Console. |
| **License** | Can we specify the *behavior* from public docs and implement on MIT/permissive stack? | If the only way is reading AGPL TSX/Go — stop; rewrite the spec from docs/UX notes only. |

Full process (phases, Chinese wall, artifacts, failure responses): **§ How the clean-room process works** above. Implementer protocol detail: [`spec-console-lens-mvp.md`](spec-console-lens-mvp.md) §1.

---

## 4. Capability map (the actual ranking)

Scores: **Port pattern** (clean-room behavior for Console MVP), **Park** (later product, maybe freeze unit), **Drop**.

| Daytona behavior (public) | Complements which Trestle goal | Trestle already has | Verdict | Why |
|---------------------------|--------------------------------|---------------------|---------|-----|
| **Dashboard as operator lens** (list objects, open one, inspect logs/files) | G1 (humans off the porch), G7 | CLI `query` / `fetch`; no web | **Port pattern** | 80/20 of Console. Map to `query(recent_runs\|run\|…)`, `fetch`, `await_runs`. |
| **Plugin / catalog screen** | G3, G8 | `list_plugins`, `describe_plugin` | **Port pattern** | Human catalog; do not dump schemas onto MCP `tools/list`. Surface `valid` when Kernel exposes it. |
| **MCP connection / `mcp init` JSON** | G1 (correct wiring, less retry churn) | `trestle serve` stdio | **Port pattern** | Settings screen + root README; generate host config, do not copy Daytona CLI. |
| **OpenAPI → generated TS/Python clients** | Unlocks Console without hand-copied SDKs | None (stdio only) | **Park as transport unit** | Generate from *Trestle* OpenAPI. A thin HTTP adapter is **not** in the freeze; same verbs / same admission as MCP or it is forbidden drift. Until that unit exists, Console talks via a **local stdio bridge** (sidecar that is Console backend, not Kernel). |
| **Playground: form → generated snippet** | G3 (try a plugin), G8 | none | **Park (Console v0.2 UI)** | Useful; not required for ledger inspection. If built, emit `run` args from `describe_plugin`, never a per-plugin MCP tool. |
| **Preview proxy** (`{port}-{id}.host`, cookie TTL) | Local web plugins / later TTY | none | **Park** | Pattern is “authenticated iframe to a backend URL.” Trestle has no `terminal.url` or preview URL today. |
| **Web terminal / VNC / Computer Use** | G9 adjacency | TTY overlay: oneshot + `fetch(art_…)`; **no live tail** | **Drop for MVP** | Daytona’s playground is mostly an iframe to a daemon URL. v0.1 forbids live `run_tail`/`run_events` (R-QB-28). Post-finalize log/artifact viewer is the honest substitute. |
| **Toolbox API** (in-box FS, git, process, log stream) | Conflicts G8 / R-INV-1 | `fetch` / `query` / child `work/` | **Drop** | Second filesystem grammar for agents/operators. Trestle files consequences under the run. |
| **Thick MCP** (sandbox create/list/exec as many tools) | Anti-G1 | Nine tools | **Drop** | Do not port tool taxonomy. Console is not allowed to become a shadow MCP with 40 RPCs. |
| **Org, billing, webhooks, invitations, PostHog** | none | localhost single-user | **Drop** | Enterprise SaaS. |
| **OIDC / SSO** | none for MVP | none | **Drop until multi-user is scoped** | |
| **Runners, snapshots, volumes, GPU, pause** | User: we don’t care about sandboxing | local spawn | **Drop** | Compute plane. |

### What would actually “level up” capabilities

Ranked by leverage on G1/G3/G7 **without** sandboxing:

1. **Human projection of the nine views** — operators complete diagnosis that would otherwise burn agent turns (`query` + `fetch`). This is G1 by moving work off the model, and G7 by using the named views as the UI grammar.
2. **Local bridge process** — browser cannot speak stdio MCP; a Console backend that is a *client* of `trestle serve` (or of ControlSurface in-process) is the missing infra. This is Daytona’s “API as entry point” *role*, not NestJS+Postgres.
3. **Generated client from our spec** — once (2) has a typed surface. Prevents a copied AGPL `libs/api-client`.
4. **Catalog + describe UX** — makes G3 visible to humans (what can I drop in as a file?).
5. **Deferred:** playground codegen, preview iframe, live stream (needs R-QB-28 amendment).

**Explicit non-level-up:** sandbox isolation, snapshot bake, Toolbox, org admin. Those are a different company.

---

## 5. Console MVP implied by this ranking (preview, not the spec)

Screens that survive the gates, each mapped to Trestle tools (spec will expand this):

| Screen | MCP / ControlSurface | Notes |
|--------|----------------------|--------|
| Run list | `query(recent_runs)` / `recent_failures` | Status only for non-finalized (R-QB-28) |
| Run detail | `query(run, last_error, run_events, run_provenance, run_artifacts, artifact_refs)` after finalize; `query(run)` while running | No live tail |
| Log / artifact fetch | `fetch` on handles; `query(run_tail)` only when finalized | Trestle `console/*` = evidence streams, not this product |
| Plugin catalog | `list_plugins`, `describe_plugin` | Optional `run` from a form later |
| Connection | not a Kernel tool | How the bridge reaches `trestle serve` |
| Pin / cancel | `pin`/`unpin`, `cancel` | Operator actions; keep admission identical to MCP |

**Transport decision (this plan):** MVP Console uses a **local bridge** (Console’s own small backend) as an MCP/ControlSurface client. A Kernel HTTP porch is a **separate freeze-impacting unit** — do not implement it as a side effect of CSS.

---

## 6. Work sequence (historical)

Phases A–B complete. **Phase C complete** (§0.5 done-when all [x]). **Next:** Phase D sessions/telemetry UI — not started; requires new spec.

1. ~~Accept ranking~~ — done.  
2. ~~Console MVP spec~~ — cancelled (owner 2026-08-25).  
3. ~~**C0 Verify**~~ — [`console-port-worthiness-verification.md`](console-port-worthiness-verification.md) obsolete (Porch cancelled).  
4. ~~**C1 Assess + C2 Implement**~~ — §0.5 complete.  
5. Trestle hygiene — optional parallel.

---

## Strategic correction — retrieval, not log viewing

**Binding for Phase C.** Supersedes log-pane / artifact-browser as MVP centerpieces.

Trestle’s product promise is **G1 + G4 + G7**: keep everything on disk, push almost nothing into context, pull **bounded slices** that answer the next question (`DefaultAgentSuccess`, named views, `fetch` with grep/tail/range). **Agents** use MCP today; **humans** use CLI. A future sessions/telemetry UI should follow the same retrieval grammar — not a scrollable log viewer.

| Want | Do not build |
|------|----------------|
| Focused question → small answer + continuation handle | Log scroller, artifact file browser as MVP |
| `query(last_error)`, `fetch(grep=…)` composed explicitly | Dump `run_tail` into UI “just in case” |
| Retrieval chain visible (bytes fetched) | Live tail, websocket stream |

**No tenth MCP tool.** Question-answering = compose existing `query` + `fetch` — not a magic NL endpoint.

---

## 8. Job IDs (reference — Porch/web cancelled)

Canonical **active** build list is **§0.3**. This table is **archived** from the cancelled Porch/web track — kept for job vocabulary only. **Do not implement** the Porch/UI column.

| ID | Job | Verdict (if revived) | Was Porch / UI — **cancelled** |
|----|-----|---------|------------|
| **B1** | Browser → Trestle | **Must** | Porch MCP client; spawn `trestle serve` |
| **K1** | Binding / health | **Must** | `GET /porch/v1/binding` |
| **K2** | Host wiring JSON | **Should** | `GET /porch/v1/host_wiring` |
| **R1** | Recent runs | **Must** | `/ledger` |
| **R2** | Failures tab | **Should** | `recent_failures` view |
| **R3** | Run identity | **Must** | `/ledger/{handle}/ask` |
| **R4** | Honest not-finalized | **Must** | ask page wait state |
| **R5** | Join/wait | **Could** | `join_waits` — skip MVP |
| **Q1** | Bounded answer card | **Must** | chunk + budget + handle on ask page |
| **Q2** | Retrieval chain | **Should** | which calls, how many bytes |
| **D1** | `last_error` | **Should** | default ask preset |
| **D2–D4** | events, provenance, refs | **Could** | later |
| **L1** | Log pane | **Drop** | no scroller |
| **A1–A2** | Artifact browser | **Could** | chunk-only if needed |
| **P1–P2** | Registry | **Should** | `/registry` |
| **M1–M2** | cancel, pin | **Could** | skip MVP |
| **X1** | Refusal UX | **Must** | no `run_id` on admission |
| **X2** | Admit from UI | **Out** | no `run` button |

---

## 9. Open decisions (obsolete — Porch cancelled)

Former Porch implementer decisions (spawn vs attach, MCP transport) **do not apply**. Future sessions/telemetry UI decisions are **not started**.

---

## 10. Background — Daytona reference study (archived)

Sections **1–5** record the public-doc study that concluded **§ Study conclusion**. **Do not re-open** vendor comparison. If a feature is not in **§0 Phase C**, it is out of scope.

---

## 11. Closure & next — Phase E (async + operator API)

**Phase C is complete** (§0.5 done-when all `[x]`). **Owner sign-off (2026-08-25):**

| Decision | Choice |
|----------|--------|
| Agent transport | **Retain stdio MCP** — do not replace with HTTP REST for agents |
| Human surface | **FastAPI/Starlette operator API only** (Phase D) — read-only, not MCP replacement |
| Priority | **F5/F6 first** → push + CI → Phase D spec → optional async conductor hardening |

**Freeze amendments landed:** `trestle-requirements.md` v0.8 (R-WAIT-14, R-OPS-10, transport note); HLD `ControlSurface.run` composition + operator HTTP rules.

### Phase E program (ordered)

| Step | Status | Deliverable |
|------|--------|-------------|
| **E1 F5/F6** | **In progress** | Non-blocking `ControlSurface.run`; tests; playbook §3 |
| **E2 Push + CI** | Pending | `git push origin master`; GitHub Actions green on Phase C + E1 |
| **E3 Async kernel** | Optional | Async conductor/project; wrapper stays sync |
| **E4 Phase D** | Not started | Requirements + spec (sessions/telemetry, retrieval-first, **no porch naming**) |
| **E5 Operator API** | Blocked on E4 | FastAPI read-only endpoints mirroring MCP admission |
| **E6 Transport** | Optional | Streamable HTTP MCP alongside stdio — only if spec'd |

Verify locally:

```bash
pip install -e ".[dev]"
pytest -q
python scripts/smoke_agent_mcp.py
```

### Do not reopen without owner sign-off

| Item | Why blocked |
|------|-------------|
| Porch / `console/` / web UI | Owner cancelled 2026-08-25 |
| Ninth MCP tool / live tail | Freeze + R-QB-28 |
| Replace stdio MCP with HTTP for agents | R-FMC-1; breaks Phase C wiring |
| FastMCP Docket/Tasks | R-FMC-8 |

**Agent SSOT:** [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md)  
**Assessment:** [`hld/agent-mcp-usability-assessment.md`](agent-mcp-usability-assessment.md)
