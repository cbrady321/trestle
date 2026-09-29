"""CL-B2 (WR-EVID-8): a declared secret is never persisted (MC-CORE-13).

L.CL-B2.1: admission writes `spec.json` with the declared arguments redacted and delivers the real
values to the plugin in memory; the sentinel is absent from the spec, the ledger and the
idempotency store. Later leaves of this merge extend this file with the write-path scrub and the
host-path scan.

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tests.proof import harness, tolerances
from trestle.common import codes, redact
from trestle.common.canonical import args_hash
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.ledger import run_dir_for
from trestle.server.main import Kernel

SENTINEL = "SENTINEL-clb2-tok-7f3a91c2d4"
NESTED_SENTINEL = "SENTINEL-clb2-pw-52e8b0a6"

# A plugin that declares two secrets (one a dotted path into a dict argument), and reports what it
# received as digests, so the test can prove the real values arrived without printing them.
ECHO_SOURCE = """\
import hashlib
import os
import subprocess
import sys

from trestle.plugin.surface import Context, trestle


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@trestle(secrets=["token", "auth.password"])
def echo_secret(ctx: Context, token: str, auth: dict[str, str], note: str = "") -> dict[str, str]:
    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('TRESTLE_RUN_SECRETS', 'absent'))"],
        capture_output=True, text=True, check=True,
    )
    return {
        "token_digest": _digest(token),
        "password_digest": _digest(auth["password"]),
        "user": auth["user"],
        "note": note,
        "env_after_call": os.environ.get("TRESTLE_RUN_SECRETS", "absent"),
        "env_in_subprocess": child.stdout.strip(),
    }
"""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def kernel_with(tmp_path: Path, source: str, name: str) -> Kernel:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(exist_ok=True)
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    published = kernel.control.publish_plugin(source)
    assert isinstance(published, PublishView), published
    assert published.name == name
    return kernel


def run_ok(kernel: Kernel, plugin: str, args: dict[str, Any], **kw: Any) -> RunView:
    view = kernel.control.run(plugin=plugin, args=args, wait_ms=tolerances.HARNESS_WAIT_MS, **kw)
    assert isinstance(view, RunView), view
    return view


def occurrences(root: Path, needle: str) -> list[str]:
    """Every file under `root` (relative names) whose bytes contain `needle`."""
    raw = needle.encode("utf-8")
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and raw in path.read_bytes()
    )


REAL_ARGS = {
    "token": SENTINEL,
    "auth": {"user": "alice", "password": NESTED_SENTINEL},
    "note": "visible",
}


@pytest.mark.proves(
    "WR-EVID-8",
    "WR-EVID-8:secret-absent-spec-ledger-store",
    "core",
    "core",
    "PROC",
    "CI",
)
def test_declared_secret_not_in_spec_ledger_or_store(tmp_path: Path) -> None:
    kernel = kernel_with(tmp_path, ECHO_SOURCE, "echo_secret")
    view = run_ok(kernel, "echo_secret", REAL_ARGS, idempotency_key="clb2-store-key")
    assert view.state == "succeeded", view
    run_dir = run_dir_for(kernel.home, view.run_id)

    # the plugin received the real values (only their digests are echoed) ...
    result = json.loads((run_dir / "evidence" / "result.json").read_text(encoding="utf-8"))
    assert result["token_digest"] == digest(SENTINEL)
    assert result["password_digest"] == digest(NESTED_SENTINEL)
    assert result["user"] == "alice" and result["note"] == "visible"
    # ... and the environment that carried them is gone before the call, and from its children
    assert result["env_after_call"] == "absent"
    assert result["env_in_subprocess"] == "absent"

    # spec.json carries the redacted arguments, every other argument untouched
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert spec["args"] == {
        "token": redact.REDACTED,
        "auth": {"user": "alice", "password": redact.REDACTED},
        "note": "visible",
    }
    for needle in (SENTINEL, NESTED_SENTINEL):
        assert (run_dir / "evidence" / "spec.json").read_bytes().count(needle.encode()) == 0
        assert (run_dir / "evidence" / "ledger.ndjson").read_bytes().count(needle.encode()) == 0
        store = kernel.home / "idempotency.json"
        assert store.is_file()
        assert store.read_bytes().count(needle.encode()) == 0
        # the whole run directory and the home's index files, the result's digests aside
        assert occurrences(run_dir, needle) == []
        assert occurrences(kernel.home, needle) == []


def test_args_hash_is_over_the_real_intent(tmp_path: Path) -> None:
    """Join semantics survive redaction: the hash is of what the caller asked for, so the same
    key with the same secret joins the run and a different secret is a conflict."""
    kernel = kernel_with(tmp_path, ECHO_SOURCE, "echo_secret")
    first = run_ok(kernel, "echo_secret", REAL_ARGS, idempotency_key="clb2-join-key")
    run_dir = run_dir_for(kernel.home, first.run_id)
    created = [
        json.loads(line)
        for line in (run_dir / "evidence" / "ledger.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
        if '"created"' in line
    ][0]
    assert created["args_hash"] == args_hash(REAL_ARGS)
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert spec["args_hash"] == args_hash(REAL_ARGS) != args_hash(spec["args"])

    joined = run_ok(kernel, "echo_secret", REAL_ARGS, idempotency_key="clb2-join-key")
    assert joined.run_id == first.run_id

    other = {**REAL_ARGS, "token": SENTINEL + "-other"}
    refused = kernel.control.run(
        plugin="echo_secret",
        args=other,
        wait_ms=tolerances.HARNESS_WAIT_MS,
        idempotency_key="clb2-join-key",
    )
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.IDEMPOTENCY_KEY_CONFLICT
    assert SENTINEL not in refused.message


def test_redact_args_and_values_shapes() -> None:
    args = {"a": "x", "b": {"c": ["p", "q"], "d": 1}, "e.f": "dotted-key"}
    declared = ["b.c", "e.f", "missing", "a"]
    assert redact.redact_args(args, declared) == {
        "a": redact.REDACTED,
        "b": {"c": redact.REDACTED, "d": 1},
        "e.f": redact.REDACTED,
    }
    assert args["a"] == "x"  # the input is never modified
    values = redact.secret_values(args, declared)
    assert values == {"a": "x", "b.c": ["p", "q"], "e.f": "dotted-key"}
    assert redact.restore_args(redact.redact_args(args, declared), values) == args
    assert redact.secret_strings(values) == {"x", "p", "q", "dotted-key"}
    # no declaration: the arguments come back equal
    assert redact.redact_args(args, []) == args
