# Several steps in one plugin

Several steps are one plain plugin, run once. Use Python for order and conditions, a
`ThreadPoolExecutor` around `ctx.exec` for steps side by side (the context is thread-safe),
`ctx.cancelled` between steps, and `raise Failed(code, detail, result=...)` for the verdict when
a step fails. Record each step and put the table in `summary_fields`. See
`trestle://guide/examples/chain`. Publishing a new chain and running it is two calls; every run
after that is one.

## Composite workflows (the tree engine)

Use the tree engine only when a step owns a resource that must be found again after a restart,
released on the way out, or shared across runs. A workflow plugin declares a tree in a
`WorkflowEntry`: an `AllDeclaration` (or `ChoiceNode`) root whose `ChildBinding`s name leaves or
further composites, with `needs` ordering siblings. A leaf class has `declare`, `observe`,
`advance` and `release`. `advance` and `release` return `Acted()`, `NoAction(reason)`,
`Blocked(code, human_action, resend)` or `Failed(code, detail)` (from `trestle.workflow`).
The plugin function calls `run_tree` from `trestle.workflow.loop` with the port implementations.
Keep `@trestle(deadline=)` equal to `WorkflowEntry.deadline`. Budgets are checked at every
admission:

- the root's budget plus 10 s must fit the deadline;
- each child's budget must be at most its parent's less 10 s;
- an `AllDeclaration`'s budget must cover its longest `needs` chain, and
  ceil(children / `concurrency`) × its largest child.

A unit never sleeps. To wait for a state, call `ctx.cancellation.wait_for(predicate, bound)`,
and return without acting on `WaitOutcome.INTERRUPTED`. See
`trestle://guide/examples/composite_workflow`.
