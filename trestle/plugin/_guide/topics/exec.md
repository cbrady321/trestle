# Running a command: `ctx.exec`

Use `ctx.exec` instead of `subprocess`:

```python
result = ctx.exec(["gh", "pr", "view", "123", "--json", "state"], timeout=60)
if result.timed_out or result.cancelled:
    ...
elif not result.ok:
    ctx.log(f"gh failed: {result.stderr[-500:]}")
```

- **argv only:** a non-empty list of strings. A single string raises `TypeError`, and no shell
  ever runs. A missing program raises `FileNotFoundError`.
- **Time limit:** `timeout` defaults to `ctx.remaining()` and is never longer. When it passes,
  or the run stops, the command's whole process group is killed. The result then has
  `timed_out` or `cancelled` set and `returncode=None`. A run with no time left does not start
  the command.
- **Environment:** the plugin's own environment with `env` on top. A `None` value removes a
  variable. `cwd` and `input` work as in `subprocess`. Stdin is otherwise empty: nothing can
  answer a prompt.
- **Output:** `stdout` and `stderr` keep at most `max_output_bytes` (1 MiB) each. `truncated`
  and `output_bytes` say how much was dropped. The kept output is attached under `output`
  (default `exec.log`) and its handle is `result.artifact`. Pass `output=None` to keep none.
- `result.ok` is true only for a command that ran to its end and exited 0.
- `credentials=` is reserved and raises `NotImplementedError`.

Subprocesses started some other way still run inside the run's stop scope. A process that
double-forks out of it is not stopped, so do not start daemons.
