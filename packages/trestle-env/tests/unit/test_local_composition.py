"""The local-override composition helper (L.RB-8.2): what an override needs is bound from the
operator's environment, and a call goes to the port of the realization it is about."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from trestle.workflow.declarations import RealizationKind
from trestle.workflow.ports import BoundCommand, ResourceSpec
from trestle.workflow.values import CreatedHandle, FoundRef, Lineage, NodePath, SelectorRef
from twin import overrides

from trestle_env.catalog import load_reference
from trestle_env.plugins import _local


class Recorder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, Any]] = []

    def __getattr__(self, member: str) -> Any:
        def call(*args: Any) -> str:
            self.calls.append((member, args))
            return self.name

        return call


def router() -> tuple[_local.RealizationRouter, Recorder, Recorder, Recorder, Recorder]:
    container_reads, create, owned = Recorder("cr"), Recorder("cc"), Recorder("co")
    local = Recorder("local")
    return (
        _local.RealizationRouter(container_reads, create, owned, local, local),
        container_reads,
        create,
        owned,
        local,
    )


LINEAGE = Lineage("r", NodePath(("n",)))
LOCAL_SPEC = ResourceSpec("s", RealizationKind.AGENT_LAUNCHED_PROJECT, "p", None)
DOCKER_SPEC = ResourceSpec("s", RealizationKind.DOCKER_SERVICE, "s", None)


def test_specs_and_targets_go_to_the_port_of_their_own_realization() -> None:
    r, container_reads, create, owned, local = router()
    assert r.observe(LOCAL_SPEC, LINEAGE, "up") == "local"
    assert r.observe(DOCKER_SPEC, LINEAGE, "up") == "cr"
    assert r.create(LOCAL_SPEC, "t") == "local" and r.create(DOCKER_SPEC, "t") == "cc"
    assert r.launch_policy(LOCAL_SPEC) == "local" and r.launch_policy(DOCKER_SPEC) == "cc"
    proc = CreatedHandle(LINEAGE, "up", "proc-0123456789abcdef", None)  # type: ignore[arg-type]
    box = CreatedHandle(LINEAGE, "up", "trwr-run-node", None)  # type: ignore[arg-type]
    assert r.stop(proc, "t") == "local" and r.stop(box, "t") == "co"
    assert r.restart(proc, "t") == "local" and r.recreate(box, "t") == "co"
    assert r.check("ready", proc) == "local" and r.check("running", box) == "cr"
    assert r.endpoint(proc, "host") == "local" and r.endpoint(box, "host") == "cr"
    found_local = FoundRef("local_process", "found-1-2", None)  # type: ignore[arg-type]
    found_box = FoundRef("docker_container", "some-name", None)  # type: ignore[arg-type]
    assert r.check("ready", found_local) == "local" and r.check("running", found_box) == "cr"
    ref = SelectorRef(LINEAGE, "up", "proc-abc", None)  # type: ignore[arg-type]
    assert r.check("ready", ref) == "local"
    seen = [name for name, _ in container_reads.calls]
    assert seen == ["observe", "check", "endpoint", "check"]  # only the Docker-shaped calls


def test_release_descriptors_follow_the_call() -> None:
    r, _, create, owned, local = router()
    create_local = SimpleNamespace(member="create", arguments={"spec": LOCAL_SPEC})
    create_box = SimpleNamespace(member="create", arguments={"spec": DOCKER_SPEC})
    stop_local = SimpleNamespace(
        member="stop", arguments={"target": SimpleNamespace(selector="proc-x")}
    )
    stop_box = SimpleNamespace(
        member="stop", arguments={"target": SimpleNamespace(selector="trwr-x")}
    )
    assert r.release_descriptor(create_local) == "local"
    assert r.release_descriptor(create_box) == "cc"
    assert r.release_descriptor(stop_local) == "local"
    assert r.release_descriptor(stop_box) == "co"


def environ(tmp_path: Path, **override: str | None) -> dict[str, str]:
    base = {k: v for k, v in overrides.operator_environ(tmp_path).items() if v is not None}
    for key, value in override.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    return base


def test_the_command_is_bound_from_the_operators_toolchain(tmp_path: Path) -> None:
    bound = _local.LocalOverrides(load_reference(), environ(tmp_path)).command_for(
        "http_support_local", port=4321
    )
    assert isinstance(bound, BoundCommand)
    assert bound.argv[0] == bound.resolved.executable == sys.executable  # never a PATH lookup
    assert bound.argv[1].endswith("tests/fixtures/apps/override_app.py")
    assert Path(bound.argv[1]).is_absolute()
    assert dict(bound.environment) == {"PORT": "4321"}
    assert bound.resolved.reported_version.startswith("3.12")


def test_a_missing_repository_a_missing_mise_and_an_unmet_pin_block(tmp_path: Path) -> None:
    catalog = load_reference()
    absent = json.dumps({"override-app": str(tmp_path / "nowhere")})
    blocked = _local.LocalOverrides(
        catalog, environ(tmp_path, TRESTLE_ENV_PROJECT_DIRS=absent)
    ).command_for("http_support_local")
    assert isinstance(blocked, _local.OverrideBlocked)
    assert blocked.code == "environment.repository_missing"
    unset = _local.LocalOverrides(
        catalog, environ(tmp_path, TRESTLE_ENV_PROJECT_DIRS=None)
    ).command_for("http_support_local")
    assert isinstance(unset, _local.OverrideBlocked) and unset.code == blocked.code
    no_mise = _local.LocalOverrides(catalog, environ(tmp_path, TRESTLE_MISE_PATH=None)).command_for(
        "http_support_local"
    )
    assert isinstance(no_mise, _local.OverrideBlocked)
    assert no_mise.code == "execution.toolchain_missing"
    with pytest.raises(ValueError):
        _local.LocalOverrides(catalog, environ(tmp_path)).command_for("nope")


def test_a_free_port_is_a_loopback_port_nothing_listens_on() -> None:
    port = _local.free_port()
    assert 1024 <= port <= 65535
