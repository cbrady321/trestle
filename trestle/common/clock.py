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
