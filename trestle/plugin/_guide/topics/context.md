# The context

- `ctx.log(message)`: a structured log event. `print` output also reaches `query(run_tail)`.
- `ctx.progress(message, fraction=...)`: progress the caller sees live.
- `ctx.event(kind, **fields)`: an event of your own kind. Reserved kinds (`log`, `progress`,
  `artifact_available`, `error`, `plan`, `issue`, `confirmation`, `result`, `released`,
  `step`, `node_end`) and an empty kind raise `ValueError`.
- `ctx.tmp` is a scratch directory and `ctx.outputs` a directory whose files become artifacts
  when the run ends.
- `ctx.remaining()` is the number of seconds left before the deadline. `ctx.cancelled` is true
  once the run is stopping.
- Events have per-event size, count and rate limits. An event over a limit is dropped, and the
  run's `limits_exceeded` names it.
- The context is safe to use from several threads.
- Settings a plugin reads come from the environment the operator configures (`[plugins.env]`
  in the service's configuration), never from your shell.
