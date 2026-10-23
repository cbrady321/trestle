"""One-off commands: `run(plugin="exec", args={"argv": [...], "cwd": "<absolute dir>"})`.

No shell is added (no pipes, globs or `&&`). `cwd` must resolve under a directory the operator
lists in TRESTLE_EXEC_ROOTS (absolute paths joined by the path separator); until one is listed the
run answers `blocked`. The run's deadline is the command's timeout. Errors are returned in `error`,
never raised, so read `summary.ok`; `output` is the whole kept output, to fetch with `tail`."""

from __future__ import annotations

import os
from typing import TypedDict

from trestle.plugin import Blocked, Context, trestle

ROOTS_ENV = "TRESTLE_EXEC_ROOTS"
RETURN_MARGIN_S = 5.0  # left to return the answer after a timeout, before the deadline kill
MAX_OUTPUT_BYTES = 16 * 1024 * 1024  # kept per stream (the first bytes)
TAIL_BYTES = 1500
DETAIL_CHARS = 300


class ExecAnswer(TypedDict):
    ok: bool
    exit_code: int | None
    timed_out: bool
    duration_s: float
    stdout_tail: str
    stderr_tail: str
    truncated: bool
    output: str
    error: str | None


def tail(text: str, limit: int = TAIL_BYTES) -> str:
    """The last `limit` bytes of `text`, cut on a UTF-8 boundary and, when the cut falls inside a
    line that is not the last, from the next line."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    kept = raw[-limit:]
    cut = kept.decode("utf-8", errors="ignore")
    if raw[-limit - 1 : -limit] != b"\n" and "\n" in cut[:-1]:
        cut = cut[cut.index("\n") + 1 :]
    return cut


def _error(code: str, detail: str) -> ExecAnswer:
    """An answer for a command that did not run: `error` is `<code>: <detail>`."""
    return {
        "ok": False,
        "exit_code": None,
        "timed_out": False,
        "duration_s": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
        "truncated": False,
        "output": "",
        "error": f"{code}: {detail[:DETAIL_CHARS]}",
    }


def _roots() -> list[str]:
    raw = os.environ.get(ROOTS_ENV, "")
    roots = [r for r in raw.split(os.pathsep) if r]
    if not roots or not all(os.path.isabs(r) for r in roots):
        raise Blocked(
            "exec_roots_unset",
            f"The operator lists the directories exec may run in: {ROOTS_ENV} under "
            "[plugins.env] in config.toml, absolute paths joined by ':'.",
        )
    return [os.path.realpath(r) for r in roots]


@trestle(summary_fields=["ok", "exit_code", "timed_out", "error", "stderr_tail", "stdout_tail"])
def exec(ctx: Context, argv: list[str], cwd: str) -> ExecAnswer:
    if argv and os.path.basename(str(argv[0])) == "trestle":
        return _error(
            "operator_command",
            "trestle is the operator's CLI; report the command to the operator instead",
        )
    if not os.path.isabs(cwd):
        return _error("cwd_not_absolute", cwd)
    roots = _roots()
    real = os.path.realpath(cwd)
    if not os.path.isdir(real):
        return _error("cwd_not_a_directory", cwd)
    if not any(os.path.commonpath([real, root]) == root for root in roots):
        return _error("cwd_outside_roots", f"roots: {', '.join(roots)}; cwd resolves to {real}")
    timeout = ctx.remaining() - RETURN_MARGIN_S
    if timeout <= 0:
        return _error("no_time_left", f"{ctx.remaining():.1f}s left; send a longer deadline_s")
    try:
        result = ctx.exec(argv, cwd=real, timeout=timeout, max_output_bytes=MAX_OUTPUT_BYTES)
    except FileNotFoundError as exc:
        return _error(
            "program_not_found",
            f"the service's PATH is not your shell's; name the program by its absolute path "
            f"({exc})",
        )
    except PermissionError as exc:
        return _error("not_executable", str(exc))
    except TypeError as exc:
        return _error("argv_invalid", str(exc))
    return {
        "ok": result.ok,
        "exit_code": result.returncode,
        "timed_out": result.timed_out,
        "duration_s": result.duration_s,
        "stdout_tail": tail(result.stdout),
        "stderr_tail": tail(result.stderr),
        "truncated": result.truncated,
        "output": result.artifact,
        "error": None,
    }
