# Trestle — TTY-class oneshot overlay contract

Scope: system overlay (optional advertised capability)  
Node: trestle  
Mode: complement  
Authority: this file is the consumer contract for TTY-class oneshot MUST-write, unreadiness, and filing. [Interfaces Architecture](hld-interface-architecture-trestle.md) remains the freeze for the three Kernel ports (Admit, Execute, Project), ControlSurface, nine MCP tools, Dual-error, Handle grammar, DefaultAgentSuccess, ViewRow freeze, and PluginSurface members. Freeze amendment `tty-overlay-kinds` types `PluginCatalogRow.capability_class` and `RunView.limits_exceeded` on that freeze; amendment `tty-class-overlay` owns the TTY-class MUST-write rules. Do not treat this overlay as a fourth port or a tenth tool.

Requirement sentences name outcomes. Helpers that provide a terminal are substitutable means. Contract identity MUST NOT name a particular helper vendor.

## Guiding Light

Upstream calls this document must serve. Plain language; no requirement IDs.

**Upstream:** [Requirements (Draft v0.6)](../trestle-requirements.md); [TTY overlay HLD](hld-tty-overlay-trestle.md); [Interfaces Architecture (freeze)](hld-interface-architecture-trestle.md)

### What must be true upstream

- An agent either completes terminal-sensitive oneshot work through the existing ten tools, or learns from the catalog and door that it must leave Trestle — never a silent hole.
- Three mouths stay separate: catalog publishes class, admission decides readiness, Context files managed bytes; pulse is not publication and filing is not unreadiness.
- Helpers stay inside the disposable child — not a port, not an MCP tool, not new agent grammar.

### This document's job

- Set the consumer contract for TTY-class MUST-write rules, one unreadiness code, and filing on top of the freeze envelopes.

---

## Summary

An MCP or CLI agent either completes TTY-class oneshot local work through the existing ten tools, or it can see from the public catalog and door that it must leave Trestle — never a silent hole, and never a new climate of tools, ports, or session ids. Publication lives on the catalog; pulse lives at admission; bytes live on Context. Freeze HLD amendment `tty-overlay-kinds` types `PluginCatalogRow.capability_class` and `RunView.limits_exceeded`. Amendment `tty-class-overlay` owns the TTY-class MUST-write rules for those members, plus one admission unreadiness code. Plugin authors keep the frozen Context members; they file console as ordinary evidence. Filing invariants keep helpers inside the child.

---

## Consumer and intent

TTY-class oneshot is optional advertised local work whose observable behavior depends on a TTY, captured as start–wait–retrieve evidence of the same class as any other run. Ordinary POSIX local work (milestones M1–M7) remains complete when this capability is unpublished.

| Role | Job this boundary serves | Explicit exclusions |
|------|--------------------------|---------------------|
| **MCP/CLI agent** (primary) | Discover whether this install claims TTY-oneshot work; complete that work as an ordinary run; or obtain a named exclusion and leave Trestle. | A tenth MCP tool; inlining plugin/TTY/helper JSON Schema into `tools/list` or `run`’s `inputSchema`; a vendor session identifier; Control-port stdin/resize/signal verbs; treating source-invalid listings as helper unreadiness; probing `run` as the only way to learn class absence. |
| **Plugin author** | Drain a helper in the disposable child; file console through existing Context sinks; return a small mapping that fits DefaultAgentSuccess. Stub Context is sufficient (stub MUST be able to record `tty_console` markers). Kernel, not the author, records gap counts on the porch. | Catalog columns; emitting `admission.*` codes; using `run_cmd` as the TTY-class path; a new Context TTY/PTY member; treating `log`/`progress` as the completeness porch. |
| **Operator** (adjacent) | Reap orphan helper sessions tagged to a `run_id` off the agent porch. | Agent-visible session grammar; a catalog health cell. |

**One job, three mouths — do not merge them.** Catalog publishes class. Admit decides whether this request becomes a run. Context files managed bytes. Pulse is not publication. Filing is not unreadiness.

**Three mutually exclusive agent stories**

1. **Absence** — the complete default-catalog publication advertises no TTY-oneshot class → leave Trestle. Not filename folklore. Not `plugin_not_found` alone. Not a single `CatalogView` page.
2. **Unreadiness** — the class is advertised; Admit refuses with exactly one unreadiness code and **no** `run_id`. Not source validation. Not a failed run.
3. **Failed run** — Admit minted a Handle; helper death or work failure → terminal `RunView`; pre-failure evidence retained.

