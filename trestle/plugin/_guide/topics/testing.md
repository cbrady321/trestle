# Testing without a server: `trestle.plugin.testing`

```python
from trestle.plugin.testing import FakeContext, call_plugin, exec_result


def test_check_pr(tmp_path):
    def gh(argv, **kwargs):
        return exec_result(argv, stdout="MERGED\n")

    ctx = FakeContext(tmp_path, exec_handler=gh)
    assert call_plugin(check_pr, {"pr": 7}, ctx=ctx) == {"state": "MERGED"}
    assert ctx.exec_calls[0].argv == ("gh", "pr", "view", "7")
```

- `FakeContext(root, deadline=300, exec_handler=None, credentials=None)` implements the whole
  context and records `logs`, `progress_updates`, `events`, `attachments`, `artifacts` and
  `exec_calls`. It refuses what a run refuses. `exec` runs the command for real unless
  `exec_handler` returns a scripted `exec_result(...)`. Set `ctx.cancelled = True` to simulate a
  stop. `credentials={"gh": {"GH_TOKEN": "fake"}}` gives `credential_env` and `credential_file`
  fake values.
- `call_plugin(fn, args, ctx=...)` calls the function as a run does: JSON arguments are hydrated
  by the annotations, and the result comes back as the JSON the run would record.
- For pytest, add `pytest_plugins = ["trestle.plugin.testing"]` to a top-level `conftest.py`
  to get the `trestle_ctx` fixture.
- A published plugin may not import this module.
