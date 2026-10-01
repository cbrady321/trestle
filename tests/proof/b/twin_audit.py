"""MC-B-03 twin audit and the node collector the checkpoint reads (L.RB-0.5).

Two roles in one module, neither a test (never matches `test_*.py`, DM-80):

* pytest plugin (`-p tests.proof.b.twin_audit`): under `TRESTLE_COLLECT_OUT` writes every collected
  item's nodeid, the label ids the root plugin resolved for it, whether it carries
  `docker_host` / `host_only`, and the `[tier, venue]` each of its `proves()` markers declares per
  label or matrix id (`marked`: a clause part's venue lives only in its marker);
* pure functions over those node dicts: `twin_nodeid`, `twin_problems`, `adversary_problems`.

HOST selection (CSC-9) deselects `docker_host` nodes unless `TRESTLE_HOST_GATE=docker` and
`host_only` nodes unless `proc`, so `collect_nodes` collects once per gate and merges.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests.proof import fence as fence_mod
from tests.proof import plugin as plugin_mod
from tests.proof.transcribe import MATRIX_CLAUSE_RE

ROOT = Path(__file__).resolve().parents[3]
COLLECT_ENV = "TRESTLE_COLLECT_OUT"
DEFAULT_PATHS = ("tests", "packages/trestle-packs/tests", "packages/trestle-env/tests")
_NODES: list[dict[str, Any]] = []


def _arg(mark: pytest.Mark, index: int, name: str) -> Any:
    return mark.args[index] if len(mark.args) > index else mark.kwargs.get(name)


def marked(item: pytest.Item) -> dict[str, list[list[str]]]:
    """Label or matrix id -> the `[tier, venue]` of every `proves(row, clause, slice, step, tier,
    venue)` marker on `item` naming it (the clause, else the row, as the root plugin keys it)."""
    out: dict[str, list[list[str]]] = {}
    for mark in item.iter_markers(name="proves"):
        key = _arg(mark, 1, "clause") or _arg(mark, 0, "row")
        if key is None:
            continue
        pair = [str(_arg(mark, 4, "tier") or ""), str(_arg(mark, 5, "venue") or "")]
        if pair not in out.setdefault(str(key), []):
            out[str(key)].append(pair)
    return out


def pytest_collection_finish(session: pytest.Session) -> None:
    _NODES.clear()
    for item in session.items:
        _NODES.append(
            {
                "nodeid": item.nodeid,
                "labels": list(plugin_mod._ITEM_LABELS.get(item.nodeid, [])),  # noqa: SLF001
                "docker_host": item.get_closest_marker("docker_host") is not None,
                "host_only": item.get_closest_marker("host_only") is not None,
                "marked": marked(item),
            }
        )


def pytest_sessionfinish(session: pytest.Session) -> None:
    out = os.environ.get(COLLECT_ENV)
    if out:
        Path(out).write_text(json.dumps(_NODES))


def collect_nodes(paths: list[str] | None = None, root: Path | None = None) -> list[dict[str, Any]]:
    """Every node under `paths` (default: the test roots that exist), collected under each HOST
    gate value so that `docker_host` and `host_only` nodes are both seen. A collection that fails
    raises: an unreadable tree is never an empty one."""
    root = root or ROOT
    chosen = list(paths) if paths is not None else [p for p in DEFAULT_PATHS if (root / p).is_dir()]
    if not chosen:
        return []
    merged: dict[str, dict[str, Any]] = {}
    for gate in ("docker", "proc"):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nodes.json"
            env = dict(os.environ, **{COLLECT_ENV: str(out), "TRESTLE_HOST_GATE": gate})
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", __name__, *chosen],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=fence_mod.HOST_RUN_MAX,
            )
            if not out.exists():
                raise RuntimeError(
                    f"collection under TRESTLE_HOST_GATE={gate} failed: "
                    f"{proc.stdout[-400:]}{proc.stderr[-400:]}"
                )
            for node in json.loads(out.read_text()):
                merged.setdefault(node["nodeid"], node)
    return list(merged.values())


# ---------------------------------------------------------------------------
# the audit (pure)
# ---------------------------------------------------------------------------

_PARAM = re.compile(r"^(?P<base>[^\[]*)(?:\[(?P<params>.*)\])?$")


def twin_nodeid(host: str) -> str | None:
    """The CI twin's node id of a HOST node id (MC-B-03), or `None` when the id has neither shape:

    * an adapter family's `[real...]` parametrization -> the same function's `[fake]` case;
    * `.../host/test_x.py::t` -> `.../twin/test_x_twin.py::t` (parameters kept)."""
    match = _PARAM.match(host)
    assert match is not None
    base, params = match.group("base"), match.group("params")
    if params is not None and params.split("-")[0] == "real":
        return f"{base}[fake]"
    path, sep, rest = base.partition("::")
    parts = path.split("/")
    if sep and len(parts) >= 2 and parts[-2] == "host" and re.match(r"^test_.+\.py$", parts[-1]):
        parts[-2] = "twin"
        parts[-1] = parts[-1][: -len(".py")] + "_twin.py"
        return "/".join(parts) + sep + rest + (f"[{params}]" if params is not None else "")
    return None


def is_clause(label_id: str) -> bool:
    """A matrix clause or part id (`B4.6`, `A1.1:core`), not a row label."""
    return bool(MATRIX_CLAUSE_RE.match(label_id.split(":", 1)[0]))


def twin_problems(
    nodes: list[dict[str, Any]], labels: dict[str, dict[str, Any]], suffix: str
) -> list[str]:
    """Every docker_host node without a well-formed twin (MC-B-03): a twin exists; registers
    `<L><suffix>` for every row label `L` its HOST node registers; registers no matrix clause; and
    every twin label is declared `stub_proven`."""
    by_id = {n["nodeid"]: n for n in nodes}
    out: list[str] = []
    for host in sorted((n for n in nodes if n.get("docker_host")), key=lambda n: n["nodeid"]):
        host_id = host["nodeid"]
        twin_id = twin_nodeid(host_id)
        if twin_id is None:
            out.append(f"{host_id}: not under .../host/test_x.py and not a [real] case: no twin id")
            continue
        twin = by_id.get(twin_id)
        if twin is None:
            out.append(f"{host_id}: twin {twin_id} is not collected")
            continue
        if twin.get("docker_host"):
            out.append(f"{twin_id}: a twin is a CI node, not a docker_host node")
        twin_labels = set(twin["labels"])
        for label in sorted(host["labels"]):
            if is_clause(label) or label.endswith(suffix):
                continue
            if label + suffix not in twin_labels:
                out.append(f"{twin_id}: does not register {label + suffix}")
        for label in sorted(twin_labels):
            if is_clause(label):
                out.append(f"{twin_id}: a twin registers no matrix clause, found {label}")
            elif label.endswith(suffix) and labels.get(label, {}).get("posture") != "stub_proven":
                out.append(f"{twin_id}: twin label {label} is not declared stub_proven")
    return out


def adversary_problems(
    nodes: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    suffix: str,
    clauses: tuple[str, ...],
) -> list[str]:
    """DM-29: a node that registers one of the adversary-stub clauses (B4.5, B9.1) registers no
    STUB label (a `stub_proven` posture label or a twin label)."""
    out: list[str] = []
    for node in nodes:
        hit = [c for c in clauses if c in node["labels"]]
        stubs = [
            lb
            for lb in node["labels"]
            if labels.get(lb, {}).get("posture") == "stub_proven" or lb.endswith(suffix)
        ]
        if hit and stubs:
            out.append(f"{node['nodeid']}: registers {hit[0]} and STUB label {stubs[0]}")
    return out