Pipe-only agents never depend on the class **value**: advertisement is optional (`null` = no claim). The field is still always present. It is not a required verb.

---

## Contract

Medium is mixed: **class** for PluginSurface (public members unchanged **by this overlay**; freeze `closed-oqs-auto-runtime` already added `ctx.outputs` for automatic keepers, not TTY) and **wire** for envelopes the freeze already named (`CatalogView`, `RequestOutcome`, `RunView`). Freeze HLD amendment `tty-overlay-kinds` types `PluginCatalogRow.capability_class` and `RunView.limits_exceeded`; amendment `tty-class-overlay` owns the TTY-class MUST-write rules on those members and one `admission.*` code. It does not add MCP tools, Kernel ports, query ViewNames, fetch window kinds, or Context members. TTY-class gap counts bind to freeze-typed `RunView.limits_exceeded` (R-LIM-5), not a new porch kind.

### Shared freeze (cite, do not fork)

Envelope families, Dual-error, Handle prefixes, DefaultAgentSuccess, `wait_ms` only on `ControlSurface.run`, porch flattening of `AdmitResult` to `RequestOutcome | RunView`, and the nine-tool Semantic Contract Table live in the freeze HLD. Amendment `tty-overlay-kinds` types `PluginCatalogRow.capability_class` and `RunView.limits_exceeded`. Overlay rules below (`tty-class-overlay`) are the TTY-class MUST-write constraints on those typed members.

---

### PluginCatalogRow — class advertisement

Registry publication remains a Project view, not a fourth Kernel port. Catalog column bumps do not bump the nine query ViewRows. `list_plugins` MUST NOT return schemas.

`PluginCatalogRow` freeze fields `name`, `version`, `description`, and `valid` keep their freeze meanings. Amendment `tty-overlay-kinds` types `capability_class` on that class; amendment `tty-class-overlay` owns the TTY-class MUST-write for advertisement. `valid` remains source validation (previous version kept; `list_plugins(invalid=True)` lists failed validation). `valid` MUST NOT mean helper health, daemon pulse, or TTY unreadiness.

#### Typed member (`PluginCatalogRow.capability_class`)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `capability_class` | string enum \| null | yes — **always present**; omit is a contract miss (`null` allowed) | Vendor-neutral class claim for this plugin identity. Allowed non-null value in this overlay: `tty-oneshot`. `null` means this row makes **no class claim** (not an omitted field, not unpublished identity). Unknown non-null strings MUST be ignored by TTY-class consumers (not treated as `tty-oneshot`); adding a new enum member is a named overlay amendment. |

`description` remains a teaser. `describe_plugin` owns depth and MUST NOT be the class type. Agents MUST read class from `capability_class`, not from a conventional filename or teaser prose.

#### Class presence and absence (normative)

Let **default catalog** mean `list_plugins` without `invalid=True` (valid published identities).

Let **default-catalog publication** mean the walk of that listing: start at the first `CatalogView`; while `truncated` is true, continue with `next_cursor`; stop when `truncated` is false. Advertisement is over that whole walk, not over one page’s `items`.

```
tty_oneshot_advertised ⇔
  ∃ page in the default-catalog publication
    ∃ row in page.items
      where row.valid is true
        and row.capability_class == "tty-oneshot"
```

**Absence** (named exclusion, leave-Trestle): `tty_oneshot_advertised` is false after that walk. No class row anywhere in the default-catalog publication **is** the exclusion. A single page with no matching row is not absence while `truncated` is true. Silence — neither an advertised class nor this observable absence — cannot occur by construction. Agents who skip `list_plugins` still hit the door; they MUST still be able to read class absence from the completed `CatalogView` walk when they scan.

**Presence** is a publication claim, not a pulse. A stale class row while a helper is dead MUST still refuse at Admit without minting a Handle. The catalog MUST NOT grow a ready/health cell.

Several rows MAY advertise the same token. That is multiple plugins in one class, not multiple classes.

`registry_version` ticks on publication change, including when the last `tty-oneshot` row disappears. Porch `tools/list.registry_version` MUST continue to mirror `CatalogView.registry_version` on the same publication.

---

### RequestOutcome — one unreadiness code

`RequestOutcome` shape is the freeze: `code`, `message`, `retryable`, `origin`. Refusals have **no** `run_id`. New admission reasons add `admission.<code>` values; they do not add `RunView` fields beyond freeze-typed (`tty-overlay-kinds`) `RunView.limits_exceeded`.

This overlay adds **exactly one** unreadiness code. It does not open an admission junk drawer.

