"""The environment lease's key (WR-OWN-8, L.SL-8.1).

The environment key is the canonical JSON of the request argument the plugin names in `env_arg`
(opaque to the host: two requests share an environment exactly when the bytes are equal);
admission records it in the `created` row (`lease_key`), in the plan's `lease_set` and in the
run's live marker. There is no lease store file. v0.3.1 (Problem A, rule 6): one running run per key
home-wide, in arrival order, checked in `home/sched.json` with the grant (`trestle.server.pool`);
the busy pre-check reads the key's queued and running runs there. The in-memory holder index
(`Holders`, `rebuild_holders`, `holder_of`) is retired.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trestle.common.plan.declared import canonical_json
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path

LEASE_KEY_FIELD = "lease_key"


def request_key(env_arg: str | None, args: Mapping[str, Any]) -> str | None:
    """The environment key of a request: the canonical JSON of the argument named by the plugin's
    `env_arg`, or None when the plugin declares no environment or the request gives no value for
    it (nothing is then held)."""
    if env_arg is None:
        return None
    value = args.get(env_arg)
    if value is None:
        return None
    return canonical_json(value)


def deadline_epoch(run_dir: Path) -> float | None:
    """The run's admitted deadline, or None when unreadable: the one its `released` row minted
    when it was held (Feature 3), else its spec's (`evidence/spec.json`)."""
    try:
        minted = RunLedger.open(ledger_path(run_dir)).released_deadline()
        spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
        fixed = datetime.fromisoformat(minted if minted is not None else str(spec["deadline"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if fixed.tzinfo is None:
        fixed = fixed.replace(tzinfo=UTC)
    return fixed.timestamp()


def leaves_too_little(
    free_at: float, would_be_deadline: float, worst_case_s: float, release_slice_s: float
) -> bool:
    """B2 ordering step 2 (B2-C5): a request that would wait for the environment until `free_at`
    (the holders' latest recorded deadline) and would then have less than the plan's worst case
    plus its release slice before its own deadline is refused busy instead of queued. Epoch
    seconds throughout."""
    return would_be_deadline - free_at < worst_case_s + release_slice_s
