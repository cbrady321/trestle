"""Published time bounds and ratios: the one product definition of each (DM-15, DM-46).

`tests/proof/tolerances.py` reads a bound from this module under its MC-09 name the moment it is
defined here; nothing else defines one (SA-05). Each bound is published by its first consumer.
"""

from __future__ import annotations

# Append cost independent of history (WR-EVID-4, published by CS-1): over 4 000 events, the
# last quartile's throughput divided by the first quartile's must stay at or above this
# fraction. S0 measured 1738 -> 464 events/s (0.27) and failed; a flat append stays near 1.0.
# A plan default disclosed for the maintainer (the requirement leaves the fraction "declared").
APPEND_COST_RATIO: float = 0.5
