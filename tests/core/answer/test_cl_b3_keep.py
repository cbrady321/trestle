"""CL-B3 (L.CL-B3.2; WR-OWN-5, WR-AUTH-4): no keep request exists this delivery, so every one an
agent might send is refused before a run id exists, and `run` has no keep or cleanup parameter.

A falsifier only: nothing here changes the product. A keep request is an argument outside the
published schema (B2-C2 "Keep request (RT-5)", B4-C6). Two carriers exist for an extra argument:

* inside `args` (the plugin's declared input): admission's schema validation refuses it with
  `admission.invalid_args` (`trestle/common/codes.py:6`), before a run id;
* beside `args` as an extra `run` argument: the tool's own published schema refuses it at the MCP
  layer, before the call reaches the kernel (a tool error naming the argument, no `RequestOutcome`).

Both leave no run directory, ledger, idempotency or other record, and start no process (MC-13);
neither is silently downgraded to a run without the request.

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.proof import ancestry, mcp_host
from trestle.common import codes

# The spellings a client would try, as extra `run` arguments: name -> value.
KEEP_PROCESSES: list[dict[str, Any]] = [
    {"keep_processes": True},
    {"keep_local_processes": True},
    {"keep_alive": True},
    {"keep": "processes"},
    {"keep": True},
    {"keep_running": True},
    {"cleanup": "keep_processes"},
    {"cleanup": "none"},
]
KEEP_CONTAINERS: list[dict[str, Any]] = [
    {"keep_containers": True},
    {"keep_container": True},
    {"keep_resources": True},
    {"keep": "containers"},
    {"cleanup": "keep_containers"},
    {"cleanup": False},
    {"cleanup_policy": "keep"},
]

# `run`'s published properties: the S0 set (MC-16 adds `completion` and nothing else).
S0_RUN_PROPERTIES = {"plugin", "args", "version", "wait_ms", "idempotency_key"}
RUN_PROPERTIES = S0_RUN_PROPERTIES | {"completion", "deadline_s", "idempotency_ttl_s", "after"}

# A property or enum value that names a keep or cleanup choice.
KEEP_WORDS = ("keep", "cleanup", "clean_up", "retain", "preserve", "persist", "release", "teardown")


def keep_or_cleanup_names(schema: Any) -> list[str]:
    """Every property name and enum value in a JSON schema (recursively) that names a keep or
    cleanup choice."""
    found: list[str] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for prop, sub in value.items():
                        if any(word in prop.lower() for word in KEEP_WORDS):
                            found.append(f"{where}.{prop}")
                        walk(sub, f"{where}.{prop}")
                elif key == "enum" and isinstance(value, list):
                    for member in value:
                        if isinstance(member, str) and any(w in member.lower() for w in KEEP_WORDS):
                            found.append(f"{where}={member}")
                else:
                    walk(value, where)
        elif isinstance(node, list):
            for item in node:
                walk(item, where)

    walk(schema, "$")
    return found


def call_raw(host: mcp_host.McpHost, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """A `tools/call` result exactly as the server framed it (so `isError` stays visible)."""
    message = json.loads(host._await_response(host.hold(name, arguments)))
    assert "result" in message, message
    return message["result"]


def tools(host: mcp_host.McpHost) -> dict[str, dict[str, Any]]:
    message = json.loads(host.tools_list_raw())
    return {tool["name"]: tool for tool in message["result"]["tools"]}


def home_files(home: Path) -> set[str]:
    return {str(p.relative_to(home)) for p in home.rglob("*")}


def run_dirs(home: Path) -> set[str]:
    return {p.name for p in (home / "runs").glob("*/*")}


def server_children(host: mcp_host.McpHost) -> set[int]:
    return {p.pid for p in ancestry.snapshot() if p.ppid == host.proc.pid}


class Quiet:
    """The state a refused request must leave untouched: every path under TRESTLE_HOME (a run
    directory, ledger, spec or idempotency record would add one), the run directories, and the
    server's child processes (MC-13)."""

    def __init__(self, host: mcp_host.McpHost) -> None:
        self.host = host
        self.files = home_files(host.home)
        self.runs = run_dirs(host.home)
        self.children = server_children(host)

    def assert_unchanged(self, request: dict[str, Any]) -> None:
        assert home_files(self.host.home) == self.files, f"a record was written for {request}"
        assert run_dirs(self.host.home) == self.runs, f"a run exists for {request}"
        assert server_children(self.host) == self.children, f"a process started for {request}"


