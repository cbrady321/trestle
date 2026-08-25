# Agent Handoff — Trestle Console port worthiness verification

> **OBSOLETE (2026-08-25).** Porch/web track cancelled. See [`plan-daytona-clean-room-scope.md`](plan-daytona-clean-room-scope.md) §0 for active scope.

## 1. Identity & Framing

You are continuing **Trestle Console** (clean-room Phase C): a local operator layer above the shipped v0.1 kernel — **Porch** (`console/porch/`) HTTP adapter + **Console** web UI (`console/web/`) — that exercises Trestle’s retrieval grammar for humans without vendoring another product’s source.

Your role is **goals verifier / gatekeeper**, not implementer yet. **Do not write `console/` code** until this hop produces a signed-off worthiness artifact. If a component fails the goals test, mark it **Drop** or **Defer** and update the plan/spec — do not build it “because it’s in the kickoff table.”

> You are verifying that every package and surface we plan to clean-room build is **worth Trestle’s goals** (especially G1, G4, G7). Implementation waits on your verdict.

## 1a. Goals (cascade)

Cite `helper-goal-cascade`. Refresh from named SSOTs, not this chat.

**Parent (SSOT):** `trestle-requirements.md` — `## 1. Goals`

- Minimize agent context consumed per unit of work done (G1).
- Retain all framework-observable evidence, surface almost none (G4).
- Bounded, backend-independent query interface over run history — nine named views (G7).
- Extend capability by writing one Python file — no restart or MCP plumbing for new plugins (G3).

**Parent (SSOT):** `hld/hld-interface-architecture-trestle.md` — `## Goals & Non-Goals`

- Nine MCP tools only; request refusals have no `run_id`; only Project emits toward MCP.
- CLI may be richer than MCP; same verb must not have different admission rules across transports.

**Parent (SSOT):** `hld/plan-daytona-clean-room-scope.md` — `## Strategic correction — retrieval, not log viewing`

- Console proves **bounded retrieval** for humans (focused question → small answer + continuation handle), not a log scroller or artifact browser MVP.
- Kernel may already satisfy the core agent job; Console is optional proving ground, not the main lever.

**This-node:** Produce **`hld/console-port-worthiness-verification.md`** — for each proposed clean-room **package, route, endpoint, and stack choice**, a verdict **Keep / Should / Defer / Drop** with explicit mapping to G1, G3, G4, G7 and freeze constraints. **Block Phase C implement** until Must-Keep set is minimal, defensible, and retrieval-first. Update plan §0 or spec only where your verdict changes scope.

**Larger picture:** `hld/plan-daytona-clean-room-scope.md` — `## 0. Kickoff` assumes Phase C is ready; **your hop re-validates that assumption** before any code lands.

**Do not:** refresh parent bullets from a prior handoff; treat chat paraphrase as SSOT; open vendor AGPL source to “compare features.”

## 2. Current State — what's true right now

**What exists**

- **Trestle kernel** (`trestle/`), v0.1 shipped: nine MCP tools, spawn-per-run, ledger on disk, `query` + `fetch` + named views, DefaultAgentSuccess (G1/G4). `pytest` green locally (102 tests). `trestle serve` = stdio MCP only.
- **Program complete:** `programs/trestle-v01/.program-ledger.yaml` — `initiative_complete: true`. Do not reopen kernel architecture.
- **Planning artifacts (no Console code):**
  - [`hld/plan-daytona-clean-room-scope.md`](plan-daytona-clean-room-scope.md) — kickoff §0, clean-room process, job IDs §8, drops (compute plane, toolbox, live tail).
  - [`hld/spec-console-lens-mvp.md`](spec-console-lens-mvp.md) — Porch HTTP §3.3, Python §3.4, UI §4, naming wall, Chinese wall.
  - [`hld/handoff-console-lens-implementer.md`](handoff-console-lens-implementer.md) — implementer packet ( **blocked until your verification** ).
- **`console/` tree:** does not exist yet.

**What's decided (and why)**

