"""L.RB-9.6: the demo secret is captured nowhere (WR-EVID-12, WR-REMEDY-4, WR-PROOF-3; D-9: AWS is
DEMO ONLY, never real; OQ-26 gated).

A real run of a plugin that composes the tree's `CredentialUnit` over the real `DemoGrant` and the
stub issuer on loopback (the issuer a separate thread of this process, the plugin a worker process
of the kernel). The plugin makes the secret available where a credential usually is: it fetches the
demo token from the issuer's one secret-bearing route and puts it in its own process environment.
Then it runs the tree. The run's directory (lane, ledger, evidence, work and outputs), the run's
answer and the host's own view are searched byte by byte for the token, its nonce and its prefix,
in three scenes: a healthy credential, an interactive identity (blocked with a named human action)
and a lifetime the refresh cannot extend. The credential facts the record holds are only identity,
expiry and generation (WR-EVID-12); a planted copy of the token in the run directory is found by
the same search (the search has teeth)."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from tests.proof import harness, records
from trestle.common.types import RunView
from trestle.server.ledger import run_dir_for
from trestle.server.main import Kernel
from twin import harness as twin_harness

REPO = Path(__file__).resolve().parents[4]
STUBS = REPO / "tests" / "fixtures" / "stubs"
PLUGIN_NAME = "credential_env"
LABEL_SECRET = "WR-EVID-12:secret-never-captured"
LABEL_SAFE = "WR-REMEDY-4:b-credential-refresh-safe"
LABEL_DEMO = "WR-PROOF-3:b-aws-demo-label"
LABEL_HOSTWIDE = "WR-ENV-13:host-wide-credential-refresh"

PLUGIN = '''\
"""A plugin that runs the reference tree's credential unit over the demo grant (test-only)."""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Mapping
from datetime import timedelta

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    LoopFlags,
    Repeat,
    WorkflowEntry,
)
from trestle.workflow.loop import Loop
from trestle_env import tree
from trestle_packs.grant import DemoGrant

ISSUER = "@ISSUER@"

