# Review: one-call chains design

**Reviewer:** Opus 5.5 (high), 2026-10-23, read-only against master `8ab91e51`.
**Of:** [`2026-10-23-one-call-chains-design.md`](2026-10-23-one-call-chains-design.md).
**Spot-checked by the orchestrator:** B1 (`server/plugin_schema.py:542`), B3 (`server/chain.py`
releases on `state == "succeeded"` only), S3 (`~/projects/ariadne/scripts/trestle/repo_tests.py`),
S4 (`server/idempotency.py:65-75`). All four hold.

The owner's five decisions stand. Nothing below argues against them. The findings must be folded
into the design before implementation.

## Blocking

**B1. The `examples/chain` sketch does not publish.** `summary_fields="steps[name,ok,detail]"`
is a string. `plugin_schema.py:540-542` accepts only a list or tuple of string literals. The same
wrong form appears in the "What already works" table.
*Fix:* `summary_fields=["ok", "steps[name,ok,detail]"]` in both places.

**B2. The answer has no field for the failure text.** `NodeAnswer` (`answer.py:63-71`) and
`_node_wire` (`answer.py:596-606`) carry `code`, `human_action` and `resend`, nothing else.
`answer.detail` is the handle `{run_id}/answer` (`answer.py:129`). `needs_detail` (`:530`) sets
that handle, not any text. So "the detail is shown" and "the answer carries the primary without
new wiring" are both wrong. A workflow `Failed` today shows only its code as well.
*Fix (KISS):* the answer carries `primary.code`; the text lives in `failed.json` and in
`result=` (the step table). Update test 1. If a text field is wanted, specify a bounded
`primary.message` and count it in the fields that are always inline.

**B3. A `Failed` run would satisfy `when: "succeeded"`.** Several places look only at `state` and
never at the answer:
- `chain.py:193-196` releases a held run when `state == "succeeded"`.
- `await_runs` `first_failure` uses `_FAILURE_TERMINAL_STATES` (`project.py:916,931`).
- The `recent_failures` view filters on state (`query/fs.py:36,168`).
- `outcome_class` maps `succeeded` to `passed` (`common/outcome.py:39-41`), and that value goes
  into the callback body and `RunView.outcome`.

The proposal moves test plugins to `Failed` and drops the skill's `match: {"ok": true}` pattern.
Together those mean `after={"key": tests}` would start a deploy after the tests failed. `Blocked`
runs already have this flaw today.
*Fix:* specify that `when: "succeeded"`, `first_failure` and `recent_failures` all require
`answer.outcome == "passed"`, reading `evidence/answer.json` (or `failed.json`/`blocked.json`).
The owner allows breaking changes. Specify `outcome_class`/the callback's `answer_outcome` the
same way. Add tests. Fix `docs/agents.md:235`, which says the outcome class "never reads failed
or blocked".

## Should-fix

**S1. "Refused at publication, mirroring `Blocked`" describes a check that does not exist.**
`child/main.py:154` catches `Blocked` for every plugin, and `answer.py:835` reads `blocked.json`
only when there is no plan.
*Fix:* delete that bullet and test 4. Say that `failed.json` is ignored for workflow runs, the
same way `blocked.json` is.

**S2. Recording `result=` is underspecified.** In `_call_plugin` (`main.py:160-177`), a result
that is too large goes to `result.state`, and one that cannot be encoded becomes
`execution_error`. That would lose the verdict.
*Fix:*
- Catch `except Failed` before `except Exception`.
- Write `failed.json` first, then the result through `write_result` and `result.index`.
- A result that is too large still answers `failed`, with the summary marked truncated.
- A result that cannot be encoded: still `failed`, result `null`, plus a limits marker
  (recommended).
- Note that `call_plugin` (`testing.py:265`) never encodes `exc.result`, so a test using
  `pytest.raises` will not catch a result that cannot be encoded.

**S3. The plugins Q-2 names are in another repo.** `repo_tests.py` and `repo_gate.py` are in
`~/projects/ariadne/scripts/trestle/`, published from the operator's `[plugins] paths`. The
plugins in this repo that return `ok: false` are:
- `pytest_run.py:13` (its docstring says "never a raised error")
- `docker_stack.py:9`
- `migrate_apply.py:16`
- `github_pr_status.py:110`
- `github_pr_comments.py:83`
- `github_pr_open.py:83`, which is a refusal and may be `Blocked` rather than `Failed`