- **Clean-room, not extract:** Study public docs only; implement from our spec; Chinese wall + naming wall (`/porch/v1`, `read_*`, `issued`/`body`, `console/porch/`). AGPL vendor trees forbidden — corporate embedding + wrong product shape.
- **Retrieval-first, not dashboard:** Log pane (L1) and artifact browser (A1–A2) **dropped** as MVP centerpieces; Q1 bounded answer card is Must-if-Console-ships.
- **Porch is MCP client only** — not Kernel HTTP, not Execute, not admit/`run` from UI.
- **Sandboxing out of scope** — user explicitly does not care about remote computers; runners/toolbox/snapshots are Drop.

**What's been tried and killed**

- Vendoring/carving AGPL dashboard monorepo — license + wrong shape.
- Console as log viewer — contradicts G1/G4; strategic correction killed it.
- Nightona as license escape — same AGPL, no advantage.
- Tenth MCP “ask” tool — violates freeze; retrieval = compose `query` + `fetch`.

**Where the numbers stand**

| Gate | Status |
|------|--------|
| Kernel pytest | 102/102 (local) |
| Console code | **0** |
| Worthiness verification artifact | **Not written — your deliverable** |
| Phase C implement | **Gated on verification** |

## 3. Open Threads

- **Is Console itself worth building?** Kernel + CLI + MCP may be enough for G1; verify whether Porch+web adds measurable value or is vanity UI.
- **Per-component Must vs Should:** Kickoff lists B1, K1, R1, R3, R4, Q1, X1 as Must — verify each independently; demote any that don’t earn its bytes against G1/G7.
- **Stack packages** (FastAPI/Starlette, Vite, shadcn, TanStack Query, OpenAPI codegen) — verify each is necessary; no dep because “dashboards use React.”
- **Should items** (registry, host_wiring, retrieval chain Q2, `last_error` preset) — verify defer vs include in MVP spec.
- Trestle hygiene (CI `master`, tag, README) — **out of this hop**.

**Blocked:** Implementation handoff until `hld/console-port-worthiness-verification.md` exists with explicit Keep/Drop and owner sign-off path.

## 4. Constraints & Non-Negotiables

**Verification method**

- Score each row in the inventory below against **G1, G3, G4, G7** and freeze (nine tools, admission identity, no live tail R-QB-28).
- Use **evidence from Trestle SSOT only** — requirements, freeze HLD, plan §8, spec §3–§4. No vendor source.
- **Acid test (G1):** Does this component reduce agent turns/tokens for the same diagnosis, or only duplicate what `query`/`fetch` already give agents?
- **Acid test (G7):** Is it expressed as named views + bounded fetch, not paths/SQL/stream dumps?
- If **Drop**, say what replaces it (usually: agent uses MCP directly, or operator uses CLI).

**Hard limits**

- Do not implement `console/` in this hop (read-only verification).
- Do not change Kernel freeze without a separate amendment unit.
- Do not add packages that thicken MCP or grow `tools/list`.

**Worthiness inventory (verify every row)**

| Package / surface | Proposed role | Kickoff verdict | You must answer |
|-------------------|---------------|-----------------|-----------------|
| `console/porch/` (Python) | MCP stdio client → HTTP for browser | Must (B1) | Required for any web UI, or can UI be skipped entirely? |
| `PorchGateway` + `kernel_client.py` | Wire HTTP → nine tools | Must | Same as above |
| `GET /porch/v1/binding` | Health / connect | Must (K1) | Needed vs CLI-only ops? |
| `GET /porch/v1/host_wiring` | MCP host JSON snippet | Should (K2) | G1 wiring value vs README-only? |
| `POST …/ledger/{view}/rows` | Human `query` | Must (R1, R3) | G7 human porch vs agent-only? |
| `POST …/evidence/chunk` | Human `fetch` | Must (Q1) | Core retrieval vs redundant with MCP? |
| `/ledger/{handle}/ask` UI | Bounded answer card | Must (Q1) | Proves retrieval grammar — worth build? |
| `/ledger` list UI | Pick a run | Must (R1) | Navigation only — minimal chrome OK? |
| `/bind` UI | Connection | Must (K1) | |
| `/registry` + registry endpoints | Callable catalog (G3) | Should (P1–P2) | Defer to v0.2? |
| `join_waits`, `request_stop`, retention | Operator power tools | Could | Confirm Drop for MVP |
| Log scroller / artifact browser | — | Drop (L1, A1–A2) | Confirm stays Drop |
| **Vite + React + TS** | UI shell | Assumed | Justified vs lighter static UI? |
| **shadcn/ui** (fresh init) | Components | Assumed | MIT-only; necessary? |
| **TanStack Query** | Porch fetch cache | Assumed | Necessary vs fetch wrapper? |
| **OpenAPI → TS client** | Type-safe Porch client | Assumed | Necessary vs thin hand-written types? |