| `code` | Origin | `retryable` | When | MUST NOT appear when |
|--------|--------|-------------|------|----------------------|
| `admission.tty_not_ready` | `admission` | `false` | Default catalog advertises `tty-oneshot` on the requested plugin identity, but Kernel-private readiness at Admit says this request cannot perform TTY-class work. Tagged `Refused`. No ledger `created`. No `run_id`. | A Handle was admitted; the class is unpublished (that is absence / identity refusal, not this code); source validation failed (that remains `valid=false` / R-REG-5); the run already exists. |

`retryable=false` so agents do not hammer `run` as a health probe. Recovery is operator-side (bring the helper up) or leave-Trestle; a later `list_plugins` + `run` is a new request, not an automatic retry.

Orthogonal freeze codes keep their jobs:

| Code | Overlay relation |
|------|------------------|
| `admission.plugin_not_found` | Unpublished **identity** (wrong or deleted name). Orthogonal to class absence. Class-level leave-Trestle is “no `tty-oneshot` row,” not “I guessed the filename.” |
| Other `admission.*` | Unchanged (`invalid_args`, `queue_full`, `idempotency_key_conflict`, `service_draining`, artifact codes). |

Kernel-private readiness at Admit is not Execute and is not a CatalogView column. Admit MUST NOT execute, wait, or return summaries in order to learn unreadiness.

Post-admit helper death is **not** this code. After durable `created`, Dual-error maps helper death to a **terminal `RunView`** (`failed`, `crashed`, `worker_exit`, or the applicable existing class). Evidence written before failure is retained. Belated `admission.*` after a Handle exists is a contract crime.

---

### RunView — same grammar, completeness on the run

TTY-class oneshot, when admitted and ready, uses the same handle grammar, named views, and fetch windows as any other run. Completeness is framework-observable bytes plus counted suppressions, retrievable after restart by the same query and fetch class.

**Public fact an agent queries:** freeze-typed **`RunView.limits_exceeded`** (amendment `tty-overlay-kinds`; R-LIM-5; the same fact is in meta and ledger) on the terminal `RunView` returned by `run`, `await_runs`, and Project `status`. Amendment `tty-class-overlay` owns the TTY-class MUST-write. Not a new ViewRow, not a DefaultAgentSuccess field invented here, not a Context member, not a tenth tool. Kernel is the information expert. Author `log` / `progress` MUST NOT be the completeness porch.

`limits_exceeded` is the gap porch for **any** class. Class advertisement is CatalogView only (`PluginCatalogRow.capability_class`). Do not treat `None` as “not TTY-class.” Kernel MAY keep a private admit snapshot of class for MUST-write; that snapshot is not agent grammar. After a `tty-oneshot` row is unpublished, an agent holding an old Handle still reads completeness from the list on that run’s terminal frame — not from current catalog, not from None-as-class.

On every TTY-class terminal frame Kernel MUST write `RunView.limits_exceeded` as a **list**. Kernel **merges** wrapper pipe-capture `stdout`/`stderr` with overlay `tty_console` into that one list.

| `limits_exceeded` | Meaning (any class) | TTY-class terminal |
|-------------------|---------------------|--------------------|
| `None` / omitted | Not checked | Contract miss — MUST be a list |
| Empty list | Checked zero gaps | Legal **only after** the `tty_console` check ran — not after wrapper `stdout`/`stderr` alone |
| Non-empty list | Counted gaps (R-LIM-3 markers: `stream`, `limit`, bytes recorded, **bytes suppressed**) | Overlay stream token `tty_console` for helper-transport holes, merged with wrapper streams. Any TTY-class gap on that merged list ⇒ ledger `completeness`=`partial`. Agent porch remains this list |

A TTY-class terminal `RunView` that omits `limits_exceeded` (or sets `None`) is a contract miss: gaps were not checked. Empty list is checked-zero **after** `tty_console` was checked, not silence and not “pipes looked empty.” A projection that looks finished with uncounted loss fails this overlay. Do not satisfy R-TTY-3 with a second unnamed channel or a porch `completeness` cell.

TTY-class terminus is `fetch` of attached `art_…`, not `query(run_tail)` and not wrapper pipe tails. A helper buffer is transport. Interactive stdin, resize, signal-as-agent-verbs, and live tail of a still-running run are out of bounds. Oneshot ends at `evidence_finalized` plus those Kernel-written counts, retrieved after the fact. Advertisement is oneshot **class**, not a control surface.

---

### PluginSurface (`trestle.Context`)

Author-facing protocol for one disposable child / one run. Plugin authors can be correct with a stub and one file. **Public members are unchanged** from the freeze; this overlay adds filing invariants only.

