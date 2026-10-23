"""K-6 across every registered entry point that can select a teardown (L.RB-3.3; WR-OWN-4, F-13(d)
wider reading), shared by the HOST node `host/test_k6_entry_points.py` and its twin
`twin/test_k6_entry_points_twin.py`.

Each case is one call through `run` on the MC-12 MCP host, and afterwards the seeded named volume
must still be there:

* `docker_stack-<teardown>`: the `docker_stack` plugin (examples/packs, unedited) over the K-6
  fixture's own Compose project. Its first wave starts `keeper`, which writes a seed file into the
  named volume `k6data`; its second wave names `broken`, whose image is a digest that is never
  cached (`pull_policy: never`), so `up` fails (`ok: false`) and the plugin tears the stack down
  per the declared value (`down`, `stop` or `none`);
* `stack_pipeline-<teardown>`: the pipeline with the first wave only and a passing stage test,
  so its `finally` tears the stack down per the declared value (K-15: once);
* `reference_env-release`: the reference plugin on its own binding, with a fixture-labelled named
  volume seeded before the call; the run's release (observe, stop, remove without volumes,
  observe) must leave it.

`stop`, `none` and a failed `up` leave containers by design; every object the fixture creates
carries `trestle.proof.fixture=k6`, so the gate's record counts them as attributable residue and
`docker_gate run` housekeeping removes them after the suite (CSC-10); each case also removes its
own objects afterwards.

Both nodes publish the plugins unedited. The HOST node's plugins run the operator's docker; the
twin's find the fake docker CLI (`trestle_packs.fakes.docker_cli`, Compose's semantics over a JSON
state file) through `TRESTLE_DOCKER_PATH`, and the twin runs the reference plugin on the fake
binding (`twin.fake_binding`) with the volume seeded in the fake engine's state.
"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import mcp_host, tolerances
from trestle_packs.fakes.docker_cli import read_state, write_fake_docker

from twin import harness

REPO = Path(__file__).resolve().parents[4]
PACKS = REPO / "examples" / "packs"
COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "k6-compose" / "compose.yaml"
FIXTURE_LABEL = "trestle.proof.fixture"
FIXTURE = "k6"
VOLUME = "k6data"
SEED_FILE = "/data/seed"
BROKEN_IMAGE_ENV = "TRESTLE_IMAGE_K6_BROKEN"
MISSING_IMAGE = "alpine@sha256:" + "0" * 64  # never cached, never pulled (pull_policy: never)
HOST_TIMEOUT_S = 180.0

LEGACY = ("docker_stack", "stack_pipeline")
TEARDOWNS = ("down", "stop", "none")
CASES = tuple(f"{p}-{t}" for p in LEGACY for t in TEARDOWNS) + ("reference_env-release",)

_PASSING = "def test_stage_passes():\n    assert True\n"


def split(case: str) -> tuple[str, str]:
    plugin, _, teardown = case.partition("-")
    return plugin, teardown


def project_name(case: str) -> str:
    return f"trestle-k6-{case.replace('_', '-')}-{uuid.uuid4().hex[:8]}"


def volume_name(project: str) -> str:
    """The name Compose gives the fixture's named volume in `project`."""
    return f"{project}_{VOLUME}"


def workdir(base: Path, case: str) -> Path:
    work = base / f"work-{case}"
    (work / "tests").mkdir(parents=True)
    shutil.copy(COMPOSE, work / "compose.yaml")
    (work / "tests" / "test_stage.py").write_text(_PASSING, encoding="utf-8")
    return work


def twin_docker(base: Path) -> tuple[Path, dict[str, str]]:
    """The fake docker's state file (`broken` fails to come up) and the environment that makes the
    plugins find it."""
    state = base / "engine.json"
    state.write_text('{"fail": ["broken"]}', encoding="utf-8")
    return state, {"TRESTLE_DOCKER_PATH": str(write_fake_docker(base / "bin", state))}


@contextmanager
def legacy_host(
    home: Path, plugin: str, *, environ: Mapping[str, str | None] | None = None
) -> Iterator[mcp_host.McpHost]:
    """The MCP host with one plugin published from examples/packs, unedited; `environ` is set for
    the host and its processes, then restored."""
    changes: dict[str, str | None] = {
        "PYTHONPATH": harness.plugin_pythonpath(),
        BROKEN_IMAGE_ENV: MISSING_IMAGE,
        **(environ or {}),
    }
    saved = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (home / "plugins").mkdir(parents=True, exist_ok=True)
        shutil.copy(PACKS / f"{plugin}.py", home / "plugins" / f"{plugin}.py")
        with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as host:
            yield host
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def legacy_args(plugin: str, teardown: str, project: str, work: Path) -> dict[str, Any]:
    keep = {"name": "keep", "services": ["keeper"], "wait": "started", "timeout_s": 60}
    broken = {"name": "broken", "services": ["broken"], "wait": "started", "timeout_s": 60}
    spec = {"project": project, "compose_file": "compose.yaml", "teardown": teardown}
    if plugin == "docker_stack":
        return {"spec": {**spec, "waves": [keep, broken]}, "workdir": str(work)}
    return {
        "stack_spec": {**spec, "waves": [keep]},
        "pytest_path": "tests",
        "workdir": str(work),
        "python": sys.executable,  # the stage test needs only pytest
    }


def call(host: mcp_host.McpHost, plugin: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """THE one call: `run(<plugin>, completion="terminal")`, counted once by the MCP host."""
    sent = host.request_count()
    answer = host.call(
        "run",
        {
            "plugin": plugin,
            "args": dict(args),
            "wait_ms": tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert host.request_count() == sent + 1, "one request, counted once by the MCP host"
    assert isinstance(answer, dict), answer
    return answer


def expect_state(plugin: str, answer: Mapping[str, Any]) -> None:
    """Both runs succeed; docker_stack's second wave fails (`ok: false`, after its teardown) and
    the pipeline passes."""
    assert answer["state"] == "succeeded", answer
    result = answer["summary"]  # the plugin's result, its `summary_fields` first
    assert result["ok"] is (plugin != "docker_stack"), answer


def teardown_calls(plugin: str, teardown: str) -> list[str]:
    """What the plugin asks docker after `up` for this case (none for `none`)."""
    return {"down": ["down"], "stop": ["stop"], "none": []}[teardown]


def engine_calls(state: Path) -> list[str]:
    """The fake docker's `up`, `stop` and `down` commands, by name."""
    return [
        c.split()[0] for c in read_state(state)["calls"] if c.split()[0] in ("up", "stop", "down")
    ]


def engine_state(path: Path) -> dict[str, Any]:
    return read_state(path)
