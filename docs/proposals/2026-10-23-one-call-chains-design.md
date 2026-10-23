# One call per objective: plugins as chains, and two skills

**Status:** design, revision 2. The owner agreed Q-1 to Q-5 as recommended (2026-10-23).
Revision 2 folds in every Blocking, Should-fix and Nit item of the
[review](2026-10-23-one-call-chains-review.md); the "Review items" table at the end maps each one.
Nothing here is built.
**Date:** 2026-10-23
**Release:** its own change after 0.6.0.
**Read at:** master `8ab91e51`, and the guide served by the running service (`fc4999d0`).

---

## The idea in plain words

Trestle's first goal is that an agent spends **one tool call per objective**. The way to get there
is already in the product: the agent writes a short Python plugin that chains the commands, with
whatever judgment the job needs (conditions, retries, steps running side by side, reading output),
publishes it, and runs it once. The plugin *is* the chain, and Python is the DAG. A new chain
takes two calls the first time (publish, then run); every later run takes one.

Nothing new is needed to express a chain. Four things are wrong around it:

1. **A plain plugin cannot say "this failed".** A normal return always answers `passed`. A chain
   whose tests failed answers `passed` with `ok: false` buried in the result, so the agent's one
   rule ("read `answer.outcome`") gives the wrong verdict.
2. **Trestle itself reads the wrong verdict.** Ordering (`after`), waiting
   (`await_runs first_failure`), the failure view and `outcome.class` all look at `state`, which
   says only that the plugin *finished*. A `blocked` run already releases a run held behind it
   with `when: "succeeded"`; a `failed` one would too.
3. **The guide points multi-step work at the wrong tool.** The `workflows` topic sends agents to
   the `trestle.workflow` tree engine, whose example is about 150 lines for three steps. Nothing
   shows the plain chain.
4. **The skill teaches the backup option first.** It leads with chaining separate runs through
   `after=`. Writing a plugin comes last.

This change fixes all four: `Failed` and an honest verdict everywhere Trestle reads one (Change 1),
one guide example (Change 2), and the one skill replaced by two (Change 3).

**Not in this change:** a DAG helper for plain plugins (`ctx.step`, `needs=`), and a
`run(steps=[...])` that takes a list of steps as JSON. Python already does both jobs. A JSON list is
a fixed sequence with no judgment, and a helper adds API before any evidence that agents need it.
`after=` stays, as the way to order runs that someone else sends or that are already running, and
as the fallback where publishing is turned off.

---

## What already works

| Need | Already there |
|---|---|
| Order and conditions | Python: `r = ctx.exec(lint)`, then `if r.ok: ...` |
| Steps side by side | `concurrent.futures` around `ctx.exec`. The context is thread-safe (`guide/plugins/context`), and every process belongs to the run's stop scope |
| Deadline and clean stop | `@trestle(deadline=)`, `ctx.remaining()`, `ctx.cancelled`, `stop_grace` |
| A person must act | `raise Blocked(code, human_action)` gives `outcome: "blocked"` |
| A record for each step | the result, plus `ctx.event("chain_step", ...)` for live progress (`step` is a reserved kind) |
| A short answer for each step | `summary_fields=["ok", "steps[name,ok,detail]"]` shows the steps as a table in the summary |
| Using a pack inside a chain | import its library and declare it in `packages=` (`guide/examples/pack_wrapper`) |
| Publish and run without the operator | `publish_plugin` (the guide reports `runtime_publishing: true`), then `run` |

---

## Change 1: a plain plugin can end `failed`, and every reader believes it

### 1a. The gap

`trestle/server/answer.py` answers a plain plugin (no plan) from the terminal kind
(`_planless`, B4-T4). `succeeded` always maps to `passed`. A raise maps to `execution_error`,
which means "setup broke", not "the thing you checked is wrong". Only a workflow unit can produce
`failed`, by returning `Failed(code, detail)` from `trestle.workflow`.

### 1b. The addition: `trestle.plugin.Failed`

The twin of `Blocked`, in `trestle/plugin/surface.py`:

```python
from trestle.plugin import Failed

raise Failed("tests_failed", "3 of 412 tests failed in tests/api", result=report)
```

