"""Change 2c through the MCP proof host: a one-off command runs through `run(plugin="exec")` with a
bounded answer; under the restricted profile only when listed; a credential declaration on it is
refused at publication. The home is touched only to place the operator's files (config.toml and
the seeded example) before the server starts."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import mcp_host, tolerances
from trestle.common import clock
from trestle.server.init_cmd import seed_exec_source

HOST_TIMEOUT_S = 300.0 + clock.finalization_margin + tolerances.JOIN_WAIT_S


@contextmanager
def _host(tmp_path: Path, config: str = "", *, seed: bool = True) -> Iterator[Any]:
    home = tmp_path / "host-home"
    (home / "plugins").mkdir(parents=True)
    roots = tmp_path / "roots"
    roots.mkdir()
    (home / "config.toml").write_text(
        f'[plugins.env]\nTRESTLE_EXEC_ROOTS = "{roots}"\n\n{config}', encoding="utf-8"
    )
    if seed:
        (home / "plugins" / "exec.py").write_bytes(seed_exec_source())
    with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as host:
        host.roots = roots
        yield host


def _run(host: Any, argv: list[str], **extra: Any) -> dict[str, Any]:
    request = {
        "plugin": "exec",
        "args": {"argv": argv, "cwd": str(host.roots)},
        "wait_ms": 1000,
        "completion": "terminal",
        **extra,
    }
    return dict(host.call("run", request))


def test_exec_runs_a_command(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        script = "print('x' * 100000); import sys; sys.exit(3)"
        view = _run(host, [sys.executable, "-c", script])
        assert view["state"] == "succeeded" and view["answer"]["outcome"] == "passed", view
        summary = view["summary"]
        assert summary["ok"] is False and summary["exit_code"] == 3
        assert summary["error"] is None
        assert summary["stdout_tail"].rstrip("\n").endswith("x")
        assert len(summary["stdout_tail"].encode()) <= 1500
        assert view["truncated"] is False
        window = {"kind": "jsonpath", "expr": "$.output"}
        fetched = host.call("fetch", {"target": f"{view['run_id']}/result", "window": window})
        (output,) = fetched["values"]
        assert output.startswith("art_"), output
        found = host.call(
            "fetch", {"target": output, "window": {"kind": "grep", "pattern": "xxxx"}}
        )
        assert "x" * 100 in json.dumps(found)


def test_exec_timeout_returns_an_answer(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        view = _run(host, [sys.executable, "-c", "import time; time.sleep(60)"], deadline_s=15)
        assert view["state"] == "succeeded", view
        summary = view["summary"]
        assert summary["timed_out"] is True and summary["exit_code"] is None


def test_exec_restricted_needs_listing(tmp_path: Path) -> None:
    restricted = '[profile]\nmode = "restricted"\nallowlist = ["echo"]\n'
    with _host(tmp_path, restricted) as host:
        items = {item["name"]: item for item in host.call("list_plugins")["items"]}
        assert items["exec"]["allowlisted"] is False
        refused = _run(host, ["true"])
        assert refused["code"] == "admission.not_allowlisted", refused
        assert "run_id" not in refused


def test_exec_credentials_refused_at_publication(tmp_path: Path) -> None:
    source = seed_exec_source().decode("utf-8")
    declared = source.replace(
        "@trestle(summary_fields=", '@trestle(credentials=["x"], summary_fields='
    )
    assert declared != source
    with _host(tmp_path, seed=False) as host:
        refused = host.call("publish_plugin", {"source": declared})
        assert refused["code"] == "publication.validation_failed", refused
        assert "never carries a credential" in refused["message"]
    with _host(tmp_path / "folder", seed=False) as host:
        (host.home / "plugins" / "exec.py").write_text(declared, encoding="utf-8")
        listed = host.call("list_plugins")
        names = [item["name"] for item in listed["items"]]
        assert "exec" not in names
        (failed,) = [row for row in listed.get("failed", []) if row["name"] == "exec"]
        assert "never carries a credential" in failed["reason"]
