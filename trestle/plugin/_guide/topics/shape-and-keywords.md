# Plugin shape and `@trestle` keywords

```python
from trestle.plugin import Context, trestle


@trestle
def echo(ctx: Context, message: str = "hello") -> dict[str, str]:
    ctx.log(f"echo: {message}")
    return {"message": message}
```

- The first parameter is the context (`ctx: Context`). Every other parameter needs a type hint
  from `trestle://guide/plugins/types`, and the function needs a return hint.
- The entry point must be a plain `def`. An `async def` entry is refused.
- One `@trestle` function per file. Its name is the plugin's name.
- Return a small mapping. The run summary keeps what fits, and the whole result stays
  fetchable at `{run_id}/result`.

## `@trestle` keywords

The call form `@trestle(...)` is read from the source at publication, so every value must be a
literal. An unknown keyword or a non-literal value is refused with
`publication.validation_failed`.

| Keyword | Meaning |
|---|---|
| `deadline` | Seconds (or a `timedelta`), 300 by default, 3600 at most. It is enforced: when it passes, the run's whole process tree is stopped. `ctx.deadline` holds it. |
| `summary_fields` | Result fields the summary keeps first. `"targets[id,state]"` shows a list-of-objects field as a table of those columns. |
| `packages` | The modules or packages from outside the standard library that the plugin imports. Each one's code is recorded at publication and checked at every run start (`execution.provenance_mismatch` when it changed). An undeclared import publishes with a `warnings` entry. |
| `env_arg` | The argument whose value names the run's environment. At most one run per value runs at a time; others wait or are refused `admission.environment_busy`. Pass the value in `args`: a default is not read at admission. A plugin that imports `trestle.workflow.ports` or a pack port module must declare it (`publication.env_arg_missing`). |
| `credentials` | Names of the credential identities the plugin uses (`["gh"]`). The operator stores and grants each; read one with `ctx.credential_env` or `ctx.credential_file`. See `trestle://guide/plugins/credentials`. |
| `repeatable` | `True` when a half-done run is safe to run again: after an `interrupted` run, resending the same idempotency key starts a fresh run. |
| `stop_grace` | Seconds (plain plugins only, capped by the operator) between a stop and the kill. When a run is cancelled, or nears its deadline, `ctx.cancelled` turns true, and you have this long to write a last status and return. A workflow that declares it is refused. |
| `adapted_from` | The name of the plugin this one adapts, such as a pack plugin you copied under a new name. Lineage only: `describe_plugin` shows it. |
