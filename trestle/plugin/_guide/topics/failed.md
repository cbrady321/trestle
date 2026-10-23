# When the check fails: `Failed`

```python
from trestle.plugin import Failed

raise Failed("tests_failed", "3 of 412 tests failed in the api suite", result=report)
```

The run ends `succeeded` with `outcome: "failed"`. `answer.primary` carries
`code: "plugin.tests_failed"`; the detail is kept in the run's record, not in the answer, so put
what the agent needs in `result=` (a table of steps, the failing tests). It is recorded exactly
as a return would be, `summary_fields` included. A code is lower-case words joined by `_` or `.`.

A run that ends `failed` (or `blocked`) does not pass: a run held behind it with
`after={..., "when": "succeeded"}` is not started, `await_runs(mode="first_failure")` wakes on it,
and `query(view="recent_failures")` lists it. A normal return always answers `passed`, so
`ok: false` in a returned result is not a failure. In a test, `pytest.raises(Failed)` around
`call_plugin` sees `code`, `detail` and `result` (the result is not encoded there).

`trestle.workflow.Failed` is a different thing: a value a workflow unit returns. Raising it is a
`TypeError`, and the run ends `execution_error`. See `trestle://guide/examples/chain`.
