"""K-15 through `run` (L.RB-11.1; WR-ENV-11, MC-12, MC-B-11): the `stack_pipeline` plugin (was
`integration_pipeline`), published unedited from `examples/packs/`, called once per variant
through the MC-12 MCP host, and its stack's teardown counted from the run's own events.

Shared by the HOST node (`host/test_pipeline_k15.py`: the operator's docker CLI) and its twin
(`twin/test_pipeline_k15_twin.py`: the same plugin over the fake docker CLI,
`trestle_packs.fakes.docker_cli`, found through `TRESTLE_DOCKER_PATH`, whose commands persist to a
JSON state file the test reads).

The three variants: `success` (the stage's tests pass), `pytest_failure` (a test fails), and
`up_failure` (the stack cannot come up: on the HOST the image variable names a digest that is not
cached and `pull_policy: never` forbids a pull; on the twin the fake fails its first wave). A
failing `up` tears itself down; otherwise the pipeline's `finally` does: one teardown per run.
Every variant's run succeeds; its answer's `ok` and `failed_stage` carry the verdict.
"""

from __future__ import annotations

import json
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
PIPELINE = REPO / "examples" / "packs" / "stack_pipeline.py"
COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "k15-compose" / "compose.yaml"
PLUGIN_NAME = "stack_pipeline"
TEARDOWN_EVENT = "teardown: compose down complete"
VARIANTS = ("success", "pytest_failure", "up_failure")
HOST_TIMEOUT_S = 180.0  # the pipeline's own waits: one wave (60 s) and a short pytest stage
MISSING_IMAGE = "alpine@sha256:" + "0" * 64  # never cached, never pulled (pull_policy: never)

_PASSING = "def test_stage_passes():\n    assert True\n"
_FAILING = "def test_stage_fails():\n    assert False, 'the planted failing stage test'\n"


def project_name(variant: str) -> str:
    return f"trestle-k15-{variant.replace('_', '-')}-{uuid.uuid4().hex[:8]}"


def workdir(base: Path, variant: str) -> Path:
    """The pipeline's working directory: the K-15 compose file and the stage's tests."""
    work = base / f"work-{variant}"
    (work / "tests").mkdir(parents=True)
    shutil.copy(COMPOSE, work / "compose.yaml")
    body = _FAILING if variant == "pytest_failure" else _PASSING
    (work / "tests" / "test_stage.py").write_text(body, encoding="utf-8")
    return work


def twin_docker(base: Path, *, fail_up: bool) -> tuple[Path, dict[str, str]]:
    """The fake docker's state file (`web` fails to come up with `fail_up`) and the environment
    that makes the plugin find it."""
    state = base / "compose-state.json"
    state.write_text(json.dumps({"fail": ["web"] if fail_up else []}), encoding="utf-8")
    return state, {"TRESTLE_DOCKER_PATH": str(write_fake_docker(base / "bin", state))}


@contextmanager
def pipeline_host(
    home: Path, *, environ: Mapping[str, str | None] | None = None
) -> Iterator[mcp_host.McpHost]:
    """The MCP host with the pipeline plugin published, unedited; `environ` is set for the host
    and its processes and restored afterwards."""
    changes: dict[str, str | None] = {"PYTHONPATH": harness.plugin_pythonpath(), **(environ or {})}
    saved = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (home / "plugins").mkdir(parents=True, exist_ok=True)
        shutil.copy(PIPELINE, home / "plugins" / PIPELINE.name)
        with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as host:
            yield host
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_pipeline(host: mcp_host.McpHost, work: Path, project: str) -> dict[str, Any]:
    """THE one call for a variant: `run(stack_pipeline, completion="terminal")`."""
    sent = host.request_count()
    answer = host.call(
        "run",
        {
            "plugin": PLUGIN_NAME,
            "args": {
                "stack_spec": {
                    "project": project,
                    "compose_file": "compose.yaml",
                    "teardown": "down",
                    "waves": [
                        {"name": "app", "services": ["web"], "wait": "started", "timeout_s": 60}
                    ],
                },
                "pytest_path": "tests",
                "workdir": str(work),
                "python": sys.executable,  # the stage test needs only pytest
            },
            "wait_ms": tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert host.request_count() == sent + 1, "one request, counted once by the MCP host"
    assert isinstance(answer, dict), answer
    return answer


def teardown_events(host: mcp_host.McpHost, run_id: str) -> int:
    """How many times the run's events say the stack was torn down."""
    (run_dir,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
    path = run_dir / "evidence" / "events.ndjson"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return sum(
        1
        for row in rows
        if row.get("kind") == "log" and row.get("payload", {}).get("message") == TEARDOWN_EVENT
    )


def expect_state(variant: str, answer: Mapping[str, Any]) -> None:
    """Every variant's run succeeds; the answer's verdict names the failed stage."""
    assert answer["state"] == "succeeded", answer
    result = answer["summary"]  # the plugin's result, its `summary_fields` first
    expected = {"success": None, "pytest_failure": "pytest", "up_failure": "docker"}[variant]
    assert (result["ok"], result["failed_stage"]) == (variant == "success", expected), answer


def fake_calls(state: Path) -> list[str]:
    """The fake docker's compose commands, in order."""
    return [c for c in read_state(state)["calls"] if c != "compose version"]
