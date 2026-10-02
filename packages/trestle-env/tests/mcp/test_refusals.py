"""A request naming an identifier outside the catalog is refused before a run id (L.RB-1.1).

Through the control surface a kernel serves over MCP: the reference plugin is published from its
own directory and called with an unknown or duplicate identifier. Nothing is minted (no run id, no
run directory, no idempotency record, no process) and no port is ever built, so no effect call
can have been made (WR-AUTH-3: the zero-effect half is admission's, B2-C2 (1))."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from trestle.common import codes
from trestle.common.types import RequestOutcome
from trestle.server.idempotency import IdempotencyStore
from trestle.server.main import Kernel, create_kernel

from trestle_env import schema, tree
from trestle_env.plugins import _bind, reference_env

PLUGINS = Path(reference_env.__file__).resolve().parent
KEY = "rb-1-1-refusal-key"
ENV = "refusal-env"

PORT_LOG = "port_calls.log"


def logged_ports(environ: dict[str, str]) -> dict[type, object]:
    """A port factory that records that a port map was asked for (it is never called here)."""
    with open(environ["TRESTLE_TEST_PORT_LOG"], "a", encoding="utf-8") as log:
        log.write("ports requested\n")
    return {}


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("TRESTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRESTLE_TEST_PORT_LOG", str(tmp_path / PORT_LOG))
    monkeypatch.setenv(_bind.PORTS_ENV, f"{__name__}:logged_ports")
    built = create_kernel(home=tmp_path / "home", plugin_dirs=[PLUGINS], skip_recovery=True)
    built.registry.refresh()
    return built


def refused(kernel: Kernel, args: dict[str, Any]) -> RequestOutcome:
    outcome = kernel.control.run(plugin="reference_env", args=args, idempotency_key=KEY)
    assert isinstance(outcome, RequestOutcome), outcome
    assert outcome.origin == "admission" and outcome.retryable is False
    assert "run_id" not in outcome.to_dict()
    runs = kernel.home / "runs"
    assert not runs.exists() or not [p for p in runs.glob("*/*") if p.is_dir()]
    assert IdempotencyStore.open(kernel.home).lookup(KEY) is None
    return outcome


CASES = {
    "service": ({schema.SERVICES_ARG: ["mongo"]}, "mongo", tree.SERVICES_SET),
    "test": ({schema.TESTS_ARG: ["smoke"]}, "smoke", tree.TESTS_SET),
    "override": ({schema.OVERRIDES_ARG: ["local-api"]}, "local-api", tree.OVERRIDES_SET),
}


@pytest.mark.proves("WR-AUTH-3", "WR-AUTH-3:unknown-id-zero-effects", "B", "B", "MCP", "CI")
@pytest.mark.proves("WR-PLAN-2", "WR-PLAN-2:b-domain-identifier-refused", "B", "B", "MCP", "CI")
@pytest.mark.proves("WR-AUTH-3", "B2.2", "B", "B", "MCP", "CI")
@pytest.mark.parametrize("kind", ["service", "duplicate", "test", "override"])
def test_unknown_identifier_refused_no_run_id_zero_effects(
    kernel: Kernel, tmp_path: Path, kind: str
) -> None:
    if kind == "duplicate":
        outcome = refused(kernel, {schema.ENV_ARG: ENV, schema.SERVICES_ARG: ["postgres"] * 2})
        # the schema refuses a repeated identifier (`uniqueItems`): an argument outside the
        # published schema, named by its field (today's `admission.invalid_args`)
        assert outcome.code == codes.INVALID_ARGS
        assert f"invalid {schema.SERVICES_ARG}" in outcome.message
    else:
        given, identifier, where = CASES[kind]
        outcome = refused(kernel, {schema.ENV_ARG: ENV, **given})
        assert outcome.code == codes.UNKNOWN_IDENTIFIER
        assert f"({identifier})" in outcome.message  # the identifier is named
        assert f"identifier_sets.{where}" in outcome.message  # and where the valid ones are listed
    # zero effects: no port map was ever asked for (a run would have built one)
    assert not (tmp_path / PORT_LOG).exists()
    assert os.environ[_bind.PORTS_ENV].endswith(":logged_ports")


def test_a_known_service_is_not_refused_by_the_identifier_check(kernel: Kernel) -> None:
    from trestle.common.types import AdmitRequest, AdmitResultAdmitted

    request = AdmitRequest(
        plugin="reference_env", args={schema.ENV_ARG: ENV, schema.SERVICES_ARG: ["postgres"]}
    )
    result = kernel.control.admission.admit(request)
    assert isinstance(result, AdmitResultAdmitted), result
