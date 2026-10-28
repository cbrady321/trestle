"""The host-docker gate's engine inventory and the WR-PROOF-6 diff (L.NW-2.1; MC-B-02 inventory
half, MC-27 keys `inventory_before` / `inventory_after`, B3-C17 (3)).

`snapshot` is read-only: through the CLI's absolute path and the gate's explicit endpoint
(`--host <endpoint>`, I-4) it lists containers (`ps -a`), images (`image ls`), volumes, networks
and the engine (`info`), JSON. It starts, stops, reconfigures and pulls nothing (WR-CON-3, MC-13).

`compute_diff` applies CSC-10's attribution rule: a change is ATTRIBUTABLE iff the object's name
carries a run-scoped selector prefix `trwr-<root run_id>-` (MC-B-01; a run id is
`r_<lowercase base32>`, so the prefix is `trwr-[a-z0-9_]+-`) or the object carries the Docker label
`trestle.proof.fixture=<fixture id>`. Attributable changes are RESIDUE (counted, recorded, removed
by `run`'s housekeeping); any other difference is UNATTRIBUTED, and a stopped or changed engine is
`engine_state_changed`; either fails the record.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

FIXTURE_LABEL = "trestle.proof.fixture"
SELECTOR_PREFIX = "trwr-"
# `trwr-<root run_id>-<path>`; `<root run_id>` is `r_<base32>` (lowercase alnum and `_`).
SELECTOR_RE = re.compile(r"^trwr-[a-z0-9_]+-")

KINDS = ("containers", "images", "volumes", "networks")

Runner = Callable[[list[str], Mapping[str, str]], Any]


class InventoryError(RuntimeError):
    """A read command failed although the engine answered `info`."""


def docker_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """`os.environ` without the variables that would compete with the explicit `--host`."""
    env = dict(os.environ if base is None else base)
    env.pop("DOCKER_HOST", None)
    env.pop("DOCKER_CONTEXT", None)
    return env


def docker_cmd(docker_bin: str, endpoint: str | None, *args: str) -> list[str]:
    """`<docker> [--host <endpoint>] <args…>`: every docker call the gate makes goes through this
    (I-4: an explicit endpoint, never the `default` context's absent socket)."""
    return [docker_bin, *(["--host", endpoint] if endpoint else []), *args]


def default_runner(argv: list[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    """Bounded by the caller (the gate runs inside `HOST_RUN_MAX`), never by a literal here."""
    return subprocess.run(  # noqa: S603
        argv, capture_output=True, text=True, env=dict(env), stdin=subprocess.DEVNULL, check=False
    )


def _json_lines(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def parse_labels(text: str | Mapping[str, str] | None) -> dict[str, str]:
    """docker's `{{json .}}` renders `Labels` as `k=v,k2=v2`."""
    if not text:
        return {}
    if isinstance(text, Mapping):
        return {str(k): str(v) for k, v in text.items()}
    out: dict[str, str] = {}
    for part in text.split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            out[key] = value
        elif part:
            out[part] = ""
    return out


def _container(row: Mapping[str, Any]) -> dict[str, Any]:
    names = [n for n in str(row.get("Names", "")).split(",") if n]
    return {
        "id": row.get("ID", ""),
        "names": names,
        "image": row.get("Image", ""),
        "state": row.get("State", ""),
        "labels": parse_labels(row.get("Labels")),
    }


def _image(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("ID", ""),
        "repository": row.get("Repository", ""),
        "tag": row.get("Tag", ""),
    }


def _volume(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": row.get("Name", ""), "labels": parse_labels(row.get("Labels"))}


def _network(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("ID", ""),
        "name": row.get("Name", ""),
        "labels": parse_labels(row.get("Labels")),
    }


def unreachable_snapshot(error: str) -> dict[str, Any]:
    return {
        "engine": {"reachable": False, "server_version": None, "id": None, "error": error},
        **{kind: [] for kind in KINDS},
    }


def snapshot(
    docker_bin: str,
    endpoint: str | None,
    *,
    runner: Runner | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The engine inventory. A stopped or unreachable engine (or a docker that cannot be executed)
    is a snapshot with `engine.reachable == False`, never an exception: the record decides."""
    run = runner or default_runner
    environ = docker_env() if env is None else env

    calls_failed: list[str] = []

    def call(*args: str) -> str | None:
        try:
            done = run(docker_cmd(docker_bin, endpoint, *args), environ)
        except OSError as exc:
            calls_failed.append(f"{args[0]}: {exc}")
            return None
        if done.returncode != 0:
            calls_failed.append(f"{args[0]}: exit {done.returncode}: {done.stderr[:200]}")
            return None
        return done.stdout

    info_text = call("info", "--format", "{{json .}}")
    if info_text is None:
        return unreachable_snapshot(calls_failed[0])
    try:
        info = json.loads(info_text)
        ps = call("ps", "-a", "--no-trunc", "--format", "{{json .}}")
        images = call("image", "ls", "--no-trunc", "--format", "{{json .}}")
        volumes = call("volume", "ls", "--format", "{{json .}}")
        networks = call("network", "ls", "--no-trunc", "--format", "{{json .}}")
        if calls_failed:
            return unreachable_snapshot("; ".join(calls_failed))
        return {
            "engine": {
                "reachable": True,
                "server_version": info.get("ServerVersion"),
                "id": info.get("ID"),
            },
            "containers": [_container(r) for r in _json_lines(ps or "")],
            "images": [_image(r) for r in _json_lines(images or "")],
            "volumes": [_volume(r) for r in _json_lines(volumes or "")],
            "networks": [_network(r) for r in _json_lines(networks or "")],
        }
    except (ValueError, TypeError) as exc:  # docker answered with something that is not JSON
        return unreachable_snapshot(f"unparseable inventory output: {exc}")


def object_names(obj: Mapping[str, Any]) -> list[str]:
    if "names" in obj:
        return [str(n).lstrip("/") for n in obj["names"]]
    return [str(obj["name"]).lstrip("/")] if obj.get("name") else []


def is_attributable(obj: Mapping[str, Any]) -> bool:
    """CSC-10: the run-scoped selector prefix in a name, or the fixture label."""
    if any(SELECTOR_RE.match(name) for name in object_names(obj)):
        return True
    return bool(obj.get("labels", {}).get(FIXTURE_LABEL))


def _key(kind: str, obj: Mapping[str, Any]) -> str:
    if kind == "images":
        return f"{obj['id']}|{obj.get('repository')}:{obj.get('tag')}"
    if kind == "volumes":
        return str(obj["name"])
    return str(obj["id"])


def _entry(kind: str, change: str, obj: Mapping[str, Any]) -> dict[str, Any]:
    names = object_names(obj)
    return {
        "kind": kind,
        "change": change,
        "id": obj.get("id") or obj.get("name"),
        "name": names[0] if names else (obj.get("repository") or ""),
    }


def engine_state_changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    b, a = before["engine"], after["engine"]
    if not b.get("reachable") or not a.get("reachable"):
        return True
    return b.get("server_version") != a.get("server_version") or b.get("id") != a.get("id")


def compute_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """`{unattributed: [...], residue: [...], engine_state_changed: bool}`. The record stores
    `diff = {unattributed, engine_state_changed}` and `residue` beside it (MC-B-02)."""
    unattributed: list[dict[str, Any]] = []
    residue: list[dict[str, Any]] = []
    for kind in KINDS:
        b = {_key(kind, o): o for o in before.get(kind, [])}
        a = {_key(kind, o): o for o in after.get(kind, [])}
        changes: list[tuple[str, Mapping[str, Any]]] = []
        changes += [("added", a[k]) for k in a if k not in b]
        changes += [("removed", b[k]) for k in b if k not in a]
        changes += [("changed", a[k]) for k in a if k in b and a[k] != b[k]]
        for change, obj in changes:
            (residue if is_attributable(obj) else unattributed).append(_entry(kind, change, obj))
    return {
        "unattributed": unattributed,
        "residue": residue,
        "engine_state_changed": engine_state_changed(before, after),
    }


def diff_failed(diff: Mapping[str, Any]) -> bool:
    """Any non-attributable difference, or a stopped/changed engine, fails the record."""
    return bool(diff["unattributed"]) or bool(diff["engine_state_changed"])
