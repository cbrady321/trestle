# Writing plugins

Plugins are ordinary Python callables wrapped by `@trestle`. Authors use **PluginSurface only** — no Kernel, FastMCP, or ledger imports.

## Shape

```python
from trestle.plugin.surface import Context, trestle


@trestle
def echo(ctx: Context, message: str = "hello") -> dict[str, str]:
    ctx.log(f"echo: {message}")
    return {"message": message}
```

Worked example: [`examples/plugins/echo.py`](../examples/plugins/echo.py).

- Return a small mapping that fits the default agent summary.
- Use `ctx.log` for structured events; wrapper stdout/`print` appears in `query(run_tail)`.
- Write keepers under `outputs/` — the foundation attaches them as artifacts.

## Deadlines, cancel and subprocesses

`ctx.deadline` is the run's deadline, fixed at admission (`timeout_s` from admission, queue time
included), and it is **enforced**: when it passes, the supervisor stops the run's whole process
tree, whether or not the plugin ever reads `ctx.deadline` or `ctx.cancelled`. It is no longer a hint
a plugin may ignore. Reading `ctx.cancelled` still lets a plugin stop cooperatively and cleanly
first.

A stop sends SIGTERM to every process attributable to the run (the run's process group and every
descendant by parent id, in whatever session it has moved to), waits at most `grace` (default 10 s),
sends SIGKILL, and waits at most `kill` (default 5 s) for confirmation: `stop_bound` in all
(`grace` + `kill`, 15 s by default), plus at most one supervisor `poll_interval` (0.05 s) to notice a
cancel. The values are published in `trestle.common.clock` (`grace`, `kill`, `stop_bound`,
`poll_interval`); the operator sets `TRESTLE_CANCEL_GRACE_S` and `TRESTLE_CANCEL_KILL_S`. A plugin
that needs to clean up on SIGTERM has `grace` to do it.

Subprocesses a plugin starts run non-interactively: their stdin is `/dev/null`, never the MCP
transport. They are inside the run's cancellable scope, so a daemon a plugin leaves behind is
stopped when the run ends, on every terminal path. A process that double-forks between two looks of
the supervisor can escape attribution; that limit is disclosed, not hidden.

---

## Publish paths

### Filesystem drop-in

```bash
cp my_tool.py ~/.trestle/plugins/
# or
trestle serve --plugin-dir ./tools
```

Persist search paths in `$TRESTLE_HOME/config.toml`:

```toml
[plugins]
paths = ["~/.trestle/plugins", "/abs/path/to/repo/tools"]
```

Hot reload: drop a new `.py` file or call `publish_plugin`; `list_plugins` shows a bumped `registry_version`.

### Runtime (MCP)

```json
{
  "source": "from trestle.plugin.surface import Context, trestle\n\n@trestle\ndef my_tool(ctx: Context) -> dict[str, str]:\n    return {\"ok\": \"yes\"}\n",
  "name": null
}
```

Success → `PublishView` with `name`, `registry_version`. Failure → `RequestOutcome` with `origin: "publication"` (no `run_id`).

After publish: `list_plugins` → optional `describe_plugin` → `run(plugin=…)`.

## Agent discovery

| Step | Tool |
|------|------|
| Names only | `list_plugins` |
| One schema | `describe_plugin(plugin_id=…)` |
| Execute | `run(plugin=…, args={…})` |

Plugin JSON schemas are **not** on `tools/list`. Empty catalog → `admission.plugin_not_found`; run `trestle init` or publish a plugin.

## Workflow packs

Pre-built Docker / pytest / migration plugins live under `examples/packs/`. Install `pip install -e ".[packs]"` and point `--plugin-dir` at that folder. Guide: [`packs.md`](packs.md).

## Related

- Agent workflow: [`agents.md`](agents.md)
- Full retrieval playbook: [`agent-console-mcp.md`](agent-console-mcp.md)