#### Summary

| Field | Value |
|-------|-------|
| Kind | protocol |
| Owner | child runtime |
| Depends on | work namespace, event sink, cancel flag |
| Lifetime / ownership | one child / one run; Kernel owns evidence after `attach` |
| Concurrency | child may use asyncio; wrapper must not |

#### Public surface

| Member | Signature | Description |
|--------|-----------|-------------|
| artifact | `artifact(...) -> Path` | Staging path under `work/artifact-staging/<id>.partial`. Traversal rejected. Never `evidence/`. TTY console bytes stage here before `attach`. |
| attach | `attach(path: Path, ...) -> Handle` | Promotes into evidence; returns opaque `art_…` handle. Console that agents fetch is this evidence, not a helper session id. |
| copy_artifact | `copy_artifact(handle: Handle) -> Path` | Writable copy in `ctx.tmp`. Never an evidence path. |
| retain | `retain(handle: Handle) -> None` | Reachability pin from this run. |
| run_cmd | `run_cmd(...) -> CmdResult` | Process-group child; pipe-capture tails capped 2 KB for plugin logic. **Not** the TTY-class path. Ordinary commands that do not need a TTY stay here. |
| log / progress | event sinks | Write `events.ndjson`. Not the TTY-class gap porch. |
| tmp | `Path` | Inside `work/`. |
| cancelled | `bool` | Becomes true on cancel flag. Helper drain MUST honor it; in-child stop does not require a Control port. |
| deadline | `datetime` | Absolute. |

No new TTY, PTY, session, resize, or write member. Missing sinks MUST NOT be solved by growing scheduler, query, pin, or Control APIs onto Context.

#### Invariants

- No writable evidence path is passed to plugin code.
- DIP: plugin depends on this protocol, not Kernel classes, not FastMCP, not a named helper SDK on the porch.
- Helper session identifiers, daemon URLs, and drain-loop cursors die in the child. They MUST NOT appear on `RunView`, `RequestOutcome`, `CatalogView`, or returned handles.
- A helper buffer is transport, not terminus. Authors still file console only through `artifact` / `attach` (R-CTX-4: all Context output is subject to §9). Agents retrieve TTY console by `fetch` of attached `art_…`, not `query(run_tail)` and not wrapper `console/*`. TTY drain (plugin or helper client) is Information Expert for **detecting wrap**. It MUST write R-LIM-3 markers (`stream` = `tty_console`) into **child-owned `work/` observation files Execute already watches**, **before** `attach` completes. Drain MUST NOT concatenate across a hole. Context **implementation** is Information Expert for **promoting** those files (PluginSurface **runtime dependency** — not a new public member, not Kernel types in plugin code). Stub Context MUST be able to record `tty_console` markers when a test drain reports wrap. Author `log` / `progress` do not substitute. Incomplete classification follows from those markers; there is no “classifiable as incomplete” substitute.
- TTY-class plugins that drain via unmanaged `open()` around Context fail this overlay (R-SCOPE-2 / R-TTY-3). Unmanaged I/O that never entered Context remains unmanaged; `describe_plugin` MUST NOT claim provenance over it.

#### Error behavior

Throw early on traversal and missing artifact. Hijacked logging is recorded, not necessarily a run failure. Helper unreachable **after** admit is a terminal run, not an `admission.*` emission (authors cannot emit admission codes).

#### Substitutability

Any helper that can drain oneshot console into `artifact`/`attach` and write `tty_console` R-LIM-3 into child-owned `work/` files Execute already watches (Context implementation promotes; stub MUST be able to record) so Kernel can merge streams and write `limits_exceeded` is substitutable. Callers (agents) do not change tools, handles, or views. Authors swap the child collaborator and, if identity changes, the plugin row.

#### Extension points

New TTY-class plugins: new catalog row, same `capability_class` token `tty-oneshot`, same Context members. No tenth tool. No new Context member.

---

### MCP tools (existing table — overlay rows only)

Nine tools remain. Core count MUST NOT grow with plugins or with this overlay. Tool **definitions** on `tools/list` MUST stay the freeze’s thin Kernel schemas (G1): this overlay adds a catalog column, not schema bytes on the porch. Tools not listed here are unchanged from the freeze Semantic Contract Table.

| Tool | Overlay postcondition | Forbidden confusions |
|------|----------------------|----------------------|
| `list_plugins` | `CatalogView` items **always include** `capability_class` (`null` = no claim; omit is a miss). Class-level leave-Trestle is readable as no valid `tty-oneshot` row on the completed default-catalog walk (`next_cursor` until `truncated` is false). `valid` still means R-REG-5 only. No schemas. Not `BoundedView`. | Not a health API. Not `describe_plugin` as class type. Not treating `valid=false` as helper down. Not treating one page as the publication. Not treating a missing field as no claim. |
| `run` | If the requested identity is a published `tty-oneshot` row but not ready: `isError: true` + `RequestOutcome` `admission.tty_not_ready`, **no** `run_id`. If unpublished identity: `admission.plugin_not_found`. If admitted: `RunView` (DefaultAgentSuccess); helper death after admit is terminal `RunView`, not unreadiness. | Not Execute-as-probe. Not Control stdin/resize. Not `wait_ms` as a readiness flag. |
| `query` | Named views unchanged (no new ViewName). `query(run_tail)` is wrapper `console/*`. | Not the TTY-class terminus. Empty `run_tail` is not completed TTY work. |
| `fetch` | TTY-class console that satisfies R-REACH-1 is attached `art_…` with existing fetch windows. | Not wrapper `console/*`. Not a new window kind. |

`describe_plugin` MAY mention that work is TTY-class oneshot as side-effect honesty. That prose is not the class advertisement and MUST NOT restate plugin types onto `tools/list` (R-MCP-3).

---

## Responsibility and dependency story

| Concern | Information expert | Control |
|---------|-------------------|---------|
| Whether this install claims TTY-oneshot as a class | Project / registry publication (`CatalogView`) | Authors publish a plugin row; Kernel increments `registry_version` |
| Whether **this request** may become a run | Admit (Door) | Kernel-private readiness; never a catalog cell |
| Console bytes and gap counts | Kernel (`RunView.limits_exceeded` via LedgerCommand `limit_exceeded`, R-LIM-5; merges wrapper `stdout`/`stderr` + `tty_console`) | Drain is expert for wrap (`work/` R-LIM-3 before `attach`); Context implementation promotes (runtime dependency; stub MUST be able to record wrap); Project only projects that Kernel fact |
| Operator reaping of helper sessions | OperatorContract (off porch) | Tagged to `run_id`; not agent grammar |

**Dependency direction.** ControlSurface composes Admit + Project only. Agents depend on `CatalogView` + `RequestOutcome` + `RunView`. Plugin authors depend on `Context`. Neither depends on a helper’s concrete protocol. Execute remains Conductor-only. CLI and MCP honor the same ControlSurface verbs; OperatorContract may reap off-porch.

Abstractions that cross the seam: freeze-typed `PluginCatalogRow.capability_class` and `RunView.limits_exceeded` (amendment `tty-overlay-kinds`; `tty-class-overlay` owns TTY-class MUST-write), plus `admission.tty_not_ready`. Concrete helper process, HTTP, PTY implementation, ring buffers, and session ids do not cross.

---

## Variation story

| Predicted change | Lands where | Stays stable for consumers |
|------------------|-------------|----------------------------|
| Swap helper (same or new drain) | Child collaborator; maybe same class row | Class token, ten tools, ViewNames, Handle prefixes, Context members |
| Delete helper / plugin file | Default catalog loses the `tty-oneshot` row; `registry_version` ticks | Absence is observable; M1–M7 still complete; no new agent grammar |
| Second TTY plugin | Second row, same `tty-oneshot` token, same sinks | No new Context member; unreadiness remains **the same one** code, not a code per helper |
| Helper dies between list and run | `admission.tty_not_ready`, no `run_id` | Catalog may still show the class row (publication lag); Door is truth for this request |
| Helper dies after admit | Terminal `RunView` | Not a fake refusal |
| Ordinary command, no TTY | `ctx.run_cmd` | TTY plugin unpublished or unused; TTY-class is not the default route |
| Interactive stdin / resize / live tail | Out of bounds until a named requirements amendment | Oneshot MUST NOT smuggle Control onto Project or Context |

**Stable seam:** ten tools; nine query ViewRows; `valid` as source validation; Context protocol; DefaultAgentSuccess; named views; fetch windows.

---

## Boundary choices