- `Failed(code, detail, result=None)`. `code` follows `Blocked`'s rule (lower-case words joined
  by `_` or `.`; anything else is a `ValueError` at the raise, which ends the run
  `execution_error` like any other exception). `detail` is a non-empty string.
- The run ends `succeeded` with `answer.outcome: "failed"`. `answer.primary` is the root with
  `node_class: "failed"` and `code: "plugin.<code>"`, exactly as a workflow `Failed` answers
  today. **The answer carries the code only, no text** (B2): `NodeAnswer` has no text field, and
  a workflow `Failed` shows only its code too. The text lives in two places: `evidence/failed.json`
  (the record) and the result, when the plugin passes one (`result=`, typically the step table,
  which `summary_fields` brings into the answer's summary).
- `answer.needs_detail` already counts `FAILED` (`answer.py:530`): that sets the `detail` handle
  (`{run_id}/answer`), nothing more.

### 1c. How the child records it (S2)

In `child/main.py` `_call_plugin`:

1. `except Failed` sits next to `except Blocked`, before `except Exception`.
2. It writes `evidence/failed.json` first: `{code: "plugin.<code>", detail}`, the detail through
   the same scrub and sanitizer as an error message (bounded like one, 512 bytes).
3. Then, if `result` is not None, it records the result exactly as a return would: the same
   `write_result` call with the declared return type, the same `result.index`, so `summary_fields`
   apply to it.
4. A result that is too large: `result.state` reads `too_large`, as for a return. The run still
   answers `failed`; the summary shows the result as truncated, as it does today.
5. A result that cannot be encoded: the run still answers `failed`, the result is `null`, and
   `result.state` reads `unencodable` so the run view marks it in `limits_exceeded` (the same
   path as `too_large`). The verdict is never lost to a bad result.
6. The child exits 0.

`call_plugin` in `trestle.plugin.testing` lets `Failed` propagate (like `Blocked`), so a test
asserts `code`, `detail` and `result` with `pytest.raises`. It never encodes `exc.result`, so a
test does not catch a result that cannot be encoded; `failed.md` says so.

### 1d. How the server answers it

- `answer.py` gains `PluginFailed {code, detail}` and `read_plugin_failed(run_dir)` beside
  `read_plugin_blocked`. `answer_for_run` passes it to `project`, and `_planless` maps
  `error is None and failed is not None` to `OutcomeClass.FAILED` with the primary above. If a
  run somehow has both records, `failed` wins (it ranks above `blocked`).
- Like `blocked.json`, `failed.json` is read only for a run with no plan (`answer_plan is None`);
  a workflow plugin's answer comes from its tree, and a `failed.json` it wrote is ignored (S1).
  There is no publication check for either.
- An `interrupted` run is decided by rule (2) before `_planless`, so `failed.json` is never read
  for it.

### 1e. Every reader judges the verdict, not the state (B3)

**The rule.** A finished run has one verdict: `answer.outcome`. Every place in Trestle that
orders, waits on, lists or reports runs by success reads the verdict. `state` keeps its meaning
underneath (how the plugin process ended) and is still shown. This applies to `Blocked` as much as
to `Failed`, and to workflow plugins whose tree answered `failed` or `blocked`, which have the
same flaw today.

**One reader.** `answer.verdict_of(run_dir, state) -> str`: the `outcome` field of
`evidence/answer.json`. The conductor and recovery write that file before the terminal row, so
every run that ended under this version has it. When it is missing or unreadable (a run from
before B4), the verdict is the class `classify(state)` gives, which is today's behaviour. Readers
that already hold the recomputed answer (the run view) use it directly; the two agree (`differ d5`
compares them).

**The four places.**

| Place | Today | After |
|---|---|---|
| `after` with `when: "succeeded"` (`server/chain.py:193-196`) | released when the earlier run's `state` is `succeeded` | released when its `state` is `succeeded` **and** its verdict is `passed`. Otherwise unmet with a new reason, `earlier_not_passed`; the message names the verdict and code, for example `run r_… ended succeeded, answer failed (plugin.tests_failed)`. `match` is checked after that, as today. `when: "ended"` is unchanged |
| `await_runs(mode="first_failure")` (`server/project.py:916,931`) | wakes when a view's `state` is terminal and not `succeeded` | wakes when a view is terminal and its `answer.outcome` is not `passed` (every terminal view already carries its answer) |
| `query(view="recent_failures")` (`query/fs.py:36,168`) | finalized runs whose `state` is not `succeeded` | finalized runs whose verdict is not `passed`. Rows gain `outcome` (the verdict). `failed_at` for a run whose state is `succeeded` is its terminal time |
| `RunView.outcome` (`common/outcome.py`, `project.py:_outcome_view`) | `class` from the terminal kind: a `succeeded` run is always `passed` | for a `succeeded` run, `class` is `answer.outcome` and `code` is `answer.primary.code` when the class is `failed`, `blocked` or `execution_error`. Other states keep today's class and code. So `outcome.class` and `answer.outcome` agree on every run |

What follows from the last row without further code: `run_by_key`'s `outcome_class` column and the
callback body's `outcome_class` (both read `RunView.outcome`) become verdict-aware, and the
callback's `answer_outcome` is unchanged.

**The name `when: "succeeded"` stays.** It now means "succeeded, with a passing verdict". Renaming
it to `passed` would be clearer but breaks every caller for a word; the docs say what it means.

**Breaking.** A run held behind a `failed` or `blocked` run is now cancelled with
`execution.after_unmet` instead of released. `first_failure` now wakes on a workflow's `failed`
answer. `outcome.class` can read `failed` and `blocked`. Each is the correct behaviour, and the
owner allows breaks that improve Trestle. A caller that used `match: {"ok": true}` to stand in for
the verdict keeps working.

**Docs.** `docs/agents.md` says in four places that these readers key on `state` and that
`outcome.class` "never reads `failed` or `blocked`" (the "Wait and long jobs" paragraph, the quote
in "Large tasks: the tree" step 3, the `first_failure` bullet under "Ad-hoc large work", and the
`when` definition under "Run after another run"). Each is rewritten to the rule above, and the
same pass covers `docs/agent-console-mcp.md` and the guide's `callbacks.md`.

### 1f. Packs (Q-2, S3)

The plugins in this repo that return `ok: false` switch as follows. Each keeps its result shape
and passes it as `result=`.

| Plugin | Verdict | Code |
|---|---|---|
| `trestle-pack-pytest/plugins/pytest_run.py` | `Failed` for failing or erroring tests | `tests_failed` |
| `trestle-pack-docker/plugins/docker_stack.py` | `Failed` when a wave or the config step fails | `stack_failed` |
| `trestle-pack-migrate/plugins/migrate_apply.py` | `Failed` when the upgrade fails | `migration_failed` |
| `trestle-pack-github/plugins/github_pr_status.py` | `Failed` when a PR's checks did not pass | `checks_not_passed` |
| `trestle-pack-github/plugins/github_pr_comments.py` | `Failed` when a PR's comments could not be read | `comments_unread` |
| `trestle-pack-github/plugins/github_pr_open.py` | `Blocked` for a policy refusal (`resend: "will_not_succeed"`, the human action is what to change); `Failed` when a push or the PR call fails after the checks | `plugin.<refusal code>` / `open_failed` |

The codes are provisional: each is settled against the plugin's docstring and tests when it is
switched, and the docstrings that say "never a raised error" are rewritten. Ariadne's
`repo_tests.py` and `repo_gate.py` live in `~/projects/ariadne/scripts/trestle/`; they switch in a
separate follow-up in that repo.

This is a breaking change for anyone reading `outcome: "passed"` plus `ok: false`; there is one
consumer, and the new behaviour is the correct one.

---

## Change 2: the guide shows the chain first

### A new example, `trestle://guide/examples/chain`

A complete plugin that publishes as-is (B1: `summary_fields` is a list):

```python
"""A chain: lint and type-check side by side, then the tests, judged as one verdict.

One run, one answer. Each step is recorded; a failed step stops the steps after it. Publish it
under a name of your own (the repo and the objective): runtime publishes are last-write-wins."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from trestle.plugin import Blocked, Context, Failed, trestle


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
    try:
        r = ctx.exec(argv, cwd=cwd, output=f"{name}.log")
    except FileNotFoundError:
        raise Blocked("tool_missing", f"Install `{argv[0]}` for the service, then resend.") from None
    if r.ok:
        detail = "ok"
    elif r.returncode is None:
        detail = "timed out" if r.timed_out else "cancelled"
    else:
        detail = (r.stdout + r.stderr)[-300:].strip() or f"exit {r.returncode}"
    ctx.event("chain_step", name=name, ok=r.ok)
    return Step(name, r.ok, detail)


@trestle(deadline=900, summary_fields=["ok", "steps[name,ok,detail]"])
def chain(ctx: Context, repo: str) -> Verdict:
    with ThreadPoolExecutor(2) as pool:
        lint = pool.submit(_step, ctx, "lint", ["ruff", "check", "."], repo)
        types = pool.submit(_step, ctx, "types", ["mypy", "."], repo)
        steps = [lint.result(), types.result()]
    if all(s.ok for s in steps) and not ctx.cancelled:
        steps.append(_step(ctx, "tests", ["pytest", "-q", "-x"], repo))
    failed = next((s for s in steps if not s.ok), None)
    if failed is not None:
        raise Failed(f"{failed.name}_failed", failed.detail, result=Verdict(False, steps))
    return Verdict(len(steps) == 3, steps)  # two rows: stopped before the tests
```

Against the review's nits: a missing tool is `Blocked`, not an exception in a pool thread (N1); a
step that timed out or was cancelled says so, and the detail is the tail of both streams, so
ruff's and pytest's stdout findings show (N2); a run stopped before the tests returns `ok: false`
with two rows, never a passing verdict (N2); the docstring says to rename it (N3).

### Testing it (S7)

- `tests/tree/test_tree_docs.py` runs each served example for real. Its harness gains a `failed`
  expectation that checks the answer's code, and `chain` gets a fixture repo
  (`tests/tree/fixtures/chain_repo/`: one module with a type hint and one passing test, so mypy
  has a file and pytest collects one). The passing run needs `ruff`, `mypy` and `pytest` on the
  child's `PATH`; the test skips with a reason when one is missing.
- `tests/test_plugin_authoring.py` adds `chain` to its named example set and tests, with a
  scripted `exec_handler`: all steps pass (three rows, `ok: true`); `lint` fails (`Failed` with
  code `lint_failed`, two rows, no `tests` call); a missing tool (`Blocked`, `tool_missing`); a
  cancel before the tests (two rows, `ok: false`).

### Topic changes

- **`topics/workflows.md`** opens with the plain chain: "Several steps are one plain plugin.
  Use Python for order and conditions, a thread pool for steps side by side, `Failed` for the
  verdict. See `examples/chain`." The tree engine moves to a closing paragraph: use it when a
  step owns a resource that must be found again after a restart, released on the way out, or
  shared across runs.
- **`index.md`**: the topic line becomes `workflows: several steps in one plugin`, and
  `failed: when the check fails` is added. A rule is added:
  `6. Several steps are one plugin; raise Failed when the check fails.`
- **New `topics/failed.md`**, the size of `blocked.md`: the raise, what the answer carries (the
  code; the text in `result=`), that `after` and `first_failure` treat it as not passed, that a
  test catches it with `pytest.raises` (which does not encode `result`), and that
  `trestle.workflow.Failed` is a different thing: a dataclass a unit *returns*, and raising it is
  a `TypeError` that ends the run `execution_error` (N6).
- **`root.md`** (`trestle://guide`): one line under the list, "Multi-step work is one plugin:
  `trestle://guide/examples/chain`."
- **`topics/callbacks.md`** (N4): the body example becomes a `Failed` run: `state: "succeeded"`,
  `outcome_class: "failed"`, `answer_outcome: "failed"`, `error: null`.
- **`authoring.server_instructions`**: one sentence, in the unrestricted branch only (S5). "For a
  job of several steps, write one plugin that chains them (`trestle://guide/examples/chain`) and
  run it once."

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
   one step, write one (skill `trestle-pack`), publish it, and run it once: two calls the first
   time, one after that. If the same chain of calls comes up twice, it should be a plugin.
   **Where publishing is off** (`publish_plugin` absent; restricted profile, S5): run the
   allowlisted plugins and order them with `after={"key": ..., "when": "succeeded"}`, which waits
   for a passing verdict.
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
   session (`session_ended`, `held_by`, `retry_after_s`).
7. **Fallbacks, last.** `after=` for ordering against a run you did not send. `exec` for a single
   command no plugin owns (`plugin="exec"`).
8. **Ground rules.** MCP only; never the service's or the operator's commands ("Never run it
   yourself, through `exec` or any other way.").

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
   check fails, `Blocked` when a person must act (a missing tool included).
3. **The rules.** `ctx.exec`, never `subprocess`; hint everything; `packages=`; a `deadline` that
   fits (over 3600 s is `admission.budget_does_not_fit`); check `ctx.cancelled` between steps;
   `summary_fields` (a list) for the step table; declared packages are recorded, not snapshotted.
4. **Test, publish, run.** `trestle.plugin.testing` with a scripted `exec_handler` for the
   failure path, then `publish_plugin` under a name of your own (repo and objective: publishes
   are last-write-wins), then one real run. **A new key per published revision** (S4): a re-send
   with the same key joins the earlier run even after a republish, so put the source hash in the
   key, or send no key while iterating.
5. **Plugin or pack.** A one-off chain stays a published plugin. Turn it into a pack when other
   repos or agents will reuse it (`guide/plugins/packs`).
6. **Advanced.** The tree engine, one paragraph, pointing at `guide/plugins/workflows`.

### One source

- `skills/trestle/` and `skills/trestle-pack/` are the only copies in the repo.
- `.cursor/skills/trestle/` is removed; every pin on it moves (next section).
- README and `docs/install.md` say to copy both folders into the host's skills folder
  (`~/.claude/skills/`, `~/.cursor/skills/`), and the installed `~/.claude/skills/trestle/` is
  replaced from the repo when this ships.
- The `description` lines are written for triggering: `trestle` on "run tests, build, pipeline,
  any multi-step local job"; `trestle-pack` on "write, adapt or publish a plugin or pack".

### Where each pin goes (S6)

| Pin | Today | After |
|---|---|---|
| `tests/core/contract/test_cl_c1_docs.py:83-84`: `admission.budget_does_not_fit`, "recorded, not snapshotted" | `.cursor` skill | `skills/trestle-pack/SKILL.md` (rule 3) |
| `tests/core/docs/test_agent_docs_mcp_only.py:18-19` (MCP-only checks over every agent doc) | both skills | `skills/trestle/SKILL.md` and `skills/trestle-pack/SKILL.md` |
| `test_agent_docs_mcp_only.py:88-92`: `view="status"`, `retry_after_s`, `held_by`, `plugin="exec"`, `session_ended`, "Never run it yourself…" | `skills/trestle` | `skills/trestle/SKILL.md` (steps 3, 6, 7, 8) |
| `test_agent_docs_mcp_only.py:95-97` (the cursor skill says never run it) | `.cursor` skill | retired (the line above covers it) |
| `tests/single/docs/test_k11_long_job_guidance.py:20`: a `## Golden workflow` section with one `Long jobs` line naming `completion="terminal"` and bounded polling | `.cursor` skill | `skills/trestle/SKILL.md`; its heading for step 2 is `## The one move` and holds the one `**Long jobs**` line |
| `tests/proof/k_doc_map.toml:118` (K-11's doc list) | `.cursor` skill | `skills/trestle/SKILL.md`, with a note that the requirement's verbatim list named the retired file |
| `tests/proof/fence.d/{core,single,cz}.toml` globs | `.cursor/skills/trestle/SKILL.md` | `skills/*/SKILL.md` |
| `README.md:96`, `docs/agents.md:5,765`, `docs/agent-console-mcp.md:4` | link the `.cursor` skill | link both skills |

---

## Test plan

| # | Test |
|---|---|
| 1 | `Failed` in a plain plugin: the run is `succeeded`, `answer.outcome` and `outcome.class` are `failed`, `answer.primary.code` is `plugin.<code>`, `failed.json` holds the code and the scrubbed detail, and `result=` is at `{run_id}/result` with `summary_fields` applied |
| 2 | `Failed` without `result=`: the result is `null` and the answer is otherwise the same |
| 3 | A bad code (upper case, spaces, empty) or an empty detail is a `ValueError`, as for `Blocked` |
| 4 | `result=` too large: `failed`, `result.state` `too_large`, the summary truncated. `result=` that cannot be encoded: `failed`, result `null`, `result.state` `unencodable` in `limits_exceeded` |
| 5 | `trestle.plugin.testing`: `pytest.raises(Failed)` around `call_plugin` sees its code, detail and result |
| 6 | Restart: a run that wrote `failed.json` and was then interrupted answers as recovery decides today; `failed.json` is never read for an `interrupted` run, nor for a workflow run |
| 7 | `after` with `when: "succeeded"` behind a `Failed` run, a `Blocked` run and a workflow run that answered `failed`: each held run ends `cancelled`, `execution.after_unmet`, reason `earlier_not_passed`. Behind a passing run it is released. A run with no `answer.json` falls back to `state` |
| 8 | `await_runs(mode="first_failure")` wakes on a `Failed` run while another run is still going |
| 9 | `recent_failures` lists `Failed` and `Blocked` runs with their `outcome`, and not passing ones; `run_by_key`'s `outcome_class` and the callback's `outcome_class` read `failed` |
| 10 | `examples/chain`: the tree-docs harness runs it on the fixture repo (`passed`, three rows) and with a failing lint (`failed`, code `plugin.lint_failed`); `test_plugin_authoring` covers the four scripted paths above |
| 11 | The guide tests: `chain` is in the named example set, `workflows.md` names it first, `failed.md` exists, the unrestricted `server_instructions` names `examples/chain` and the restricted one does not |
| 12 | The pins in the table above, moved with each assertion's meaning kept; `.cursor/skills/` no longer exists |
| 13 | Each switched pack plugin keeps its result shape; its tests assert `pytest.raises(Failed)` (or `Blocked`) with the result for a failing check |

---

## Decisions

The owner agreed every recommendation below on 2026-10-23.

**Q-1. `Failed` as an exception, or a returned `ok: false` read by convention?**
*Recommendation: the exception.* It mirrors `Blocked`, so there is one way to say "the verdict is
not a pass". A magic key in the result would let any plugin flip its verdict by accident, and the
result shape would stop being the plugin's own.

**Q-2. Should the pack plugins switch to `Failed` in this change?**
*Recommendation: yes.* Revision 2: the in-repo plugins are listed in 1f; `repo_tests` lives in
ariadne and follows separately.

**Q-3. Remove `.cursor/skills/trestle/`, or keep it generated from `skills/trestle/`?**
*Recommendation: remove it.* Cursor reads the same `SKILL.md` format from `~/.cursor/skills/`, and
a generated copy is still a second file to keep correct.

**Q-4. The second skill's name: `trestle-pack` or `trestle-plugin`?**
*Recommendation: `trestle-pack`.* It matches how the owner describes the work, and it covers
single plugins: a published plugin is the smallest pack.

**Q-5. Should the `trestle` skill still name `repo_tests` and `exec`?**
*Recommendation: as examples only.* They are the two plugins nearly every service has, which
makes them good illustrations, but `describe_plugin` is the reference for their arguments.

---

## Review items

| Item | Where it is answered |
|---|---|
| B1 `summary_fields` must be a list | "What already works"; the `chain` example |
| B2 no text field in the answer | 1b: code only; the text in `failed.json` and `result=` |
| B3 readers key on `state` | 1e |
| S1 no publication check exists | 1d; old test 4 removed |
| S2 recording `result=` | 1c |
| S3 the real plugin list | 1f |
| S4 same key joins the old run | `trestle-pack` step 4 |
| S5 restricted profile | `trestle` step 2; `server_instructions` in the unrestricted branch |
| S6 pins | "Where each pin goes" |
| S7 test 7 cannot run | "Testing it" |
| N1-N3 | the `chain` example and the note under it |
| N4 callback example | "Topic changes" |
| N5 two calls for a new chain | "The idea in plain words"; `trestle` step 2 |
| N6 `trestle.workflow.Failed` is a dataclass | `failed.md` |
