# One call per objective: plugins as chains, and two skills

**Status:** design; the owner agreed Q-1 to Q-5 as recommended (2026-10-23). Nothing here is built.
**Date:** 2026-10-23
**Release:** its own change after 0.6.0.
**Read at:** master `8ab91e51`, and the guide served by the running service (`fc4999d0`).

---

## The idea in plain words

Trestle's first goal is that an agent spends **one tool call per objective**. The way to get there
is already in the product: the agent writes a short Python plugin that chains the commands, with
whatever judgment the job needs (conditions, retries, steps running side by side, reading output),
publishes it, and runs it once. The plugin *is* the chain, and Python is the DAG.

Nothing new is needed to express a chain. Three things are wrong around it:

1. **A plain plugin cannot say "this failed".** A normal return always answers `passed`. A chain
   whose tests failed answers `passed` with `ok: false` buried in the result, so the agent's one
   rule ("read `answer.outcome`") gives the wrong verdict.
2. **The guide points multi-step work at the wrong tool.** The `workflows` topic sends agents to
   the `trestle.workflow` tree engine, whose example is about 150 lines for three steps. Nothing
   shows the plain chain.
3. **The skill teaches the backup option first.** It leads with chaining separate runs through
   `after=`. Writing a plugin comes last.

This change fixes all three: one small runtime addition (`Failed`), one guide example, and the
one skill replaced by two.

**Not in this change:** a DAG helper for plain plugins (`ctx.step`, `needs=`), and a
`run(steps=[...])` that takes a list of steps as JSON. Python already does both jobs. A JSON list is
a fixed sequence with no judgment, and a helper adds API before any evidence that agents need it.
`after=` stays, as the way to order runs that someone else sends or that are already running.

---

## What already works

| Need | Already there |
|---|---|
| Order and conditions | Python: `r = ctx.exec(lint)`, then `if r.ok: ...` |
| Steps side by side | `concurrent.futures` around `ctx.exec`. The context is thread-safe (`guide/plugins/context`), and every process belongs to the run's stop scope |
| Deadline and clean stop | `@trestle(deadline=)`, `ctx.remaining()`, `ctx.cancelled`, `stop_grace` |
| A person must act | `raise Blocked(code, human_action)` gives `outcome: "blocked"` |
| A record for each step | the result, plus `ctx.event("chain_step", ...)` for live progress (`step` is a reserved kind) |
| A short answer for each step | `summary_fields="steps[name,ok,detail]"` shows the steps as a table in the summary |
| Using a pack inside a chain | import its library and declare it in `packages=` (`guide/examples/pack_wrapper`) |
| Publish and run without the operator | `publish_plugin` (the guide reports `runtime_publishing: true`), then `run` |

---

## Change 1: a plain plugin can end `failed`

### The gap

`trestle/server/answer.py` answers a plain plugin (no plan) from the terminal kind
(`_planless`, B4-T4). `succeeded` always maps to `passed`. A raise maps to `execution_error`,
which means "setup broke", not "the thing you checked is wrong". Only a workflow unit can produce
`failed`, by returning `Failed(code, detail)` from `trestle.workflow`.

### The addition

`trestle.plugin.Failed`, the twin of `Blocked`:

```python
from trestle.plugin import Failed

raise Failed("tests_failed", "3 of 412 tests failed in tests/api", result=verdict)
```

- The run ends `succeeded` with `outcome: "failed"`. `answer.primary` carries
  `code: "plugin.tests_failed"` and the `detail`. Codes follow `Blocked`'s rule: lower-case
  words joined by `_` or `.`.
- `result=` (optional) is recorded exactly as a return would be: the same JSON form, kept at
  `{run_id}/result`, with `summary_fields` applied. The verdict and the per-step table arrive in
  the same answer. Without it, the result is `null`.
- Recorded like `Blocked`: `evidence/failed.json`, read by a `read_plugin_failed` beside
  `read_plugin_blocked`. `_planless` maps a `succeeded` run with that record to
  `OutcomeClass.FAILED`.
- `needs_detail` already treats `FAILED` as needing detail (`answer.py:530`), so the answer
  carries the primary without new wiring.
- `trestle.plugin.testing`: `Failed` propagates out of `call_plugin` like `Blocked` does, so a
  test asserts its `code`, `detail` and `result` with `pytest.raises`. Nothing to add there.
