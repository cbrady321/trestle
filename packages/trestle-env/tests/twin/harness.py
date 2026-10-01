"""One MCP call to the reference plugin, and what the record says of it (L.RB-0.4; MC-12, MC-19).

Shared by every Slice B HOST node and its CI twin, so the two run the SAME call and read the SAME
facts: the HOST node binds the real container adapter over the operator's docker (the host-docker
gate exports `TRESTLE_DOCKER_ENDPOINT` and `TRESTLE_IMAGE_<ROLE>`), the twin sets
`TRESTLE_ENV_PORTS` to a `fake_binding` seam. The plugin, the tree, the call and the assertions
are the same; only the port map differs.

`reference_host` starts the MC-12 MCP host (`trestle serve` over stdio, one request counted per
call) with the unmodified `reference_env` plugin in its plugin directory and this checkout on the
plugin processes' `PYTHONPATH`. The lane is read through the proof court's own oracle
(`tests.proof.records`), never the product's reader.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import mcp_host, records, tolerances
from trestle.common import clock

from trestle_env import tree
from trestle_env.plugins import _bind, reference_env

REPO = Path(__file__).resolve().parents[4]
ENV_TESTS = Path(__file__).resolve().parents[1]
PLUGIN = Path(reference_env.__file__)
PLUGIN_NAME = "reference_env"
SELECTOR_PREFIX = "trwr-"
# a terminal call waits out the declared deadline and the finalization margin at most
HOST_TIMEOUT_S = float(tree.DEADLINE_S) + clock.finalization_margin + tolerances.JOIN_WAIT_S


def plugin_pythonpath() -> str:
    """This checkout's packages, and the env tests directory (the `twin.fake_binding` seam)."""
    paths = [
        REPO,
        REPO / "packages" / "trestle-packs",
        REPO / "packages" / "trestle-env",
        ENV_TESTS,
    ]
    inherited = os.environ.get("PYTHONPATH", "")
    return os.pathsep.join([*(str(p) for p in paths), *([inherited] if inherited else [])])


@contextmanager
def reference_host(
    home: Path, environ: Mapping[str, str | None] | None = None
) -> Iterator[mcp_host.McpHost]:
    """The MCP host with the reference plugin published. `environ` is set (a `None` value unset)
    for the host and every process it starts, and restored afterwards."""
    changes: dict[str, str | None] = {"PYTHONPATH": plugin_pythonpath(), **(environ or {})}
    saved = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        home.mkdir(parents=True, exist_ok=True)
        (home / "plugins").mkdir(exist_ok=True)
        shutil.copy(PLUGIN, home / "plugins" / PLUGIN.name)
        with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as host:
            yield host
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def twin_environ(state: Path, seam: str | None = None) -> dict[str, str | None]:
    """A twin's operator environment: the fake binding seam and its state file; the real
    binding's variables unset (the seam never reads them)."""
    from twin import fake_binding  # the seam module, importable as the plugin process imports it

    return {
        _bind.PORTS_ENV: seam or fake_binding.SEAM,
        fake_binding.STATE_ENV: str(state),
        _bind.DOCKER_PATH_ENV: None,
    }


def host_environ(seam: str | None = None) -> dict[str, str | None]:
    """A HOST node's operator environment: the gate's endpoint and pins as exported, the seam
    only when a HOST case plants something in the real binding (`fake_binding.real_*`)."""
    return {_bind.PORTS_ENV: seam}


def run_terminal(
    host: mcp_host.McpHost, env: str, services: list[str] | None = None
) -> dict[str, Any]:
    """THE one call: `run(reference_env, completion="terminal")`; MC-12 counts it once."""
    sent = host.request_count()
    answer = host.call(
        "run",
        {
            "plugin": PLUGIN_NAME,
            "args": {"env": env, "services": services or [tree.POSTGRES_SERVICE]},
            "wait_ms": tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert host.request_count() == sent + 1, "one request, counted once by the MCP host"
    assert isinstance(answer, dict), answer
    note_passed(answer)
    return answer


# Every passed run this test session made through the harness, by run id (L.RB-12.6: the WR-OWN-3
# container-half falsifier checks each of them, not only its own).
PASSED_RUNS: list[str] = []


def note_passed(answer: Mapping[str, Any]) -> None:
    body = answer.get("answer")
    if isinstance(body, Mapping) and body.get("outcome") == "passed" and "run_id" in answer:
        PASSED_RUNS.append(str(answer["run_id"]))


def run_dir(host: mcp_host.McpHost, run_id: str) -> Path:
    (found,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
    return found


def lane(run_dir_: Path) -> list[dict[str, Any]]:
    rows = records.lane_rows(run_dir_)
    assert not rows.problems and not rows.torn, rows.problems
    return [row.entry for row in rows.rows]


def dispositions(answer: Mapping[str, Any]) -> dict[str, str | None]:
    """Every listed vertex's resource disposition, by its path (`started`, `reused`, ...)."""
    body = answer["answer"]
    return {".".join(node["path"]): node["disposition"] for node in body["listed"]}


def container_released(answer: Mapping[str, Any]) -> bool:
    """RunView.cleanup (MC-32) reports the created container released.

    Only `released` is read. The owned `stop` entry that released it carries the same `ArgvRelease`
    descriptor, and the host sweep may run an `ArgvRelease` only when its executable is on
    `OperatorLimits.release_executables`, which the server cannot load from its configuration yet
    (the reference operator config lists docker, `trestle/server/config.py` reads no `[operator]`
    table: B-SPINE-RETURN item 4). Until that is wired, that entry is swept `unknown` and the answer
    is not `clean`; this helper does not hide it, it only does not decide it (B-HOST1-RETURN)."""
    return int(answer["answer"]["cleanup"]["released"]) >= 1


def claim_precedes_create(entries: list[dict[str, Any]], effect: str = tree.UP) -> bool:
    """MC-10: every create's claim (`issue`) is written before its confirmation, once per path."""
    paths = {e.get("path") for e in entries if (e["class"], e.get("effect")) == ("issue", effect)}
    if not paths:
        return False
    for path in paths:
        classes = [(e["class"], e.get("effect")) for e in entries if e.get("path") == path]
        issued = [i for i, c in enumerate(classes) if c == ("issue", effect)]
        confirmed = [i for i, c in enumerate(classes) if c == ("confirmation", effect)]
        if not (len(issued) == 1 and len(confirmed) == 1 and issued[0] < confirmed[0]):
            return False
    return True


def selector_prefix(run_id: str) -> str:
    """The run-scoped selector prefix of every container the run created (MC-B-01)."""
    return f"{SELECTOR_PREFIX}{run_id}-"


def events(run_dir_: Path) -> list[dict[str, Any]]:
    path = run_dir_ / "evidence" / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