| Exported | Why a named client needs it | Hidden |
|----------|-----------------------------|--------|
| `capability_class` = `tty-oneshot` \| null (always present) | Agent can exclude the class without guessing filenames (reach vs leave-Trestle). `null` is no claim, not an omitted field. | Helper topology, schemas, vendor names, ready bit |
| `admission.tty_not_ready` | Agent can tell advertised-but-not-ready from invalid source and from a failed run, without a `run_id`. | How Admit probes readiness; Execute internals |
| Existing Context sinks | Author files same-class evidence; wrap observation is a PluginSurface runtime dependency (no new member). Stub MUST be able to record `tty_console` when a test drain reports wrap. | Drain poll, encoding, ring size, daemon URL; Kernel types in plugin code |
| Freeze-typed `RunView.limits_exceeded` (R-LIM-5) | Agent queries not-checked (`None`/omit) vs checked-zero (empty list **after** `tty_console` check) vs counted gaps (non-empty) on the terminal `RunView` they hold. TTY-class terminal MUST be a list. Kernel merges wrapper `stdout`/`stderr` + `tty_console` (R-STORE-6; one `limit_exceeded`). Any TTY-class gap on that merged list ⇒ ledger `completeness`=`partial`; porch stays this list. Overlay MUST-write (`tty-class-overlay`). | Helper `droppedBytes`; author MAY-logs as the completeness porch; a second unnamed channel; reading class from `None`; treating empty `run_tail` as TTY done; coercing pipe-only `None` to `[]` |

**Deliberately not exported:** catalog health cell; extra `admission.*` unreadiness family; Context TTY member; tenth tool; vendor session id; ControlSurface stdin/resize/signal; `wait_ms` on AdmitRequest; `valid=false` as daemon down.

**E4 bound (minimum kind).** If a proposed field is not freeze-typed (`tty-overlay-kinds`) `capability_class` advertisement, the one unreadiness code, existing-sink filing, or the R-LIM-5 `RunView.limits_exceeded` MUST-write (`tty-class-overlay`), it does not ship. Operator reap is not a consumer-contract kind.

---

## Consumer Scenario Proof Pack

Each claim traces to the contract above. Scenarios prove semantics, not helper internals.

### Intended use

**Consumer:** MCP/CLI agent; plugin author.

**Trajectory.** Agent walks `list_plugins` (`next_cursor` until `truncated` is false) and reads a valid row with `capability_class == "tty-oneshot"` (the field is present on every row; other rows may be `null`). Agent calls `run` with that plugin name, version, args, and optional idempotency key — the same nine-tool family as any other work. Admit is ready → Handle minted → oneshot completes → terminal `RunView` after durable `evidence_finalized`, DefaultAgentSuccess, same named views and fetch windows. Agent retrieves TTY console by `fetch` of attached `art_…`, not `query(run_tail)` and not wrapper pipes. Agent reads **`limits_exceeded`** on that frame (R-LIM-5): a list is required; empty list means Kernel checked zero gaps **after** the `tty_console` check ran; non-empty means counted gaps (`tty_console` merged with wrapper `stdout`/`stderr`) and ledger `completeness` **partial**. Author staged console with `artifact`, promoted with `attach`, honored `cancelled`/`deadline`, returned a small mapping. Drain wrote any `tty_console` R-LIM-3 markers into child-owned `work/` files Execute already watches; Context implementation promoted them (stub MUST be able to record). No new tool. No vendor session id on the wire. Helper buffer never presented as terminus.

**Success.** Observer can point to a published TTY-class capability that completed through the existing ten tools, or (below) to typed exclusion.

### Likely misuse or failure

