"""A chain: lint and type-check side by side, then the tests, judged as one verdict.

One run, one answer. Each step is recorded; a failed step stops the steps after it. Publish it
under a name of your own (the repo and the objective): runtime publishes are last-write-wins.
`run(plugin="<your name>", args={"repo": "<absolute dir>"}, wait_ms=1000, completion="terminal")`
answers `passed` with three steps, or `failed` with the code of the first step that failed."""

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
        action = f"Install `{argv[0]}` where the service can run it, then resend."
        raise Blocked("tool_missing", action) from None
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
