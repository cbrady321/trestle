"""K-15 through `run` (L.RB-11.1; WR-ENV-11, MC-12, MC-B-11): the legacy `integration_pipeline`
plugin, published unedited from `examples/packs/`, called once per variant through the MC-12 MCP
host, and its stack's teardown counted from the run's own events.

Shared by the HOST node (`host/test_pipeline_k15.py`: the operator's docker through the plugin's
default `WhaleComposeBackend`) and its twin (`twin/test_pipeline_k15_twin.py`: the same plugin
source with one appended line that hands `StackRunner` a recording in-memory backend, the
`FakeComposeBackend` shape, whose calls persist to a JSON file the test reads).

The three variants: `success` (the stage's tests pass), `pytest_failure` (a test fails), and
`up_failure` (the stack cannot come up: on the HOST the image variable names a digest that is not
cached and `pull_policy: never` forbids a pull; on the twin the fake fails its first wave). A
failing `up` tears itself down; otherwise the pipeline's `finally` does: one teardown per run.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import mcp_host, tolerances

from twin import harness

REPO = Path(__file__).resolve().parents[4]
PIPELINE = REPO / "examples" / "packs" / "integration_pipeline.py"
COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "k15-compose" / "compose.yaml"
PLUGIN_NAME = "integration_pipeline"
TEARDOWN_EVENT = "teardown: compose down complete"
VARIANTS = ("success", "pytest_failure", "up_failure")
HOST_TIMEOUT_S = 180.0  # the pipeline's own waits: one wave (60 s) and a short pytest stage
MISSING_IMAGE = "alpine@sha256:" + "0" * 64  # never cached, never pulled (pull_policy: never)

FAKE_STATE_ENV = "TRESTLE_K15_FAKE_STATE"
FAKE_FAIL_UP_ENV = "TRESTLE_K15_FAKE_FAIL_UP"
# appended to the published copy of the pipeline on the twin only: `StackRunner` is looked up when
# the plugin function runs, so this hands every runner the recording backend
TWIN_SHIM = "\nfrom twin.k15 import twin_runner as _twin_runner  # noqa: E402\n" + (
    "StackRunner = _twin_runner(StackRunner)  # type: ignore[misc]  # noqa: F811\n"
)

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


@contextmanager
def pipeline_host(
    home: Path, *, twin: bool, environ: Mapping[str, str | None] | None = None
) -> Iterator[mcp_host.McpHost]:
    """The MCP host with the pipeline plugin published, unedited (the twin appends only
    `TWIN_SHIM`); `environ` is set for the host and its processes and restored afterwards."""
    changes: dict[str, str | None] = {"PYTHONPATH": harness.plugin_pythonpath(), **(environ or {})}
    saved = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (home / "plugins").mkdir(parents=True, exist_ok=True)
        source = PIPELINE.read_text(encoding="utf-8")
        (home / "plugins" / PIPELINE.name).write_text(
            source + (TWIN_SHIM if twin else ""), encoding="utf-8"
        )
        with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as host:
            yield host
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_pipeline(host: mcp_host.McpHost, work: Path, project: str) -> dict[str, Any]:
    """THE one call for a variant: `run(integration_pipeline, completion="terminal")`."""
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
    """Success answers passed; a failing stage or a failed `up` raises in the plugin."""
    if variant == "success":
        assert answer["state"] == "succeeded", answer
    else:
        assert answer["state"] == "failed", answer


# ---- the twin's recording backend (runs inside the plugin process) -------------------------


class RecordingComposeBackend:
    """`FakeComposeBackend`'s behaviour (packs tests), with every call appended to the JSON-lines
    file `TRESTLE_K15_FAKE_STATE` names; `TRESTLE_K15_FAKE_FAIL_UP=1` fails the first wave."""

    def __init__(self) -> None:
        self._state = Path(os.environ[FAKE_STATE_ENV])
        self._fail_up = os.environ.get(FAKE_FAIL_UP_ENV) == "1"
        self._logs: dict[str, list[str]] = {}

    def _record(self, call: str, **fields: Any) -> None:
        with self._state.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"call": call, **fields}) + "\n")

    def is_available(self) -> bool:
        return True

    def up(self, spec: Any, services: list[str], *, wait: Any, timeout_s: float, cwd: Path) -> None:
        self._record("up", project=spec.project, services=list(services))
        if self._fail_up:
            raise RuntimeError("simulated failure on wave 0")
        for service in services:
            self._logs.setdefault(service, []).append(f"{service} started")

    def down(self, spec: Any, *, cwd: Path, remove_volumes: bool = False) -> None:
        self._record("down", project=spec.project, remove_volumes=remove_volumes)

    def stop(self, spec: Any, *, cwd: Path) -> None:
        self._record("stop", project=spec.project)

    def service_logs(self, spec: Any, services: list[str], *, cwd: Path) -> dict[str, str]:
        return {s: "\n".join(self._logs.get(s, [])) + "\n" for s in services}


def twin_runner(runner_class: Any) -> Any:
    def build(ctx: Any) -> Any:
        return runner_class(ctx, backend=RecordingComposeBackend())

    return build


def fake_calls(state: Path) -> list[dict[str, Any]]:
    if not state.exists():
        return []
    return [json.loads(line) for line in state.read_text(encoding="utf-8").splitlines() if line]