| Misuse | Contract response | Why it is bounded |
|--------|-------------------|-------------------|
| Treat `valid=false` / `list_plugins(invalid=True)` as “helper down” | **Fails.** `valid` is source validation only. Unreadiness is `admission.tty_not_ready` with no `run_id`. Invalid listing does not mint a run and does not rename R-REG-5. | Distinct mouths: catalog validation vs Door pulse |
| Optional-with-silence (install with neither advertisement nor visible absence) | **Fails by construction.** No valid `tty-oneshot` row on the completed default-catalog walk **is** the named exclusion. Agents MUST read class absence from that walk, not “I did not see a familiar filename” and not one truncated page. `plugin_not_found` remains identity-only. | R-REACH-1 / class billboard |
| `fetch` with a filesystem path | Freeze: `projection.invalid_handle`, `isError: true`, not file contents. TTY console is retrieved by `fetch` of attached `art_…`. | R-FET-8; no path porch |
| Treat empty `query(run_tail)` or wrapper pipe tails as TTY-class done | **Fails.** Terminus is attached `art_…` fetch. Empty `run_tail` does not mean TTY done. | R-TTY-2 / R-CTX-4 |
| Omit `capability_class` on a catalog row | **Fails.** Field always present; `null` = no claim. | R-TTY-5 |
| Inline plugin/TTY/helper JSON Schema into `tools/list` or `run` `inputSchema` | **Fails.** Nine thin Kernel definitions; class is CatalogView; `describe_plugin` is pull-for-one. | G1 / R-MCP-1–3 |
| Control-port smuggling (resize/write/signal as catalog flags, extra tools, or Context members) | **Rejected.** Advertisement is oneshot class. Context members unchanged. Tool count frozen. Oneshot does not satisfy interactive control or live tail. | R-TTY-9; E2 Control deferred in freeze |
| `run` when class advertised but helper dead (pre-admit) | `admission.tty_not_ready`, no `run_id`, no ledger row that pretends work ran. Catalog may still show the class row. | Pulse at Door, not catalog health |
| Map post-admit helper crash to `admission.*` | **Fails.** Dual-error terminal `RunView`; compact error; pre-failure evidence retained. | Request vs run |
| Log a vendor session id on `RunView` / return value | **Fails.** Handles are `r_…` / `art_…` only. | R-TTY-7 |
| Force wrap / dropped helper bytes with no gap record | **Fails.** Drain MUST write R-LIM-3 markers (`stream` = `tty_console`) into child-owned `work/` files Execute already watches; Context implementation promotes (stub MUST be able to record). Kernel merges wrapper `stdout`/`stderr` + `tty_console`. Terminal `RunView.limits_exceeded` MUST be a non-empty list; ledger `completeness` is **partial**; agent porch stays `limits_exceeded` (no new completeness cell). Empty list is legal on a TTY-class terminal only after the `tty_console` check ran. `None`/omit is a miss. Author `log` / `progress` do not substitute. Silent concatenation fails. | R-TTY-3 / R-LIM-3 / R-STORE-14 |
| Drain TTY via unmanaged `open()` around Context | **Fails** the overlay (R-SCOPE-2 / R-TTY-3). | Wrap observation is a PluginSurface runtime dependency |
| Use `ctx.run_cmd` as the TTY-class path | **Fails** the overlay’s author contract. Ordinary non-TTY commands stay on pipe capture; TTY-class MUST NOT become their default route. | R-TTY-8 / R-CTX-2 |

### Extension

A second helper plugin advertises the **same** freeze-typed `capability_class` token `tty-oneshot`. Agents do not learn a vendor id. The typed catalog member is unchanged; ViewRow table unchanged; MCP tool count unchanged. Unreadiness remains `admission.tty_not_ready` — not a new code per helper. Context members unchanged. Callers keep `list_plugins` + `run` + fetch/query.

### Substitution

Delete or replace the helper plugin file. Next completed default-catalog walk has **no** valid `tty-oneshot` row (`registry_version` ticks). That is absence, not silence, not a failed run, not one page of a truncated listing. Nine tools, handle prefixes, ViewNames, and Context members unchanged. Requirement text remains intelligible with no helper vendor named. Ordinary M1–M7 POSIX work still completes.

Replace the helper behind the same plugin identity: agents keep the same class token and run grammar; only the child collaborator changes.

### Internal change

A plausible helper uses a lossy in-process buffer (wrap, truncated reads, encoding seams) as **transport** between a PTY and `ctx.artifact`. Drain loop, daemon URL, ring size, and encoding stay in the child. Drain writes `tty_console` R-LIM-3 into child-owned `work/` files Execute already watches; Context implementation promotes (stub MUST be able to record). Agent-visible contract stays: class advertisement (`capability_class` always present) + Door unreadiness or ordinary `RunView` / `fetch` of attached `art_…` with Kernel-written `limits_exceeded` (list on TTY-class terminals; empty only after `tty_console` check; Kernel merges wrapper `stdout`/`stderr` + `tty_console`; non-empty ⇒ ledger `completeness` partial). Treating that buffer, `query(run_tail)`, or wrapper pipes as the terminus — concatenating across holes, or surfacing helper session ids — violates encapsulation. Swap of drain algorithm or collaborator MUST NOT bump query ViewRows, grow MCP tools, or add Context members.

---

## CAFE / helper-ground checklist

