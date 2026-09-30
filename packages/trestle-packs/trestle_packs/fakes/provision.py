"""Fake provisioning port (L.RB-6.1; B3-C1, B3-C3, B3-C4, B3-C21, DM-09).

Stdlib only, like its siblings. `FakeProvision` is `ResourceReads` + `ResourceCreate` over an
in-memory record store: the fake of `ProvisionPort` (a submit is `ResourceCreate.create` with
`PROVISIONED`, its probe `ResourceReads.observe`, the authoritative read). It is ruled like the
real adapter: a record's `key` is the run-scoped selector `trwr-<root run_id>-<path>` and creation
is idempotent per key; another root's record of the same system is a `found` record; a `DURABLE`
create is `Durable(ENVIRONMENT)` and a `RUN` create is refused before any change (no `RUN` form,
V-10.2); a store that is down is V-3.8's could-not-observe shape, never "absent".

Knobs: `lag` is the number of `check` reads (the convenience read) that stay unsatisfied after a
submit before the record shows, while `observe`, the authoritative probe, sees it at once, which is
what lets a node be shown never to resubmit on a lagging read (WR-ENV-4:completion-observed-
separately); `readable=False` is a store that does not answer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from trestle_packs.fakes.command import (
    ROUTE_UNSUPPORTED,
    Confirmation,
    ConfirmationStatus,
    ExecutionPolicy,
    Helpers,
    SelfProvisioning,
    _value,
    durable,
)
from trestle_packs.fakes.marker import (
    CheckResult,
    FoundRef,
    ResourceObservation,
    RouteRefused,
    SelectorRef,
)

DOCKER_ENGINE_UNREACHABLE = "adapter.docker_engine_unreachable"
RECORD_KIND = "postgres_record"
FOUND_KEEP = 4
_UNSAFE = re.compile(r"[^a-z0-9_.-]")


def selector_name(lineage: Any) -> str:
    """`trwr-<root run_id>-<path>` (MC-B-01), the key of the record a lineage submits."""
    path = _UNSAFE.sub("_", ".".join(lineage.path.segments).lower())
    return f"trwr-{lineage.root_run_id}-{path}"


class FakeProvision:
    """A scripted record store behind the provisioning port's members."""

    def __init__(self, *, lag: int = 0, readable: bool = True) -> None:
        self.lag = lag
        self.readable = readable
        self.records: dict[str, tuple[str, str]] = {}  # key -> (system, payload)
        self.submits = 0  # writes that reached the store (an idempotent repeat is not one)
        self._pending: dict[str, int] = {}  # key -> `check` reads still to be unsatisfied

    # ------------------------------------------------------------------ test hooks

    def inventory(self) -> Mapping[str, frozenset[str]]:
        """What the read-facet watcher looks at around a read (B3-C17 item 3)."""
        return {"records": frozenset(self.records)}

    def plant_found(self, system: str) -> str:
        """A record of `system` under a key this root's selector does not name."""
        key = f"trwr-r_other_root-planted-{len(self.records)}"
        self.records[key] = (system, "planted")
        return key

    def close(self) -> None:
        return None

    # ------------------------------------------------------------------ ResourceReads

    def observe(self, spec: Any, lineage: Any, effect: str | None) -> ResourceObservation:
        if not self.readable:
            return ResourceObservation(False, None, False, False, (), (), DOCKER_ENGINE_UNREACHABLE)
        selector = "" if effect is None else selector_name(lineage)
        present = selector in self.records and self.records[selector][0] == spec.logical_system
        others = sorted(
            key
            for key, (system, _) in self.records.items()
            if system == spec.logical_system and key != selector
        )
        now = datetime.now(UTC)
        found = tuple(FoundRef(RECORD_KIND, key, now) for key in others[:FOUND_KEEP])
        ref = SelectorRef(lineage, effect or "", selector, now) if present and effect else None
        return ResourceObservation(present, ref, present, present, (), found, None)

    def check(self, check: str, target: Any) -> CheckResult:
        if not self.readable:
            return CheckResult(
                False, DOCKER_ENGINE_UNREACHABLE, "the record store could not be read"
            )
        selector = getattr(target, "selector", "")
        if check != "recorded" or selector not in self.records:
            return CheckResult(False, None, "the record is not in the store")
        if self._pending.get(selector, 0) > 0:  # the convenience read lags the submit
            self._pending[selector] -= 1
            return CheckResult(False, None, "the record is not in the store yet")
        return CheckResult(True, None, "the record is in the store")

    def endpoint(self, target: Any, vantage: Any) -> RouteRefused:
        return RouteRefused(
            ROUTE_UNSUPPORTED, "A provisioned record has no address to route to: use the probe."
        )

    # ------------------------------------------------------------------ ResourceCreate

    def launch_policy(self, spec: Any) -> ExecutionPolicy:
        return ExecutionPolicy(SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.PREVENTED, None)

    def release_descriptor(self, call: Any) -> dict[str, Any]:
        """`Durable(ENVIRONMENT)` for `DURABLE`; a `RUN` call has no form and is refused."""
        if _value(call.lifetime) != "durable":
            raise ValueError("a provisioned record has no RUN form: it is created DURABLE (V-10.2)")
        return durable("environment")

    def create(self, spec: Any, ticket: Any) -> Confirmation:
        if _value(ticket.lifetime) != "durable":
            raise ValueError("a provisioned record has no RUN form: it is created DURABLE (V-10.2)")
        if not self.readable:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, DOCKER_ENGINE_UNREACHABLE, None)
        key = selector_name(ticket.lineage)
        if key not in self.records:  # idempotent per key: a repeat changes nothing
            self.records[key] = (spec.logical_system, "fixture-record")
            self._pending[key] = self.lag
            self.submits += 1
        return Confirmation(ConfirmationStatus.APPLIED, None, key)
