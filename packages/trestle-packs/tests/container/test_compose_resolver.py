"""L.NW-2.7: the real Compose resolver's argv, refusals and bound, over the real `CommandPort` and
the absolute-path `fake_docker.py` shim (no engine, nothing pulled or started), and its agreement
with the fake over the same fixtures. The unmodified family suite runs in `test_conformance.py`."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import ENDPOINT, Rig
from trestle.workflow import ports
from trestle.workflow.ports import Closure, ClosureRefused

from trestle_packs.container import PortSet, bind
from trestle_packs.container import compose as compose_mod
from trestle_packs.container.compose import (
    BOUND_EXCEEDED,
    IDSET_MAX,
    UNKNOWN_IDENTIFIER,
    RealComposeResolver,
    config_args,
    dependencies,
)
from trestle_packs.container.engine import COMPOSE_DEFINITION_INVALID, TEXT_MAX
from trestle_packs.fakes.compose import FakeComposeResolver
from trestle_packs.process.command import CommandPort

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _envelope_hash(base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(base)).encode() + path.read_bytes())
    return digest.hexdigest()


@pytest.fixture
def definitions(tmp_path: Path) -> Path:
    base = tmp_path / "defs"
    base.mkdir()
    shutil.copy(FIXTURES / "compose-chain.yaml", base / "chain.json")
    shutil.copy(FIXTURES / "compose-changed.yaml", base / "changed.json")
    (base / "invalid.json").write_text("{not json", encoding="utf-8")
    return base


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    return tmp_path / "compose-artifacts"


@pytest.fixture
def resolver(rig: Rig, definitions: Path, artifacts: Path) -> RealComposeResolver:
    projects = {p.stem: p for p in definitions.glob("*.json")}
    port = bind(
        rig.executable,
        ENDPOINT,
        CommandPort(),
        compose_projects=projects,
        compose_artifacts=artifacts,
    )
    assert port.compose is not None
    return port.compose


def test_bind_sets_the_compose_resolver_in_the_port_map(rig: Rig) -> None:
    port = bind(rig.executable, ENDPOINT, CommandPort())
    assert isinstance(port, PortSet) and isinstance(port.compose, RealComposeResolver)
    assert port.as_map()[ports.ComposeResolver] is port.compose


def test_the_read_is_one_compose_config_call_with_the_pinned_shape(
    resolver: RealComposeResolver, rig: Rig, definitions: Path, artifacts: Path
) -> None:
    before = _envelope_hash(definitions)
    result = resolver.closure("chain", frozenset({"web"}))
    assert isinstance(result, Closure)
    (call,) = rig.calls()
    assert call["host"] == ENDPOINT and call["stdin"] in ("eof", "closed")
    output = call["args"][-1]
    assert Path(output).parent == artifacts  # the artifact directory, never the definition's
    assert call["args"] == list(config_args(str(definitions / "chain.json"), output))
    assert call["args"][:3] == ["compose", "-f", str(definitions / "chain.json")]
    assert list(artifacts.iterdir()) == []  # the artifact is removed once read
    assert "up" not in call["args"] and "pull" not in call["args"]
    assert all(v in (None, "") for v in call["env"].values())  # environment built from empty
    assert _envelope_hash(definitions) == before  # a read writes nothing
    assert rig.read()["containers"] == []


@pytest.mark.parametrize(
    "selected", [{"web"}, {"web", "worker"}, {"cache"}, {"api", "db"}, {"web", "worker", "cache"}]
)
@pytest.mark.parametrize("project", ["chain", "changed"])
def test_closure_and_fingerprint_equal_the_fakes_over_the_same_fixtures(
    resolver: RealComposeResolver, definitions: Path, project: str, selected: set[str]
) -> None:
    fake = FakeComposeResolver({p.stem: p for p in definitions.glob("*.json")})
    real = resolver.closure(project, frozenset(selected))
    twin: Any = fake.closure(project, frozenset(selected))
    assert isinstance(real, Closure)
    assert (real.services, real.edges, real.definition_fingerprint) == (
        twin.services,
        twin.edges,
        twin.definition_fingerprint,
    )


def test_an_unknown_service_is_named_and_no_partial_closure_is_returned(
    resolver: RealComposeResolver,
) -> None:
    refused = resolver.closure("chain", frozenset({"web", "zzz", "aaa"}))
    assert refused == ClosureRefused(UNKNOWN_IDENTIFIER, "aaa")


def test_the_code_values_are_the_vocabularys() -> None:
    assert (UNKNOWN_IDENTIFIER, BOUND_EXCEEDED) == (
        "admission.unknown_identifier",
        "admission.bound_exceeded",
    )
    assert COMPOSE_DEFINITION_INVALID == "adapter.compose_definition_invalid"


@pytest.mark.parametrize("project", ["invalid", "no-such-project", "missing-file"])
def test_an_unreadable_definition_is_refused_as_invalid(
    rig: Rig, definitions: Path, project: str
) -> None:
    projects = {
        "invalid": definitions / "invalid.json",
        "missing-file": definitions / "gone.json",
    }
    port = bind(rig.executable, ENDPOINT, CommandPort(), compose_projects=projects)
    assert port.compose is not None
    refused = port.compose.closure(project, frozenset({"web"}))
    assert isinstance(refused, ClosureRefused)
    assert refused.code == COMPOSE_DEFINITION_INVALID and 0 < len(refused.subject) <= TEXT_MAX


def test_a_missing_docker_cli_is_a_refused_definition_not_an_exception(
    tmp_path: Path, definitions: Path
) -> None:
    port = bind(
        str(tmp_path / "no-such-docker"),
        None,
        CommandPort(),
        compose_projects={"chain": definitions / "chain.json"},
    )
    assert port.compose is not None
    refused = port.compose.closure("chain", frozenset({"web"}))
    assert isinstance(refused, ClosureRefused)
    assert refused.code == COMPOSE_DEFINITION_INVALID


def test_a_dependency_on_a_service_the_definition_lacks_is_invalid(
    rig: Rig, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"services": {"a": {"depends_on": ["ghost"]}}}))
    port = bind(rig.executable, ENDPOINT, CommandPort(), compose_projects={"broken": broken})
    assert port.compose is not None
    refused = port.compose.closure("broken", frozenset({"a"}))
    assert isinstance(refused, ClosureRefused)
    assert refused.code == COMPOSE_DEFINITION_INVALID and "ghost" in refused.subject


def test_a_bad_depends_on_is_invalid() -> None:
    assert dependencies({"depends_on": 3}) is None
    assert dependencies({"depends_on": {"b": {}, "a": {}}}) == ["a", "b"]
    assert dependencies({"depends_on": ["b", "a"]}) == ["a", "b"]
    assert dependencies({}) == []


def test_a_definition_longer_than_the_console_excerpt_is_read_whole(
    rig: Rig, tmp_path: Path
) -> None:
    # the port returns only a TEXT_MAX console tail (B3-C14): the document is read from the
    # `--output` artifact instead, whole (L.NW-2.7.fix1)
    services = {f"s{i}": {"depends_on": [f"s{i + 1}"] if i < 60 else []} for i in range(61)}
    big = tmp_path / "big.json"
    big.write_text(json.dumps({"services": services}))
    assert len(big.read_bytes()) > TEXT_MAX
    port = bind(rig.executable, ENDPOINT, CommandPort(), compose_projects={"big": big})
    assert port.compose is not None
    closure = port.compose.closure("big", frozenset({"s0"}))
    assert isinstance(closure, Closure)
    assert closure.services == {f"s{i}" for i in range(61)} and len(closure.edges) == 60


def test_a_config_call_that_writes_no_artifact_is_invalid(
    resolver: RealComposeResolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a `config` that prints instead of saving: the console tail is never parsed as the document
    monkeypatch.setattr(compose_mod, "config_args", lambda definition, output: (
        "compose", "-f", definition, "config", "--format", "json", "--no-interpolate",
    ))  # fmt: skip
    refused = resolver.closure("chain", frozenset({"web"}))
    assert isinstance(refused, ClosureRefused) and refused.code == COMPOSE_DEFINITION_INVALID
    assert "no rendered definition" in refused.subject


def test_a_relative_definition_path_is_rejected_at_bind(rig: Rig) -> None:
    with pytest.raises(ValueError, match="absolute"):
        bind(rig.executable, None, CommandPort(), compose_projects={"p": "relative/compose.yaml"})


def test_the_bound_constants() -> None:
    assert IDSET_MAX == 1024
