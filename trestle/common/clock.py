"""Published time bounds and ratios: the one product definition of each (DM-15, DM-46).

`tests/proof/tolerances.py` reads a bound from this module under its MC-09 name the moment it is
defined here; nothing else defines one (SA-05). Each bound is published by its first consumer.
"""

from __future__ import annotations

import os

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

# The cooperative release slice is published by SV-3 (B2-C10); until then it is zero, so the stop
# bound is the grace plus the kill wait, measured from the moment the supervisor decides to stop.
_RELEASE_SLICE_S: float = 0.0
stop_bound: float = _RELEASE_SLICE_S + grace + kill

# The supervisor's liveness-poll interval, published beside the bound (B2-C10, CB-9): a cancel
# request is seen within one interval, so the bound from the request is `stop_bound` plus this.
# It is the interval the conductor has always polled at.
poll_interval: float = 0.05

# The finalization margin (B2 `OperatorLimits` name, MC-09, published by CS-4): how long after the
# admitted deadline a call may still be answered. A deadline stop of a plugin that ignores SIGTERM
# takes `grace` + `kill` (B2-C2 (5) in its plan-less form: `grace + kill <= finalization_margin`;
# SV-3 extends the check to the release targets), and the finalization writes after the stop need
# room of their own, so the default is the stop bound plus this reserve (10 s: a plan default
# disclosed for the maintainer, not a requirement; A-1 publishes the reserve under its own name).
_FINALIZATION_WRITES_S: float = 10.0
finalization_margin: float = float(
    os.environ.get("TRESTLE_FINALIZATION_MARGIN_S", str(stop_bound + _FINALIZATION_WRITES_S))
)
