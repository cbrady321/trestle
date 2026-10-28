"""A long job that reports as it goes: one status artifact, re-attached each round.

Re-attaching under the same name replaces the listed artifact and takes no new slot, so a job
that rewrites `status.md` every round holds one. `stop_grace` gives it time to write a last status
after a cancel or near its deadline; `summary_fields` keeps `rounds` in the summary as a table."""

from __future__ import annotations

from typing import TypedDict

from trestle.plugin import Context, trestle


class Round(TypedDict):
    round: int
    items: int


class Status(TypedDict):
    rounds: list[Round]
    stopped: bool


@trestle(deadline=600, stop_grace=5, summary_fields=["rounds[round,items]", "stopped"])
def artifact_status(ctx: Context, rounds: int = 3, items_per_round: int = 100) -> Status:
    status = ctx.tmp / "status.md"
    done: list[Round] = []
    stopped = False
    for number in range(1, rounds + 1):
        if ctx.cancelled or ctx.remaining() < 10:
            stopped = True
            break
        items = sum(1 for _ in range(items_per_round))  # one round of real work goes here
        done.append({"round": number, "items": items})
        status.write_text(f"# Status\n\nround {number} of {rounds}: {items} items\n")
        ctx.attach(status, name="status.md")
        ctx.progress(f"round {number} of {rounds}", fraction=number / rounds)
    if stopped:
        status.write_text(f"# Status\n\nstopped after {len(done)} of {rounds} rounds\n")
        ctx.attach(status, name="status.md")
    return {"rounds": done, "stopped": stopped}