*Fix:* list each plugin with its verdict (`Failed`, `Blocked` or unchanged). Do the ariadne
plugins as a separate follow-up.

**S4. Re-running an edited plugin with the same key returns the old run.** `same_call` ignores
the plugin version (`idempotency.py:65-75`, "a republish never makes a conflict"), so the same key
joins the earlier run (`admission.py:450`).
*Fix:* `trestle-pack` step 4 says to use a new key for each published revision (for example,
include the source hash), or no key while iterating.

**S5. The restricted profile turns runtime publishing off** (`authoring.py:59-63,161`). The
skill's "write one, publish it" path needs a fallback: chain separate runs with `after=`/`match`.
The new `server_instructions` sentence belongs only in the branch where publishing is allowed.

**S6. The test plan misses places that pin the current skill files.**
- `tests/proof/k_doc_map.toml:118` and the globs in `fence.d/{core,single,cz}.toml`.
- `test_cl_c1_docs.py:83-84` pins `admission.budget_does_not_fit` and "recorded, not
  snapshotted".
- `test_agent_docs_mcp_only.py:88-92` pins `retry_after_s`, `held_by`, `view="status"`,
  `session_ended` and `plugin="exec"`.
- `test_k11` requires a `## Golden workflow` section.

*Fix:* map each pin to `trestle`, to `trestle-pack`, or to retired. Update the proof maps and
`README.md:96`.

**S7. Test 7 cannot run as written.** The harness in `test_tree_docs.py:166-205` knows only the
expectations `succeeded` and `blocked`, and it runs commands for real. A passing run needs ruff,
mypy and pytest on the child's PATH, plus a fixture repo with a `.py` file and a passing test
(mypy exits 2 with no files; pytest exits 5 when nothing is collected).
*Fix:*
- Add a `failed` expectation that also checks the code.
- Create the fixture repo.
- Test the path where lint fails in `test_plugin_authoring.py`, with a scripted `exec_handler`.

## Nits

- **N1.** A missing tool raises `FileNotFoundError` inside a pool thread, so the run ends
  `execution_error`. Catch it and raise `Blocked("tool_missing", ...)`, as `exec_with_blocked.py`
  does.
- **N2.** In `_step`:
  - A command that timed out or was cancelled gets the detail `exit None`.
  - `r.stderr or r.stdout` hides ruff's and pytest's findings (which go to stdout) whenever stderr
    has warnings. Use the tail of both.
  - If the run is cancelled, a `FakeContext` test gets back a passing verdict with two rows.
    Return early, or mark the skipped step.
- **N3.** Runtime publishes are last-write-wins (`registry.py:497-499`). Agents who copy `chain`
  as-is will overwrite each other. Name the plugin after its repo and objective.
- **N4.** The callback body example in `callbacks.md` doesn't match how runs end: it shows
  `state: "failed"` with `answer_outcome: "failed"`, but a plugin that raises answers
  `execution_error`. Use a `Failed` run as the example: `succeeded` / `passed` / `failed`.
- **N5.** A new chain takes two calls (publish, then run); later runs take one. Say so.
- **N6.** `failed.md` should warn that `trestle.workflow.Failed` is a dataclass. Raising it is a
  `TypeError`, which ends the run `execution_error`.

## Confirmed fine

- `chain_step` is not a reserved event kind (`child/context.py:46-62`).
- The `ExecResult` fields the sketch uses exist (`surface.py:44-68`).
- `answer_for_run` is the only place answers are computed, so recovery, the conductor and the
  view all pick up `read_plugin_failed` (`answer.py:823-836`).
- An interrupted run is decided by rule (2) before `_planless` is reached, so test 6 holds.
- `summary_fields` apply to any `result.json`, whatever the outcome (`project.py:669-680`).

## Verdict

The direction is right, and Q-1 to Q-5 can all still be done; Q-2 only needs its list of plugins
corrected (S3). Change 1 rests on two misreadings of the code: a detail field that does not exist
(B2) and a publication check that does not exist (S1). Its biggest hole is B3: run ordering,
waiting and the failure views all key off `state`, so a `Failed` test run would still release the
deploy chained behind it. Before implementation the design must specify outcome-aware `after`,
`first_failure` and `recent_failures`, and fix the sketch so it publishes (B1). Then it needs a
pass on the S-items: what happens when the result fails to record, idempotency on re-runs, the
restricted profile, and the map of doc-test pins and proof maps.