- **Coherence.** Catalog’s reason-to-change remains registry publication (now including one optional class claim), not daemon pulse. Admit’s reason-to-change remains request fate. Context’s reason-to-change remains author-filed managed evidence. Mixing pulse into `valid` would give Catalog two reasons-to-change.
- **Adaptability.** Predicted variation (which helper, whether any helper, second TTY plugin) is added as rows and child collaborators. Callers are not edited. OCP at PluginSurface and at the single new admission code.
- **Freedom.** Agents are not trapped into probing `run` to learn class absence, not trapped into treating one `CatalogView` page as the publication, not trapped into a vendor session grammar, not trapped into `invalid=True` as capability health, not trapped into treating omitted `capability_class` as no claim, not trapped into `query(run_tail)` as TTY terminus. Authors depend on Context, not a named helper. Pipe-only agents ignore the class **value** (`null`).
- **Encapsulation.** Billboard reveals class, not how a terminal is provided. Door reveals unreadiness as a semantic refusal, not a boolean health flag. Session ids never leave the child. Helper buffers never become porch types. Gap counts leave the child only as Kernel `limits_exceeded` (R-LIM-3 `tty_console` in `work/` files Execute already watches; Context implementation promotes), never as author events or a new Context member. Ledger `completeness` partial is Kernel ledger, not a new agent porch.
- **SRP / ISP / DIP / tightness.** Three mouths, three experts. `capability_class` always present; `null` = no claim. Consumers depend on envelope abstractions. Verification of the overlay is: class token present or `null` on every default-catalog row of the completed walk; unreadiness is that one code with no `run_id`; TTY terminus is attached `art_…` fetch; TTY gaps are `RunView.limits_exceeded` as a list after `tty_console` check (`None` = not checked; Kernel merges wrapper streams + `tty_console`; non-empty ⇒ ledger `completeness` partial); Context public member set unchanged; ten tools unchanged. Cost tracks those kinds, not helper size.

---

## Grounding (traceability)

G9, R-REACH-1, R-TTY-1 through R-TTY-9, R-INV-1, R-MCP-1, R-MCP-2, R-REG-5, R-CTX-2, R-FET-8, R-LIM-3, R-LIM-5, R-STORE-6, R-STORE-14, R-QB-27. Freeze: CatalogView / PluginCatalogRow, ten tools, Dual-error after admit, PluginSurface, Control port deferred. Amendment `tty-overlay-kinds` types `PluginCatalogRow.capability_class` and `RunView.limits_exceeded`; amendment `tty-class-overlay` owns TTY-class MUST-write and **one** `admission.tty_not_ready`. Requirements §25 question 10 (unreadiness channel) is closed in v0.6 as `admission.tty_not_ready`; class channel is freeze-typed `PluginCatalogRow.capability_class` always present (`null` = no claim) over the completed default-catalog walk; TTY terminus is attached `art_…` fetch (not `query(run_tail)`); gap/completeness channel is freeze-typed `RunView.limits_exceeded` (`None`/omit = not checked; empty list = checked zero **after** `tty_console` check; Kernel merges wrapper `stdout`/`stderr` + `tty_console`; non-empty TTY gaps ⇒ ledger `completeness` partial; agent porch stays `limits_exceeded`).

Helpers named in adoption memos are means. This overlay’s MUST identity is TTY-oneshot class advertisement, Door unreadiness, Context filing, and Kernel `limits_exceeded` — substitutable behind those seams.

---

## Source notes

Exploration commit: class billboard + door unreadiness + context airlock (`_tmp/exploration-interface_design-tty-cycle-1/`). Freeze HLD amendment `tty-overlay-kinds` types `PluginCatalogRow.capability_class` and `RunView.limits_exceeded`. Amendment `tty-class-overlay` owns TTY-class MUST-write on those members, plus code `admission.tty_not_ready`; Context sinks unchanged. The TTY-class gap porch is Kernel-written `RunView.limits_exceeded` (R-LIM-5), not a Context member and not author events.

---

## Changelog

- **2026-08-20** — Thin MCP: overlay MUST NOT inline plugin/TTY/helper schemas into `tools/list` or `run` `inputSchema`; class stays `capability_class` on CatalogView; `describe_plugin` remains pull-for-one.
- **2026-08-20** — Drain is wrap expert: `tty_console` R-LIM-3 into child `work/` files Execute already watches **before** `attach` (Context implementation promotes; PluginSurface runtime dependency; stub MUST record wrap; unmanaged `open()` fails overlay); Kernel merges both R-STORE-6 owners (empty TTY list only after that check; never coerce pipe-only `None`); TTY terminus is `fetch(art_…)`, not `query(run_tail)`; `capability_class` always present (`null` = no claim); any TTY-class gap on the merged list ⇒ ledger `completeness`=`partial`, agent porch still `limits_exceeded`.
- **2026-08-20** — Named child-runtime R-LIM-3 `tty_console` injection onto existing LedgerCommand `limit_exceeded` (R-CTX-4 via `artifact`/`attach`); `limits_exceeded` None/omit = not-checked (not class); TTY-class terminal MUST be a list; completeness stays on the held Handle after unpublish.
