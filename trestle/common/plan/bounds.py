"""The V-13 bounds A-1 uses: the one definition of each (L.SV-1.1, SA-05, SA-14).

A pure stdlib module, so `trestle.workflow` reads the bounds without importing `clock.py` or
any host module (C.5 step 4). Every magnitude is V-13's provisional, design-owned value. A byte
bound on a text field is measured on the UTF-8 bytes of the value's JSON string encoding, escapes
included and the enclosing quotes excluded (`text_bytes`); an entry bound (`LANE_ENTRY_MAX`) on
the lane codec's encoded entry.
"""

from __future__ import annotations

import json

CODE_MAX: int = 64  # every StableCode, printable ASCII
NAME_MAX: int = 128  # UnitRef, EffectId, a path segment, a logical name
PATH_MAX: int = 512  # the canonical encoded NodePath
TOKEN_MAX: int = 256  # selectors, digests, generations, fingerprints
EXEC_PATH_MAX: int = 1024  # ArgvRelease.executable (V-13; not in the plan's SV-1.1 name list)
HUMAN_ACTION_MAX: int = 1024  # every HumanAction
TEXT_MAX: int = 512  # BoundedText
ARGV_RELEASE_MAX: int = 4096  # an ArgvRelease encoded: executable + the three argvs
LANE_ENTRY_MAX: int = 8 * 1024  # every written lane entry except the plan entry
LANE_BASE_ENTRIES: int = 4096  # LANE_ENTRIES = LANE_BASE_ENTRIES + |selected_scope| + 2
VERTEX_MAX: int = 1024  # PlanAccepted.selected_scope (AM-6: one definition, here)
OBSERVE_STDOUT_MAX: int = 4096  # stdout the sweep reads from `observe_argv` (V-10.4)


def text_bytes(value: str) -> int:
    """The bytes `value` takes as a JSON string on the lane: its UTF-8 bytes with escapes
    included, the enclosing quotes excluded (V-13 'What a byte bound measures')."""
    return len(json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"))
