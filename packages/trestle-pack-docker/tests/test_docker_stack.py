"""`docker_stack` (the docker pack), called with `FakeContext` over the fake docker CLI.

Copy this file beside an adapted wrapper to test it the same way: the fake answers `docker
compose` with Compose's semantics and records every command it was given."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
import trestle_pack_docker.cli as docker_cli
from docker_pack_testing import PYTHON, STACK, compose, context, fake_docker, load_wrapper
from trestle_pack_docker.results import StackResult

from trestle.plugin import Blocked, Failed
from trestle.plugin.testing import call_plugin

# K-3/K-4: these plugins take and return typed records (skipped if they are declined).
pytestmark = pytest.mark.typed_records

WAVES = {
    "project": "shop",
    "waves": [
        {"name": "data", "services": ["postgres", "redis"], "wait": "healthy", "timeout_s": 30},
        {"name": "app", "services": ["api"], "wait": "started", "timeout_s": 60},
    ],
    "depends_on": {"api": ["postgres", "redis"]},
}


def _run(tmp_path: Path, args: dict, **state: object) -> tuple[dict, list[str], dict]:
    """Call the wrapper; the answer, the docker commands the fake saw, and its final state."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        fake = fake_docker(tmp_path, monkeypatch, **state)
        ctx = context(tmp_path)
        answer = call_plugin(load_wrapper("docker_stack").docker_stack, args, ctx=ctx)
        assert isinstance(answer, dict)
        return answer, fake.calls, fake.state
    finally:
        monkeypatch.undo()


