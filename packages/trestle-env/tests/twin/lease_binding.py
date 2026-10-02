"""A binding seam whose `create` holds for a while (L.RB-6.4; WR-OWN-8): the fake engine of
`fake_binding`, in memory per run, with each container's creation taking `HOLD_S` of wall time.

Two runs of one environment are only told apart from two runs that happened not to meet when the
first is still mutating while the second is admitted; a fake that answers at once never is. The
hold widens every run's mutation interval (its first applied claim to its last release) so the
lease's exclusion is what keeps two intervals of one environment apart, and different
environments overlap (`TRESTLE_ENV_PORTS=twin.lease_binding:slow_ports`)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from trestle.workflow import ports

from twin import fake_binding

SEAM = f"{__name__}:slow_ports"
HOLD_S = 1.5


class SlowEngine(fake_binding.FakeReferenceEngine):
    def create(self, spec: Any, ticket: Any) -> Any:
        answer = super().create(spec, ticket)
        time.sleep(HOLD_S)
        return answer


def slow_ports(environ: Mapping[str, str]) -> Mapping[type, object]:
    engine = SlowEngine(None, fake_binding._checks(None))  # noqa: SLF001
    return {
        ports.ResourceReads: engine,
        ports.ResourceCreate: engine,
        ports.ResourceOwned: engine,
        ports.ResourceSafeStart: engine,
    }