ENTRY = WorkflowEntry(
    root="credential_env",
    units={
        "credential_env": AllDeclaration(
            unit="credential_env",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(ChildBinding(unit=tree.CREDENTIAL_UNIT, params={}, needs=()),),
            concurrency=1,
            budget=timedelta(seconds=100),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        tree.CREDENTIAL_UNIT: tree.CredentialUnit(),
    },
    deadline=timedelta(seconds=120),
)


@trestle(deadline=120, env_arg="env", packages=("trestle_env", "trestle_packs"))
def credential_env(ctx: Context, env: str) -> Mapping[str, str]:
    grant = DemoGrant(ISSUER)
    with urllib.request.urlopen(ISSUER + "/credential") as reply:  # the ONE route with the secret
        os.environ["AWS_DEMO_SESSION_TOKEN"] = json.load(reply)["token"]
    Loop(ctx.run_services, ENTRY, {"env": env}, grant.as_map()).run()  # binds DemoHostScope
    return {"env": env}
'''


def load_issuer() -> ModuleType:
    """`tests/fixtures/stubs/stub_issuer.py`, loaded once by absolute path (a stub is a fixture, not
    a package, and is never found by a search)."""
    name = "stub_issuer"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, STUBS / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def secrets_of(issuer: Any) -> list[str]:
    """Every string that is or contains the secret: the nonce, the token prefix, and the token of
    each generation the issuer has issued."""
    state, prefix = issuer.state, load_issuer().TOKEN_PREFIX
    return [state.nonce, f"{prefix}:", *(f"{prefix}:{g}:{state.nonce}" for g in state._issued)]  # noqa: SLF001


def hits(where: Path, needles: list[str]) -> list[str]:
    """The files under `where` whose bytes contain any secret."""
    found = []
    for path in sorted(where.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            if any(n.encode() in data for n in needles):
                found.append(str(path.relative_to(where)))
    return found


def run_terminal(tmp_path: Path, issuer: Any) -> tuple[Path, RunView]:
    """One terminal run of the credential plugin over `issuer`; its run directory and view."""
    plugins = tmp_path / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "credential_env.py").write_text(
        PLUGIN.replace("@ISSUER@", issuer.url), encoding="utf-8"
    )
    kernel: Kernel = harness.fresh_kernel(plugin_dirs=[plugins])
    view = kernel.control.run(PLUGIN_NAME, {"env": "demo"}, wait_ms=180_000, completion="terminal")
    assert isinstance(view, RunView), view
    return run_dir_for(kernel.home, view.run_id), view


def _facts_in(value: Any) -> list[dict[str, Any]]:
    """Every credential currency fact (`subject == demo_credential`) anywhere in a decoded row."""
    if isinstance(value, dict):
        own = [value] if value.get("subject") == "demo_credential" else []
        return own + [f for v in value.values() for f in _facts_in(v)]
    if isinstance(value, list):
        return [f for v in value for f in _facts_in(v)]
    return []


def credential_facts(run_dir: Path) -> set[str]:
    """The keys of every recorded fact that names a credential, in the lane and the evidence
    events (identity is the human action's and the confirmation's; the fact carries the
    generation and the expiry)."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    decoded: list[Any] = [row.entry for row in lane.rows]
    events = run_dir / "evidence" / "events.ndjson"
    if events.exists():
        decoded += [json.loads(line) for line in events.read_text().splitlines() if line]
    return {key for row in decoded for fact in _facts_in(row) for key in fact}


ALLOWED = {"subject", "observed_generation", "valid_until", "older"}  # generation, expiry (+ tags)


@pytest.mark.proves("WR-EVID-12", LABEL_SECRET, "B", "B", "STUB+PROC", "CI")
@pytest.mark.proves("WR-REMEDY-4", LABEL_SAFE, "B", "B", "STUB+PROC", "CI")
@pytest.mark.proves("WR-PROOF-3", LABEL_DEMO, "B", "B", "STUB+PROC", "CI")
@pytest.mark.proves("WR-ENV-13", LABEL_HOSTWIDE, "B", "B", "STUB+PROC", "CI")
@pytest.mark.gated_on("OQ-26")
def test_secret_absent_from_run_dir_ledger_artifacts_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", twin_harness.plugin_pythonpath())
    stub = load_issuer()
    scenes: dict[str, dict[str, Any]] = {
        "healthy": {},
        "interactive": {"interactive": True},
        "short_lifetime": {"lifetime_s": 60.0},  # a refresh cannot extend it: the node blocks
    }
    seen_outcomes: dict[str, str] = {}
    for name, state in scenes.items():
        with stub.StubIssuer(**state) as issuer:
            run_dir, view = run_terminal(tmp_path / name, issuer)
            needles = secrets_of(issuer)
            assert len(needles) >= 3  # the nonce, the prefix and the token the plugin fetched
            # the run directory (lane, ledger, evidence, work, outputs) holds no secret byte
            assert hits(run_dir, needles) == [], (name, hits(run_dir, needles))
            # nor does the answer the host gave, nor its view of the run
            shown = json.dumps(dataclasses.asdict(view), default=str)
            assert not any(n in shown for n in needles), name
            # what the record holds of the credential is identity, expiry and generation only
            recorded = credential_facts(run_dir)
            assert recorded <= ALLOWED, (name, recorded)
            if name == "interactive":  # no usable credential: no fact, and the identity is named
                assert recorded == set() and state.get("identity", "demo-user") in shown
            else:  # the generation of the credential the run read (the record keeps no more)
                assert "observed_generation" in recorded, (name, recorded)
            seen_outcomes[name] = str(view.state)
            # the search has teeth: the same token planted in the run directory is found
            plant = run_dir / "work" / "planted.txt"
            plant.parent.mkdir(exist_ok=True)
            plant.write_text(needles[-1], encoding="utf-8")
            assert hits(run_dir, needles) == ["work/planted.txt"]
    # three different runs ended, and none of them was a run of nothing
    assert len(seen_outcomes) == 3
