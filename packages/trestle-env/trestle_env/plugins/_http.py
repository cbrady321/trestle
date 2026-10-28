"""The read facet that makes an HTTP readiness contract observable (L.RB-2.1; KDD 2, WR-VERIFY-2).

`HttpReadinessReads` wraps a `ResourceReads` implementation (the container adapter, or the local
process adapter) and answers the HTTP checks the tree declares (`trestle_env.tree.HttpReadiness`):

* the resource must be alive first (the wrapped port's own check: `running` for a container,
  `ready` for a local process), so a check never reads a leftover listener;
* the endpoint is the wrapped port's `HOST`-vantage endpoint of the target, and only loopback is
  ever requested (a port that answered another address would be reaching outside the machine);
* the contract holds when a GET of the declared path answers the declared status with EXACTLY the
  declared body. A refused connection, a timeout, another status or another body is "not ready
  yet", never a code: the readiness observation is the answer, not the connection.

Everything else (`observe`, `endpoint`, every other check) is the wrapped port's. Only the
composition root builds this; the tree's units know a check id, never a socket.
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Final

from trestle.workflow.declarations import CheckRef, Vantage
from trestle.workflow.ports import Endpoint, ResourceReads
from trestle.workflow.values import CheckResult, CreatedHandle, FoundRef, OwnedHandle, SelectorRef

from trestle_env.tree import HttpReadiness

REQUEST_TIMEOUT_S: Final = 2.0  # executor-chosen: one request never outlives a poll's patience
LOOPBACK: Final = "127.0.0.1"
DETAIL_MAX: Final = 200

type Target = CreatedHandle | OwnedHandle | FoundRef | SelectorRef


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is another answer, not the declared one: it is never followed."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class HttpReadinessReads:
    """`ResourceReads` with the tree's HTTP readiness checks added to the wrapped port's."""

    def __init__(
        self,
        inner: ResourceReads,
        contracts: Mapping[CheckRef, HttpReadiness],
        *,
        alive: str = "running",
        timeout_s: float = REQUEST_TIMEOUT_S,
    ) -> None:
        self._inner = inner
        self._contracts = dict(contracts)
        self._alive = alive
        self._timeout_s = timeout_s
        self._opener = urllib.request.build_opener(_NoRedirect)

    def observe(self, spec, lineage, effect):  # type: ignore[no-untyped-def]
        return self._inner.observe(spec, lineage, effect)

    def endpoint(self, target, vantage):  # type: ignore[no-untyped-def]
        return self._inner.endpoint(target, vantage)

    def check(self, check: CheckRef, target: Target) -> CheckResult:
        contract = self._contracts.get(check)
        if contract is None:
            return self._inner.check(check, target)
        alive = self._inner.check(self._alive, target)
        if not alive.satisfied:
            return alive  # not running (or the engine could not be read: its code passes through)
        where = self._inner.endpoint(target, Vantage.HOST)
        if not isinstance(where, Endpoint):
            return CheckResult(False, None, "no route to the resource from the host yet")
        if where.host != LOOPBACK:
            return CheckResult(False, None, f"{where.host} is not loopback: never requested")
        return self._get(contract, where.port)

    def _get(self, contract: HttpReadiness, port: int) -> CheckResult:
        url = f"http://{LOOPBACK}:{port}{contract.path}"
        try:
            with self._opener.open(url, timeout=self._timeout_s) as response:
                status = response.status
                body = response.read(len(contract.body.encode()) + 1)
        except urllib.error.HTTPError as answer:
            status, body = answer.code, b""
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            return CheckResult(False, None, f"GET {contract.path}: no answer yet")
        if status != contract.status:
            return CheckResult(False, None, f"GET {contract.path} answered {status}")
        if body != contract.body.encode():
            return CheckResult(False, None, f"GET {contract.path} answered another body")
        return CheckResult(True, None, f"GET {contract.path} answered as declared")
