# Composite workflows

A workflow plugin declares a tree in a `WorkflowEntry`: an `AllDeclaration` (or `ChoiceNode`)
root whose `ChildBinding`s name leaves or further composites, with `needs` ordering siblings. A
leaf class has `declare`, `observe`, `advance` and `release`. `advance` and `release` return
`Acted()`, `NoAction(reason)`, `Blocked(code, human_action, resend)` or `Failed(code, detail)`.
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