**Output schema** (`hld/console-port-worthiness-verification.md`):

```markdown
## Guiding light
(bound parent goals — path + heading)

## Verdict summary
- **Proceed to implement:** <minimal Keep list>
- **Defer:** <Should → later>
- **Drop:** <confirmed dead>
- **Console overall:** Build / Defer entire initiative / Build subset (e.g. Porch-only, no web)

## Component table
| Component | Verdict | G1 | G3 | G4 | G7 | Freeze | Rationale (2–3 sentences) |

## Recommended plan/spec edits
(bullet list of file + section to change if verdict ≠ kickoff)

## Implementer unblocks when
(checklist)
```

## 5. Immediate Next Steps

1. **Read SSOTs** — `trestle-requirements.md` §1 Goals; freeze HLD Goals & MCP table; plan §0, §8, Strategic correction; spec §3–§4. **Done** = you can cite goal IDs per component without guessing.

2. **Draft worthiness artifact** — `hld/console-port-worthiness-verification.md` using schema above. **Done** = every inventory row has a verdict + rationale. **Waiting on:** nothing.

3. **Stress-test “Console overall”** — write one paragraph: if we **never** built web UI, would Trestle still meet G1 for operators (CLI/MCP only)? If yes, what is the **minimum** Console that still earns its maintenance? **Done** = explicit Build / Defer-whole / Build-subset recommendation.

4. **Patch plan if needed** — if any Must in §0.5 fails verification, edit `plan-daytona-clean-room-scope.md` §0.5–§0.6 and note in verification doc. **Do not** implement code in this hop.

5. **Hand off implementer** — only after step 2–4: successor uses [`handoff-console-lens-implementer.md`](handoff-console-lens-implementer.md) **plus** your verification doc as gate. Implementer Must list = your **Proceed to implement** section only.

## 6. Questions Worth Sitting With

- If Console only helps humans scroll less but agents still do the real retrieval, did we build the right thing?
- Is **Porch without web** (curl-friendly HTTP) enough to prove the porch pattern?
- Does any **Should** item exist only because a sandbox dashboard had it — not because Trestle goals need it?
- What would we **fail to notice** if we dropped Console entirely and invested in better `grep` / view docs for agents instead?

## 7. Reference Materials

| Artifact | Use |
|----------|-----|
| [`trestle-requirements.md`](../trestle-requirements.md) — `## 1. Goals` | Scoring rubric (G1, G3, G4, G7) |
| [`hld/hld-interface-architecture-trestle.md`](hld-interface-architecture-trestle.md) — `## Goals & Non-Goals`, MCP table, ViewRow freeze | Freeze + retrieval grammar |
| [`hld/plan-daytona-clean-room-scope.md`](plan-daytona-clean-room-scope.md) — §0, §8, Strategic correction | Proposed scope + drops |
| [`hld/spec-console-lens-mvp.md`](spec-console-lens-mvp.md) — §3–§4 | Proposed packages/routes to verify |
| [`hld/handoff-console-lens-implementer.md`](handoff-console-lens-implementer.md) | **Next hop after you** — blocked until verification |

**Jargon:** Trestle on-disk `console/*` = evidence streams. **Console** = web product. **Porch** = `console/porch/` HTTP adapter.

**Do not read for this hop:** any Daytona/Nightona source tree or dashboard code.
