"""A port factory for `TRESTLE_ENV_PORTS`: the real binding with a supporting service that can
never become ready (L.RB-3.1; the failed and cancelled paths of the reuse proof).

The supporting container is created and runs, but its declared HTTP contract is replaced by one
its nginx never serves (`GET /absent` answers 404), so its readiness observation never passes while
everything else is the reference binding on the operator's real docker. Named by absolute path
(`/abs/planted_support.py:never_ready`) because the published plugin's child process cannot import
the test session's modules."""

from __future__ import annotations

from collections.abc import Mapping

from trestle.workflow import ports

from trestle_env import tree
from trestle_env.plugins._bind import reference_ports
from trestle_env.plugins._http import HttpReadinessReads

NEVER = tree.HttpReadiness(tree.HTTP_SUPPORT_READY, "/absent", 200, "ok")


def never_ready(environ: Mapping[str, str]) -> Mapping[type, object]:
    real = dict(reference_ports(environ, use_seam=False))
    inner = real[ports.ResourceReads]
    real[ports.ResourceReads] = HttpReadinessReads(
        getattr(inner, "_inner", inner), {tree.HTTP_SUPPORT_READY: NEVER}
    )
    return real
