# Trestle v0.1 — Program Architecture

Cross-project decomposition and bulkhead HLDs for the `trestle-v01` brownfield migration program.

**Authority:** Freeze HLD at `hld/hld-interface-architecture-trestle.md` is the system contract. These artifacts map that contract onto delivery units and name cross-unit interface obligations.

| Artifact | Role |
|----------|------|
| [00-projects-and-bulkheads.md](00-projects-and-bulkheads.md) | Units as nodes; capability cards; neighbor matrix |
| [hld/00-trestle-v01-system-hld-writer.md](hld/00-trestle-v01-system-hld-writer.md) | System HLD pointer + composition proof |
| [hld/I01-admit-scheduler-execute.md](hld/I01-admit-scheduler-execute.md) | Admit → Scheduler → Execute bulkhead |
| [hld/I02-execute-wrapper-child.md](hld/I02-execute-wrapper-child.md) | Execute → wrapper → child bulkhead |
| [hld/I03-project-query-evidence.md](hld/I03-project-query-evidence.md) | Project ↔ query backend ↔ evidence bulkhead |
| [hld/I04-mcp-control-surface.md](hld/I04-mcp-control-surface.md) | FastMCP porch ↔ ControlSurface bulkhead |
