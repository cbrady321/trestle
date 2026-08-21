# trestle-v01 — Integration Plan

Light planning at program scope (no execution). Proves cross-unit contracts before delivery.

## Wave W1 — Kernel round-trip (after M1)

**Verifier:** Trivial plugin → MCP `run` → terminal RunView with status frame; refusal path returns RequestOutcome without run_id.

**Bulkheads:** I01, I02, I04

## Wave W2 — Bounding E2E (after M2)

**Verifier:** Hostile return → index+summary bounded; fetch by handle; `outputs/` auto-promote.

**Bulkheads:** I02, I03

## Wave W3 — Agent workflow (after M4)

**Verifier:** US-01, US-04, US-05 — run + await_runs + query + fetch without polling burn.

**Bulkheads:** I03, I04

## Wave W4 — Program closure (after M7)

**Verifier:** R-VER-2–5, R-VER-10; chaos matrix; CI green; US-01–24 core stories.

**Bulkheads:** All
