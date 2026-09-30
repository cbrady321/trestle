"""K-6 across every registered entry point that can select a teardown (L.RB-3.3; WR-OWN-4, F-13(d)
wider reading), shared by the HOST node `host/test_k6_entry_points.py` and its twin
`twin/test_k6_entry_points_twin.py`.

Each case is one call through `run` on the MC-12 MCP host, and afterwards the seeded named volume
must still be there:

* `docker_stack-<teardown>`: the legacy `docker_stack` plugin (examples/packs, unedited) over the
  K-6 fixture's own Compose project. Its first wave starts `keeper`, which writes a seed file into
  the named volume `k6data`; its second wave names `broken`, whose image is a digest that is never
  cached (`pull_policy: never`), so `up` fails and the runner tears the stack down per the declared
  value (`down`, `stop` or `none`);
* `integration_pipeline-<teardown>`: the legacy pipeline with the first wave only and a passing
  stage test, so its `finally` tears the stack down per the declared value (K-15: once);
* `reference_env-release`: the reference plugin on its own binding, with a fixture-labelled named
  volume seeded before the call; the run's release (observe, stop, remove without volumes,
  observe) must leave it.

`stop`, `none` and a failed `up` leave containers by design; every object the fixture creates
carries `trestle.proof.fixture=k6`, so the gate's record counts them as attributable residue and
`docker_gate run` housekeeping removes them after the suite (CSC-10); each case also removes its
own objects afterwards.

The HOST node publishes the plugins unedited; the twin appends one line to the published legacy
copies (`TWIN_SHIM`) that hands `StackRunner` an in-memory engine backend (`EngineBackend`, the
`FakeComposeBackend` shape) whose state persists to a JSON file, and runs the reference plugin on
the fake binding (`twin.fake_binding`) with the volume seeded in the fake engine's state.
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
PACKS = REPO / "examples" / "packs"
COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "k6-compose" / "compose.yaml"
FIXTURE_LABEL = "trestle.proof.fixture"
FIXTURE = "k6"
VOLUME = "k6data"
SEED_FILE = "/data/seed"
BROKEN_IMAGE_ENV = "TRESTLE_K6_BROKEN_IMAGE"
MISSING_IMAGE = "alpine@sha256:" + "0" * 64  # never cached, never pulled (pull_policy: never)
HOST_TIMEOUT_S = 180.0

LEGACY = ("docker_stack", "integration_pipeline")
TEARDOWNS = ("down", "stop", "none")
CASES = tuple(f"{p}-{t}" for p in LEGACY for t in TEARDOWNS) + ("reference_env-release",)

FAKE_STATE_ENV = "TRESTLE_K6_FAKE_STATE"
TWIN_SHIM = "\nfrom twin.k6 import twin_runner as _twin_runner  # noqa: E402\n" + (
    "StackRunner = _twin_runner(StackRunner)  # type: ignore[misc]  # noqa: F811\n"
)
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


@contextmanager
def legacy_host(
    home: Path, plugin: str, *, twin: bool, environ: Mapping[str, str | None] | None = None
) -> Iterator[mcp_host.McpHost]:
    """The MCP host with one legacy plugin published from examples/packs, unedited (the twin
    appends only `TWIN_SHIM`); `environ` is set for the host and its processes, then restored."""
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
        source = (PACKS / f"{plugin}.py").read_text(encoding="utf-8")
        (home / "plugins" / f"{plugin}.py").write_text(
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
    """docker_stack's second wave fails (`up` raises after its teardown); the pipeline passes."""
    expected = "failed" if plugin == "docker_stack" else "succeeded"
    assert answer["state"] == expected, answer


def teardown_calls(plugin: str, teardown: str) -> list[str]:
    """What the runner asks the backend after `up` for this case (none for `none`)."""
    return {"down": ["down(volumes=False)"], "stop": ["stop"], "none": []}[teardown]


# ---- the twin's engine backend (runs inside the plugin process) ---------------------------------


class EngineBackend:
    """`FakeComposeBackend`'s shape over an in-memory engine that applies Compose's semantics, its
    state persisted to the JSON file `TRESTLE_K6_FAKE_STATE` names: `up` creates the service
    containers, the project's default network and the named volume (seeding it); `down` removes
    containers and networks, and volumes only with `remove_volumes`; `stop` stops containers. A
    wave naming `broken` fails, as the uncached image does on the HOST."""

    def __init__(self) -> None:
        self._path = Path(os.environ[FAKE_STATE_ENV])

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            loaded: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
            return loaded
        return {"containers": {}, "networks": [], "volumes": {}, "calls": []}

    def _save(self, state: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")

    def is_available(self) -> bool:
        return True

    def up(self, spec: Any, services: list[str], *, wait: Any, timeout_s: float, cwd: Path) -> None:
        state = self._load()
        state["calls"].append("up")
        if "broken" in services:
            self._save(state)
            raise RuntimeError(f"no such image: {MISSING_IMAGE}")
        for service in services:
            state["containers"][f"{spec.project}-{service}-1"] = "running"
        if f"{spec.project}_default" not in state["networks"]:
            state["networks"].append(f"{spec.project}_default")
        state["volumes"][volume_name(spec.project)] = "seeded"
        self._save(state)

    def down(self, spec: Any, *, cwd: Path, remove_volumes: bool = False) -> None:
        state = self._load()
        state["calls"].append(f"down(volumes={remove_volumes})")
        prefix = f"{spec.project}-"
        state["containers"] = {
            k: v for k, v in state["containers"].items() if not k.startswith(prefix)
        }
        state["networks"] = [n for n in state["networks"] if n != f"{spec.project}_default"]
        if remove_volumes:
            state["volumes"].pop(volume_name(spec.project), None)
        self._save(state)

    def stop(self, spec: Any, *, cwd: Path) -> None:
        state = self._load()
        state["calls"].append("stop")
        for name in state["containers"]:
            if name.startswith(f"{spec.project}-"):
                state["containers"][name] = "exited"
        self._save(state)

    def service_logs(self, spec: Any, services: list[str], *, cwd: Path) -> dict[str, str]:
        return {s: f"{s} started\n" for s in services}


def twin_runner(runner_class: Any) -> Any:
    def build(ctx: Any) -> Any:
        return runner_class(ctx, backend=EngineBackend())

    return build


def engine_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"containers": {}, "networks": [], "volumes": {}, "calls": []}
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded
