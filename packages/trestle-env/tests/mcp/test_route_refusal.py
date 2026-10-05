"""L.RB-8.2: a container consumer of a local override is refused before a run id (WR-ENV-2, V-7.3,
B2-C2 (3)).

Through the control surface a kernel serves over MCP, the `local_override` test plugin is published
from its own directory: the reference Postgres child consumes the supporting service from a
CONTAINER, and the request selects the supporting service's agent-launched override, which declares
the host vantage only. No eligible alternative is reachable from the consumer, so admission refuses
`ROUTE_UNSUPPORTED` naming the dependent. Nothing is minted (no run id, no run directory, no
idempotency record, no process) and no port map is ever built: the refusal precedes startup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from trestle.common import codes
from trestle.common.types import RequestOutcome
from trestle.server.idempotency import IdempotencyStore
from trestle.server.main import Kernel, create_kernel
from trestle.workflow.declarations import Vantage

from trestle_env import schema
from trestle_env.plugins import _bind
from trestle_env.realization import DOCKER_REACH, LOCAL_REACH

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "local_override.py"
KEY = "rb-8-2-route-refusal-key"
PORT_LOG = "port_calls.log"


def logged_ports(environ: dict[str, str]) -> dict[type, object]:
    """A port factory that records that a port map was asked for (it must never be)."""
    with open(environ["TRESTLE_TEST_PORT_LOG"], "a", encoding="utf-8") as log:
        log.write("ports requested\n")
    return {}


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("TRESTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRESTLE_TEST_PORT_LOG", str(tmp_path / PORT_LOG))
    monkeypatch.setenv(_bind.PORTS_ENV, f"{__name__}:logged_ports")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / FIXTURE.name).write_bytes(FIXTURE.read_bytes())
    built = create_kernel(home=tmp_path / "home", plugin_dirs=[plugins], skip_recovery=True)
    built.registry.refresh()
    return built


def refused(kernel: Kernel, args: dict[str, Any]) -> RequestOutcome:
    outcome = kernel.control.run(plugin="local_override", args=args, idempotency_key=KEY)
    assert isinstance(outcome, RequestOutcome), outcome
    assert outcome.origin == "admission" and outcome.retryable is False
    assert "run_id" not in outcome.to_dict()
    runs = kernel.home / "runs"
    assert not runs.exists() or not [p for p in runs.glob("*/*") if p.is_dir()]
    assert IdempotencyStore.open(kernel.home).lookup(KEY) is None
    return outcome


@pytest.mark.proves(
    "WR-ENV-2", "WR-ENV-2:unsupported-route-refused-before-startup", "B", "B", "MCP", "CI"
)
def test_container_to_local_route_refused_no_run_id(kernel: Kernel, tmp_path: Path) -> None:
    # the declared data the refusal reads: a local process is reachable from the host only
    assert Vantage.CONTAINER in DOCKER_REACH and Vantage.CONTAINER not in LOCAL_REACH
    outcome = refused(
        kernel, {schema.ENV_ARG: "route-env", schema.OVERRIDES_ARG: ["http_support_local"]}
    )
    assert outcome.code == codes.ROUTE_UNSUPPORTED
    assert "postgres" in outcome.message  # the dependent whose vantage nothing serves
    assert not (tmp_path / PORT_LOG).exists()  # no port map was ever asked for
