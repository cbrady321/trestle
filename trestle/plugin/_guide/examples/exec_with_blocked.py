"""Run a command with `ctx.exec`, and raise `Blocked` when a person must act first.

`run(plugin="exec_with_blocked", args={"tool": "git"})` answers with the tool's version; a tool
that is not installed ends the run `succeeded` with `outcome: "blocked"` and the human action."""

from __future__ import annotations

from trestle.plugin import Blocked, Context, trestle


@trestle(deadline=120)
def exec_with_blocked(ctx: Context, tool: str = "git") -> dict[str, str]:
    try:
        result = ctx.exec([tool, "--version"], timeout=30, output="version.log")
    except FileNotFoundError:
        raise Blocked(
            "tool_missing",
            f"Install `{tool}` where the Trestle service can find it, then resend.",
        ) from None
    if result.timed_out or result.cancelled:
        raise RuntimeError(f"{tool} --version did not finish")
    if not result.ok:
        raise RuntimeError(f"{tool} --version exited {result.returncode}: {result.stderr[-300:]}")
    return {"tool": tool, "version": result.stdout.strip()}