def _fails(tmp_path: Path, args: dict, **state: object) -> tuple[Failed, dict, list[str], dict]:
    """Like `_run` for a stack that must fail: the `Failed` raised, its result as a dict, the
    docker commands the fake saw and its final state."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        fake = fake_docker(tmp_path, monkeypatch, **state)
        with pytest.raises(Failed) as raised:
            call_plugin(load_wrapper("docker_stack").docker_stack, args, ctx=context(tmp_path))
        result = raised.value.result
        assert isinstance(result, StackResult)
        return raised.value, asdict(result), fake.calls, fake.state
    finally:
        monkeypatch.undo()


def test_minimal_form_starts_every_service_of_the_compose_file(tmp_path: Path) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    answer, calls, state = _run(tmp_path, {"workdir": str(work), "project": "shop"})
    assert answer["ok"] is True, answer
    assert answer["services"] == ["api", "postgres", "redis"]  # `debug` sits behind a profile
    assert answer["waves_completed"] == ["wave-0"]
    assert "up --detach --wait --wait-timeout 120 api postgres redis" in calls
    assert set(answer["logs"]) == {"api", "postgres", "redis"}
    assert state["containers"] == {
        "shop-api-1": "running",
        "shop-postgres-1": "running",
        "shop-redis-1": "running",
    }


def test_waves_run_in_order_with_their_wait_modes(tmp_path: Path) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    answer, calls, _ = _run(tmp_path, {"spec": WAVES, "workdir": str(work)})
    assert answer["ok"] is True and answer["waves_completed"] == ["data", "app"]
    ups = [c for c in calls if c.startswith("up ")]
    assert ups == ["up --detach --wait --wait-timeout 30 postgres redis", "up --detach api"]


def test_compose_variations_reach_the_command_line(tmp_path: Path) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    compose(work, "services:\n  api:\n    image: example/api:dev\n", "override.yml")
    (work / "dev.env").write_text("A=1\n", encoding="utf-8")
    spec = {
        "project": "shop",
        "compose_files": ["docker-compose.yml", "override.yml"],
        "profiles": ["debug"],
        "env_files": ["dev.env"],
        "build": True,
        "no_deps": True,
        "wait": "started",
    }
    monkeypatch = pytest.MonkeyPatch()
    try:
        fake_docker(tmp_path, monkeypatch)
        ctx = context(tmp_path)
        answer = call_plugin(
            load_wrapper("docker_stack").docker_stack,
            {"spec": spec, "workdir": str(work), "services": ["api", "debug"]},
            ctx=ctx,
        )
    finally:
        monkeypatch.undo()
    assert isinstance(answer, dict) and answer["ok"] is True, answer
    (up,) = [c.argv for c in ctx.exec_calls if "up" in c.argv]
    assert up[1:] == (
        "compose", "--project-name", "shop",
        "--file", str(work / "docker-compose.yml"), "--file", str(work / "override.yml"),
        "--profile", "debug", "--env-file", str(work / "dev.env"),
        "up", "--detach", "--build", "--no-deps", "api", "debug",
    )  # fmt: skip


@pytest.mark.proves("WR-OWN-4", "WR-OWN-4:legacy-default-keeps-volumes", "B", "B", "LOGIC", "CI")
@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-6", "B", "B", "LOGIC", "CI")
def test_failed_wave_tears_down_once_and_keeps_volumes(tmp_path: Path) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    failed, answer, calls, state = _fails(
        tmp_path, {"spec": WAVES, "workdir": str(work)}, fail=["api"]
    )
    assert failed.code == "stack_failed" and "up failed at app" in failed.detail
    assert "no such image for service api" in failed.detail
    assert answer["ok"] is False
    assert (answer["failed_step"], answer["teardown"]) == ("app", "down")
    assert "no such image for service api" in answer["error"]
    assert answer["waves_completed"] == ["data"]
    assert [c for c in calls if c.split()[0] in ("down", "stop")] == ["down"]  # no --volumes
    assert state["containers"] == {} and state["volumes"] == {"shop_data": "seeded"}


@pytest.mark.parametrize(("policy", "after"), [("stop", "exited"), ("none", "running")])
def test_declared_teardown_stop_or_none(tmp_path: Path, policy: str, after: str) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    spec = {**WAVES, "teardown": policy}
    _, answer, calls, state = _fails(tmp_path, {"spec": spec, "workdir": str(work)}, fail=["api"])
    assert answer["ok"] is False and answer["teardown"] == policy
    assert "down" not in calls
    assert state["containers"]["shop-postgres-1"] == after
    assert state["volumes"] == {"shop_data": "seeded"}


def test_stop_single_services_and_down(tmp_path: Path) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    args = {"workdir": str(work), "project": "shop"}
    assert _run(tmp_path, args)[0]["ok"] is True
    stopped, calls, state = _run(tmp_path, {**args, "action": "stop", "services": ["api"]})
    assert stopped["ok"] is True and calls[-1] == "stop api"
    assert state["containers"]["shop-api-1"] == "exited"
    assert state["containers"]["shop-redis-1"] == "running"
    down, calls, state = _run(tmp_path, {**args, "action": "down"})
    assert down["ok"] is True and calls[-1] == "down"
    assert state["containers"] == {} and state["volumes"] == {"shop_data": "seeded"}
    refused, answer, _, _ = _fails(tmp_path, {**args, "action": "down", "services": ["api"]})
    assert refused.code == "stack_failed" and "stop" in refused.detail
    assert answer["ok"] is False and "stop" in answer["error"]


def test_custom_wait_polls_the_probe(tmp_path: Path) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    marker = tmp_path / "ready"
    probe = [PYTHON, "-c", f"import pathlib,sys; p=pathlib.Path({str(marker)!r}); "
             "sys.exit(0 if p.exists() else (p.touch() or 1))"]  # fmt: skip
    spec = {
        "project": "shop",
        "waves": [{"name": "db", "services": ["postgres"], "wait": "custom", "timeout_s": 20}],
        "probes": {"postgres": {"command": probe}},
    }
    answer, calls, _ = _run(tmp_path, {"spec": spec, "workdir": str(work)})
    assert answer["ok"] is True, answer
    assert "up --detach postgres" in calls  # no --wait: the probe decides
    never = {**spec, "probes": {"postgres": {"command": ["false"]}}}
    never["waves"] = [{**spec["waves"][0], "timeout_s": 1}]
    _, answer, _, _ = _fails(tmp_path / "never", {"spec": never, "workdir": str(work)})
    assert answer["ok"] is False and "probe for postgres" in answer["error"]
    unprobed = {**spec, "probes": {}}
    _, answer, _, _ = _fails(tmp_path / "none", {"spec": unprobed, "workdir": str(work)})
    assert answer["ok"] is False and "needs probes" in answer["error"]


def test_bad_input_raises_failed_at_the_config_step(tmp_path: Path) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    _, missing, _, _ = _fails(tmp_path, {"workdir": str(work), "compose_file": "nope.yml"})
    assert missing["failed_step"] == "config" and "compose file not found" in missing["error"]
    _, unknown, _, _ = _fails(tmp_path, {"workdir": str(work), "services": ["ghost"]})
    assert unknown["failed_step"] == "config" and "ghost" in unknown["error"]
    cycle = {"depends_on": {"api": ["redis"], "redis": ["api"]}}
    _, looped, _, _ = _fails(tmp_path, {"workdir": str(work), "spec": cycle})
    assert looped["ok"] is False and "cycle" in looped["error"]


def test_missing_docker_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    monkeypatch.delenv("TRESTLE_DOCKER_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(docker_cli, "WELL_KNOWN_DOCKER", ())
    with pytest.raises(Blocked) as blocked:
        call_plugin(
            load_wrapper("docker_stack").docker_stack, {"workdir": str(work)}, ctx=context(tmp_path)
        )
    assert blocked.value.code == "docker_missing"
    assert "TRESTLE_DOCKER_PATH" in blocked.value.human_action


def test_unreachable_engine_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    compose(work, STACK)
    fake = fake_docker(tmp_path, monkeypatch, engine_down=True)
    with pytest.raises(Blocked) as blocked:
        call_plugin(
            load_wrapper("docker_stack").docker_stack, {"workdir": str(work)}, ctx=context(tmp_path)
        )
    assert blocked.value.code == "docker_unreachable"
    assert "Cannot connect" in blocked.value.human_action
    assert not [c for c in fake.calls if c.startswith("up")]


def test_fake_state_is_plain_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fake_docker(tmp_path, monkeypatch, fail=["x"])
    assert json.loads(fake.state_path.read_text(encoding="utf-8"))["fail"] == ["x"]
