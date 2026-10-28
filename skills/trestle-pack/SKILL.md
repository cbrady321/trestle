---
name: trestle-pack
description: >-
  Write, adapt or publish a Trestle plugin or pack: one Python plugin that chains the steps of a
  local job (lint, types, tests, build, a Docker stack) into one run with one verdict. Use before
  writing or changing a plugin or pack, or when a job of several steps has no plugin yet.
  Triggers: write a plugin, chain steps, publish_plugin, pack, adapt a plugin.
---

# Trestle plugins and packs

A plugin is one Python file with one `@trestle` function, and it is the chain: Python sets the
order and conditions, and the run answers one verdict. The details live in the running service
under `trestle://guide/plugins`; read one topic at a time so they always match it.

## 1. Before writing

`list_plugins`: does a plugin or pack nearly fit? `describe_plugin` gives a pack plugin's
`source_path`; read it with your file tools, then adapt it under a new name
(`@trestle(adapted_from="<name>")`) or import its library (`trestle://guide/examples/pack_wrapper`).
A pack's source is open; Trestle's own source and home are not.

## 2. The pattern

Start from `trestle://guide/examples/chain`:

- one function per step, each returning a `Step(name, ok, detail)`, all gathered in one `Verdict`;
- Python for order and conditions, a `ThreadPoolExecutor` for steps side by side (the context
  is thread-safe), and no later steps once one fails;
- `raise Failed(code, detail, result=verdict)` when the check fails
  (`trestle://guide/plugins/failed`). The run answers `failed` with code `plugin.<code>`, and a
  run held behind it with `when: "succeeded"` is not released. A normal return always answers
  `passed`, so `ok: false` in the result is not a failure;
- `raise Blocked(code, human_action)` when a person must act, a missing tool included
  (`trestle://guide/plugins/blocked`).

## 3. The rules

- `ctx.exec(argv, cwd=..., output="<step>.log")`, never `subprocess`.
- Hint every parameter and the return with parameterized types (`dict[str, str]`, not `dict`).
- Declare every non-stdlib import in `packages=`. Declared packages are recorded, not snapshotted:
  if one changes after publication, runs stop with `execution.provenance_mismatch`.
- A `deadline` that fits the whole chain (300 s by default). Over 3600 s, or over the service's
  ceiling (`query(view="status")`), is refused with `admission.budget_does_not_fit`.
- Check `ctx.cancelled` between steps; `stop_grace=` leaves time to write a last status.
- `summary_fields` is a list: `["ok", "steps[name,ok,detail]"]` puts the step table in the summary.
  Live progress: `ctx.event("chain_step", ...)` (`step` is a reserved kind).
- `@trestle(...)` values are literals (`trestle://guide/plugins/shape-and-keywords`). Settings come
  from the operator's `[plugins.env]`, tokens by name (`trestle://guide/plugins/credentials`).

## 4. Test, publish, run

1. Test without a server with `trestle.plugin.testing`: `FakeContext(tmp_path, exec_handler=...)`
   and `call_plugin`. Script a failing step with `exec_result(...)` and catch it with
   `pytest.raises(Failed)` to check its code, detail and result (`trestle://guide/plugins/testing`).
2. `publish_plugin(source=..., name="<repo>_<objective>")`. Use a name of your own: publishing a
   name again replaces it for everyone (last write wins). `list_plugins` shows `valid` and
   `warnings`; a publication refusal's fix is in `trestle://guide/plugins/refusals`.
3. One real run: `run(plugin="<name>", args={...}, wait_ms=1000, completion="terminal")`.

**A new key per published revision.** A resend with the same `idempotency_key` joins the earlier
run even after you republish, so you would read the old code's answer. Put the source hash in the
key (`"<name>:<commit>:<source hash>"`), or send no key while iterating.

No `publish_plugin` tool (the restricted profile): say you cannot publish, and order the
allowlisted plugins with `after=` as the `trestle` skill describes.

## 5. Plugin or pack

A one-off chain stays a published plugin. Make it a pack (a library plus thin wrapper files and a
`pack.toml`) when other repos or agents will reuse it: `trestle://guide/plugins/packs`. Installing
a pack is the operator's job: report what to install, never install it yourself.

## 6. Advanced: the tree engine

`trestle.workflow` declares a tree of units with `needs`, budgets and a release step. Use it only
when a step owns a resource that must be found again after a restart, released on the way out, or
shared across runs; otherwise the plain chain is shorter. See `trestle://guide/plugins/workflows`.
Its `Failed` is a value a unit returns, not the `trestle.plugin.Failed` you raise.