def refused_by_schema_layer(result: dict[str, Any], argument: str) -> bool:
    """The MCP layer's refusal of an argument the tool's schema does not publish."""
    text = " ".join(b.get("text", "") for b in result.get("content", []))
    return bool(result.get("isError")) and argument in text and "run_id" not in text


@pytest.mark.proves(
    "WR-OWN-5", "WR-OWN-5:keep-processes-refused", "core", "core", "MCP+LOGIC", "CI"
)
@pytest.mark.proves(
    "WR-OWN-5", "WR-OWN-5:keep-containers-refused", "core", "core", "MCP+LOGIC", "CI"
)
def test_keep_request_refused_invalid_args_before_run_id(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        # a request without keep behaves as before: a run, a run id, a terminal answer
        baseline = host.call("run", {"plugin": "echo", "args": {"message": "plain"}})
        assert baseline["state"] == "succeeded", baseline
        assert baseline["run_id"]
        quiet = Quiet(host)

        for request in KEEP_PROCESSES + KEEP_CONTAINERS:
            argument = next(iter(request))

            # carrier 1: an extra `run` argument, refused by the published tool schema
            result = call_raw(
                host, "run", {"plugin": "echo", "args": {"message": "kept"}, **request}
            )
            assert refused_by_schema_layer(result, argument), (request, result)
            quiet.assert_unchanged(request)

            # carrier 2: inside the plugin's `args`, refused by admission's schema validation
            answer = host.call("run", {"plugin": "echo", "args": {"message": "kept", **request}})
            assert answer["code"] == codes.INVALID_ARGS == "admission.invalid_args", answer
            assert answer["origin"] == "admission", answer
            assert "run_id" not in answer, answer
            assert answer["retryable"] is False, answer
            quiet.assert_unchanged(request)

        # the refusals never downgraded to a run without the request: one run exists, the baseline
        (only,) = sorted((host.home / "runs").glob("*/*"))
        assert only.name == baseline["run_id"]

        # and the surface still answers a request without keep, identically
        again = host.call("run", {"plugin": "echo", "args": {"message": "plain"}})
        assert again["state"] == "succeeded", again
        assert set(again) == set(baseline), (sorted(again), sorted(baseline))


@pytest.mark.proves("WR-AUTH-4", "WR-AUTH-4:cleanup-closed-set", "core", "core", "MCP+LOGIC", "CI")
def test_run_schema_has_no_keep_or_cleanup_parameter(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        listed = tools(host)
        # `completion` is the one additive parameter (MC-16): a validated set, not a free string
        for spelling in ("keep_processes", "cleanup", "keep", ""):
            answer = host.call("run", {"plugin": "echo", "completion": spelling})
            assert answer["code"] == codes.INVALID_ARGS, (spelling, answer)
            assert "run_id" not in answer, answer
        assert not (host.home / "runs").exists() or not list((host.home / "runs").glob("*/*"))

    run_schema = listed["run"]["inputSchema"]
    assert set(run_schema["properties"]) == RUN_PROPERTIES
    assert set(run_schema["required"]) == {"plugin"}

    # no tool carries a keep or cleanup parameter, and none names a keep or cleanup choice as a
    # value (B4-C6)
    for name, tool in listed.items():
        assert keep_or_cleanup_names(tool["inputSchema"]) == [], name

    # every agent-selectable `run` value is a closed or validated set (the plugin name by the
    # registry, `args` by the plugin's schema, `completion` by the surface: checked above) and none
    # selects cleanup of a found resource: `completion` chooses when the answer is given
    props = run_schema["properties"]
    assert _has_type(props["completion"], "string"), props["completion"]
    for name in ("plugin", "version", "idempotency_key"):
        assert _has_type(props[name], "string"), (name, props[name])
    assert _has_type(props["wait_ms"], "integer"), props["wait_ms"]
    assert _has_type(props["args"], "object"), props["args"]

    # the checker sees a planted `keep` property, and a planted keep enum value
    planted = json.loads(json.dumps(run_schema))
    planted["properties"]["keep"] = {"type": "boolean"}
    assert keep_or_cleanup_names(planted) == ["$.keep"]
    planted = json.loads(json.dumps(run_schema))
    planted["properties"]["completion"] = {"enum": ["bounded", "keep_processes"]}
    assert keep_or_cleanup_names(planted) == ["$.completion=keep_processes"]


def _has_type(prop: dict[str, Any], kind: str) -> bool:
    options = prop.get("anyOf", [prop])
    return any(option.get("type") == kind for option in options)