- A workflow plugin that raises it is refused at publication (`Failed` from `trestle.workflow`
  is the unit's form). This mirrors `Blocked`.

### Packs

`repo_tests` and the other pack plugins that return `ok: false` for a failed check should raise
`Failed(..., result=...)` instead. The result stays the same, and the verdict becomes right.
This is a breaking change for anyone reading `outcome: "passed"` plus `ok: false`; there is one
consumer, and the new behaviour is the correct one.

---

## Change 2: the guide shows the chain first

### A new example, `trestle://guide/examples/chain`

A complete plugin that publishes as-is and stays under about 60 lines. A sketch (the published
file must pass publication and the guide tests):

```python
"""A chain: lint and type-check side by side, then the tests, judged as one verdict.
One run, one answer. Each step is recorded; a failed step stops the steps after it."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from trestle.plugin import Context, Failed, trestle


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


@dataclass
class Verdict:
    ok: bool
    steps: list[Step]


def _step(ctx: Context, name: str, argv: list[str], cwd: str) -> Step:
    r = ctx.exec(argv, cwd=cwd, output=f"{name}.log")
    detail = "ok" if r.ok else (r.stderr or r.stdout)[-300:].strip() or f"exit {r.returncode}"
    ctx.event("chain_step", name=name, ok=r.ok)
    return Step(name, r.ok, detail)


@trestle(deadline=900, summary_fields="steps[name,ok,detail]")
def chain(ctx: Context, repo: str) -> Verdict:
    with ThreadPoolExecutor(2) as pool:
        lint = pool.submit(_step, ctx, "lint", ["ruff", "check", "."], repo)
        types = pool.submit(_step, ctx, "types", ["mypy", "."], repo)
        steps = [lint.result(), types.result()]
    if all(s.ok for s in steps) and not ctx.cancelled:
        steps.append(_step(ctx, "tests", ["pytest", "-q", "-x"], repo))
    verdict = Verdict(all(s.ok for s in steps), steps)
    if not verdict.ok:
        failed = next(s for s in steps if not s.ok)
        raise Failed(f"{failed.name}_failed", failed.detail, result=verdict)
    return verdict
```

It is tested like the other served examples (`tests/tree/test_tree_docs.py` runs each one; add it
to `tests/test_plugin_authoring.py`'s named set). The test runs one request where every step
passes, and one where `lint` fails and `tests` never runs.

### Topic changes

- **`topics/workflows.md`** opens with the plain chain: "Several steps are one plain plugin.
  Use Python for order and conditions, a thread pool for steps side by side, `Failed` for the
  verdict. See `examples/chain`." The tree engine moves to a closing paragraph: use it when a
  step owns a resource that must be found again after a restart, released on the way out, or
  shared across runs.
- **`index.md`**: the topic line becomes `workflows: several steps in one plugin`. A rule is
  added: `6. Several steps are one plugin; raise Failed when the check fails.`
- **New `topics/failed.md`**: the `Failed` reference, the same size as `blocked.md`.
- **`root.md`** (`trestle://guide`): one line under the list, "Multi-step work is one plugin:
  `trestle://guide/examples/chain`."
- **`authoring.server_instructions`**: one sentence. "For a job of several steps, write one
  plugin that chains them (`trestle://guide/examples/chain`) and run it once."

---

## Change 3: two skills in place of one

### Today

There is one `trestle` skill, with three copies that have drifted: `skills/trestle/SKILL.md`
(212 lines), `~/.claude/skills/trestle/SKILL.md` (166) and `.cursor/skills/trestle/SKILL.md`
(97). It mixes using Trestle, writing plugins and a table of refusal codes.

### `trestle`: get the job done in one call

It loads when the trestle tools are present and the agent is about to do local work. Aim: under
90 lines. Order of the body:

1. **Why** (three lines): every tool call re-sends the conversation; a run carries the
   whole objective; the answer is one verdict.
2. **The one move.** Name the objective. Is there a plugin for it (`list_plugins`)? Run it once:
   `run(..., wait_ms=1000, completion="terminal")`. If there is none and the job has more than
   one step, write one (skill `trestle-pack`), publish it, and run it once. If the same chain of
   calls comes up twice, it should be a plugin.
3. **Finding things.** The tools present means Trestle is present; their absence means report it
   and stop. Plugins and packs: `list_plugins`, then `describe_plugin`. Everything else:
   `trestle://guide`. No paths, and no plugin names except as examples, because they vary by
   service.
4. **Reading the answer.** `answer.outcome`: `passed` move on; `failed` read the named step and
   fix it; `blocked` report the human action word for word and stop; a refusal means nothing ran.
   Smallest read first: summary, then `last_error`, then a log slice. Never read a log to confirm
   a pass.
5. **Not doing it twice.** `idempotency_key` for anything you might send again.
6. **Waiting** when the host cuts calls off (the `await_runs` loop), and runs outliving the
   session.
7. **Fallbacks, last.** `after=` for ordering against a run you did not send. `exec` for a single
   command no plugin owns.
8. **Ground rules.** MCP only; never the service's or the operator's commands.

Leaves out: the refusal-code table (each refusal's message says what to do, and
`guide/plugins/refusals` has the full list), the `repo_tests` argument walkthrough (that is
`describe_plugin`'s job), and the plugin-writing rules (the other skill).

### `trestle-pack`: write the chain

It loads when the agent is about to write or change a plugin or pack. Aim: under 80 lines. It is
mostly a workflow that points into the guide, so the details always match the running service:

1. **Before writing.** `list_plugins`: does a pack nearly fit? Read its source and adapt it
   (`adapted_from=`), or import its library.
2. **The pattern.** `guide/examples/chain`: steps as functions, Python for order, a thread pool
   for steps side by side, a `Step` record each, one `Verdict`, `Failed` with `result=` when the
   check fails, `Blocked` when a person must act.
3. **The rules.** `ctx.exec`, never `subprocess`; hint everything; `packages=`; `deadline` that
   fits; check `ctx.cancelled` between steps; `summary_fields` for the step table.
4. **Test, publish, run.** `trestle.plugin.testing` with a scripted `exec_handler` for the
   failure path, then `publish_plugin`, then one real run.
5. **Plugin or pack.** A one-off chain stays a published plugin. Turn it into a pack when other
   repos or agents will reuse it (`guide/plugins/packs`).
6. **Advanced.** The tree engine, one paragraph, pointing at `guide/plugins/workflows`.

### One source

- `skills/trestle/` and `skills/trestle-pack/` are the only copies in the repo.
- `.cursor/skills/trestle/` is removed. Its facts that tests pin move to `skills/trestle/` (see
  the test plan).
- README and `docs/install.md` say to copy both folders into the host's skills folder
  (`~/.claude/skills/`, `~/.cursor/skills/`), and the installed `~/.claude/skills/trestle/` is
  replaced from the repo when this ships.
- The `description` lines are written for triggering: `trestle` on "run tests, build, pipeline,
  any multi-step local job"; `trestle-pack` on "write, adapt or publish a plugin or pack".

---

## Test plan

| # | Test |
|---|---|
| 1 | `Failed` in a plain plugin: the run is `succeeded`, `outcome` is `failed`, `answer.primary.code` is `plugin.<code>`, the detail is shown, and `result=` is at `{run_id}/result` with `summary_fields` applied |
| 2 | `Failed` without `result=`: the result is `null` and the answer is otherwise the same |
| 3 | A bad code (upper case, spaces, empty) is refused the way `Blocked`'s is |
| 4 | A workflow plugin that raises `trestle.plugin.Failed` is refused at publication |
| 5 | `trestle.plugin.testing`: `pytest.raises(Failed)` around `call_plugin` sees its code, detail and result |
| 6 | Restart: a run that wrote `failed.json` and was then interrupted answers as recovery decides today; `failed.json` is never read for an `interrupted` run |
| 7 | `examples/chain` publishes and runs: all steps pass (`passed`, three rows); `lint` fails (`failed`, code `plugin.lint_failed`, two rows, no `tests` step) |
| 8 | The guide tests: `chain` is in the named example set, `workflows.md` names it first, `failed.md` exists, and `test_agent_docs_mcp_only` reads both skills |
| 9 | The doc tests that pin `.cursor/skills/trestle/SKILL.md` (`test_k11_long_job_guidance`, `test_cl_c1_docs`, `test_agent_docs_mcp_only`) are moved to `skills/trestle/SKILL.md`, keeping each assertion's meaning |
| 10 | Pack plugins that switch to `Failed` keep their result shape; their tests assert `outcome: "failed"` for a failing check |

---

## Decisions

The owner agreed every recommendation below on 2026-10-23.

**Q-1. `Failed` as an exception, or a returned `ok: false` read by convention?**
*Recommendation: the exception.* It mirrors `Blocked`, so there is one way to say "the verdict is
not a pass". A magic key in the result would let any plugin flip its verdict by accident, and the
result shape would stop being the plugin's own.

**Q-2. Should the pack plugins switch to `Failed` in this change?**
*Recommendation: yes, `repo_tests` at least.* It is the plugin agents run most, and with
`ok: false` its verdict is wrong today. The other packs can follow in the same release if their
tests make it cheap.

**Q-3. Remove `.cursor/skills/trestle/`, or keep it generated from `skills/trestle/`?**
*Recommendation: remove it.* Cursor reads the same `SKILL.md` format from `~/.cursor/skills/`, and
a generated copy is still a second file to keep correct.

**Q-4. The second skill's name: `trestle-pack` or `trestle-plugin`?**
*Recommendation: `trestle-pack`.* It matches how the owner describes the work, and it covers
single plugins: a published plugin is the smallest pack.

**Q-5. Should the `trestle` skill still name `repo_tests` and `exec`?**
*Recommendation: as examples only.* They are the two plugins nearly every service has, which
makes them good illustrations, but `describe_plugin` is the reference for their arguments.
