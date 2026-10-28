"""Published time bounds and ratios: the one product definition of each (DM-15, DM-46).

`tests/proof/tolerances.py` reads a bound from this module under its MC-09 name the moment it is
defined here; nothing else defines one (SA-05). Each bound is published by its first consumer.
"""

from __future__ import annotations

import os

# The V-13 lane bounds A-1 publishes through this module (L.SV-1.1): defined once, in the
# stdlib-pure `trestle.common.plan.bounds` (so `trestle.workflow` reads them without importing this
# module); re-exported here by import, never by a second assignment, so SA-05's one-definition
# check holds.
from trestle.common.plan.bounds import LANE_BASE_ENTRIES as LANE_BASE_ENTRIES
from trestle.common.plan.bounds import LANE_ENTRY_MAX as LANE_ENTRY_MAX

# Append cost independent of history (WR-EVID-4, published by CS-1): over 4 000 events, the
# last quartile's throughput divided by the first quartile's must stay at or above this
# fraction. S0 measured 1738 -> 464 events/s (0.27) and failed; a flat append stays near 1.0.
# A plan default disclosed for the maintainer (the requirement leaves the fraction "declared").
APPEND_COST_RATIO: float = 0.5

# The one stop (B2-C10, published by CS-2 under B2's OperatorLimits names, MC-09). A stop sends
# SIGTERM to every process attributable to the run, waits at most `grace`, sends SIGKILL, and
# waits at most `kill` for the group to be confirmed gone. The values are today's request-path
# defaults (10 s and 5 s); the env names are the ones the operator already sets.
grace: float = float(os.environ.get("TRESTLE_CANCEL_GRACE_S", "10"))
kill: float = float(os.environ.get("TRESTLE_CANCEL_KILL_S", "5"))

# The cooperative release slice (B2-C10, published by L.SV-3.4 under B2's
# `OperatorLimits.release_slice` name, MC-09): the longest a root that declares a release walk
# may wait between the stop row and the kill for its own cooperative release. Each root's own
# slice (`PlanAccepted.release_slice`) is fixed at admission and never exceeds this; a root with
# no release walk gets 0 (B2-C1).
# A plan default disclosed for the maintainer (C.9, DM-46), overridable for a deployment.
release_slice: float = float(os.environ.get("TRESTLE_RELEASE_SLICE_S", "10"))

# The stop bound, defined once (B2-C10, SA-05): from U2's stop row, `release_slice + grace + kill`.
stop_bound: float = release_slice + grace + kill

# The reserve each parent holds back for its own finalization work (B2-C5 carve) and the room the
# finalization writes need after the stop (published by L.SV-3.4; a plan default disclosed for the
# maintainer, DM-46): the finalization margin's default is the stop bound plus this.
FINALIZATION_RESERVE_S: float = float(os.environ.get("TRESTLE_FINALIZATION_RESERVE_S", "10"))

# The most release targets the sweep works on at once, and only within one release rank (B2
# `OperatorLimits.sweep_parallelism`, V-4.4; published by L.SV-3.4 because B2-C2 (5) needs it, and
# read by the sweep, L.SV-3.7). The plan gives no value; four is an executor-chosen plan default
# disclosed for the maintainer (AM-15).
sweep_parallelism: int = int(os.environ.get("TRESTLE_SWEEP_PARALLELISM", "4"))

# The supervisor's liveness-poll interval, published beside the bound (B2-C10, CB-9): a cancel
# request is seen within one interval, so the bound from the request is `stop_bound` plus this.
# It is the interval the conductor has always polled at.
poll_interval: float = 0.05

# How often a waiter (await_runs, run(completion="terminal")) re-reads its runs (v0.3.1 Problem C):
# each poll reads one small state.json per run. The supervision poll above is unchanged.
await_poll_interval: float = 0.25

# The finalization margin (B2 `OperatorLimits` name, MC-09, published by CS-4): how long after the
# admitted deadline a call may still be answered. A deadline stop of a plugin that ignores SIGTERM
# takes `grace` + `kill`, and the sweep of a root's release targets takes the rest of B2-C2 (5)'s
# sum; the finalization writes after the stop need room of their own. The default is the stop
# bound plus the reserve above, which covers B2-C2 (5) for every A-1 fixture root at the published
# defaults (SA-05, test_sa05_operator_limits); a root whose own sum exceeds it is refused at
# admission (L.SV-3.5).
finalization_margin: float = float(
    os.environ.get("TRESTLE_FINALIZATION_MARGIN_S", str(stop_bound + FINALIZATION_RESERVE_S))
)

# The longest deadline a call may be admitted with (B2 `OperatorLimits` name, MC-09; published by
# L.CL-C1.5, DM-46). A plugin that declares a longer deadline is refused at admission with
# `admission.budget_does_not_fit`, before any run id (B2-C2 (4)). One hour: a plan default
# disclosed for the maintainer, not a requirement (an operator override belongs to the phase that
# owns `OperatorLimits`, MC-B2-04, so this is a constant and not a config field).
deadline_ceiling: float = 3600.0
