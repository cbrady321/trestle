"""A `TRESTLE_ENV_PORTS` factory for the toolchain leg's MCP cases (L.RB-4.5; not a test module).

The healthy fake engine of the twins (`twin.fake_binding.fake_ports`), plus the REAL toolchain
resolver and task runner over the mise-shaped stub the case built (`TESTKIT_MISE`, an absolute
path; its answers come from `TESTKIT_MISE_CONFIG`, calls logged to `TESTKIT_MISE_LOG`). The stub
stands for mise: the real tool is unverified (D-1, OPEN-MISE-HOST). Named by absolute path because
the published plugin's process cannot import the test session's modules."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from trestle.workflow import ports
from trestle_packs.process.command import CommandPort

from trestle_env import tree
from trestle_env.plugins import _bind
from trestle_env.plugins._tasks import TaskExecution
from twin import fake_binding


def stub_toolchain_ports(environ: Mapping[str, str]) -> Mapping[type, object]:
    """The twins' fake engine plus the stub toolchain."""
    return _with_toolchain(dict(fake_binding.fake_ports(environ)), environ)


def real_docker_stub_toolchain_ports(environ: Mapping[str, str]) -> Mapping[type, object]:
    """The REAL binding over the operator's docker (a HOST case) plus the stub toolchain."""
    real = dict(_bind.reference_ports(environ, use_seam=False))
    return _with_toolchain(real, environ)


def _with_toolchain(
    mapping: dict[type, object], environ: Mapping[str, str]
) -> Mapping[type, object]:
    project_environment = {
        str(p.id): {
            "STUB_MISE_CONFIG": environ["TESTKIT_MISE_CONFIG"],
            "STUB_MISE_LOG": environ["TESTKIT_MISE_LOG"],
        }
        for p in tree.CATALOG.projects
    }
    execution = CommandPort()
    resolver, runner = _bind.toolchain_ports(
        environ,
        execution,
        mise=environ["TESTKIT_MISE"],
        project_environment=project_environment,
        artifacts=Path(environ["TESTKIT_ARTIFACTS"]),
    )
    assert resolver is not None and runner is not None
    mapping[ports.ToolchainResolver] = resolver
    mapping[ports.ExecutionPort] = TaskExecution(execution, runner)
    return mapping
