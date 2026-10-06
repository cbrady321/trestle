"""CK-1 (K-1; MC-12, MC-16, MC-17, MC-30, MC-CORE-12; OQ-1 recorded default): the same idempotency
key joins the run it named after the plugin was republished, and the key's window covers the run's
whole life. v0.4 removed the `admission.JOIN_ACROSS_REPUBLISH` switch: K-1 is permanent, and the
key's window is sized from the run's own deadline ("Fix first").

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, tolerances

PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"
ROOT = Path(__file__).resolve().parents[3]
KEY = "ck-1-key"
WAIT_MS = tolerances.HARNESS_WAIT_MS
# The idempotency ttl of the window test: one settle unit, in whole seconds.
TTL_S = int(tolerances.SETTLE_LONG_S)
# A run that is still going well after the ttl, and the pause that lets the ttl pass.
HOLD_S = tolerances.SETTLE_LONG_S * 4
PAST_TTL_S = TTL_S + tolerances.SETTLE_LONG_S


def _install_counter(host: mcp_host.McpHost) -> Path:
    shutil.copy(PLUGIN_DIR / "counter.py", host.home / "plugins")
    return host.home / "plugins" / "counter.py"


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _republish(host: mcp_host.McpHost, plugin_source: Path, tmp_path: Path) -> str:
    """Edit the plugin's source (no semantic change), then run it once under another key on a
    counter of its own: the answer's snapshot id is the republished one. Returns that id."""
    plugin_source.write_text(
        plugin_source.read_text(encoding="utf-8") + "\n# republished\n", encoding="utf-8"
    )
    probe = host.call(
        "run",
        {
            "plugin": "counter",
            "args": {"counter_file": str(tmp_path / "probe-counter")},
            "idempotency_key": "ck-1-probe",
            "wait_ms": WAIT_MS,
        },
    )
    assert probe["state"] == "succeeded", probe
    return _snapshot_id(probe)


def _snapshot_id(view: dict[str, Any]) -> str:
    return str(view["outcome"]["identity"]["snapshot_id"])


@pytest.mark.proves("WR-IDEM-1", "A3.1", "A", "core", "MCP+PROC", "CI")
@pytest.mark.proves("WR-IDEM-1", "WR-IDEM-1:join-after-republish", "core", "core", "PROC", "CI")
def test_republish_then_same_key_joins_counter_once(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    args = {"plugin": "counter", "args": {"counter_file": str(counter)}, "idempotency_key": KEY}
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        source = _install_counter(host)
        first = host.call("run", {**args, "wait_ms": WAIT_MS})
        assert first["state"] == "succeeded", first
        assert _lines(counter) == 1

        republished = _republish(host, source, tmp_path)
        assert republished != _snapshot_id(first), "the republish was not picked up"

        again = host.call("run", {**args, "wait_ms": WAIT_MS})
        assert "code" not in again, again
        assert again["run_id"] == first["run_id"]
        assert _lines(counter) == 1


@pytest.mark.proves("WR-IDEM-1", "WR-IDEM-1:window-covers-run-life", "core", "core", "MCP", "CI")
def test_run_longer_than_window_still_joins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRESTLE_IDEMPOTENCY_TTL_S", str(TTL_S))
    args = {"plugin": "slow", "args": {"seconds": HOLD_S}, "idempotency_key": KEY}
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        first = host.call("run", {**args, "wait_ms": 0})
        assert "run_id" in first, first
        # the run outlives the ttl the key was minted with; the window still covers it
        time.sleep(PAST_TTL_S)
        again = host.call("run", {**args, "wait_ms": 0})
        assert "code" not in again, again
        assert again["run_id"] == first["run_id"]
        done = host.call("await_runs", {"run_ids": [first["run_id"]], "timeout_ms": WAIT_MS * 3})
        views = done["result"] if isinstance(done, dict) and "result" in done else done
        assert [view["state"] for view in views] == ["succeeded"], done


@pytest.mark.proves("WR-IDEM-1", "WR-IDEM-1:answer-names-identity", "core", "core", "MCP", "CI")
@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-1", "core", "core", "INSPECT", "CI")
def test_joined_answer_names_snapshot_that_ran(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    args = {"plugin": "counter", "args": {"counter_file": str(counter)}, "idempotency_key": KEY}
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        source = _install_counter(host)
        first = host.call("run", {**args, "wait_ms": WAIT_MS})
        original = _snapshot_id(first)
        current = _republish(host, source, tmp_path)
        assert current != original

        joined = host.call("run", {**args, "wait_ms": WAIT_MS})
        assert joined["run_id"] == first["run_id"], joined
        # the joined answer names the code that ran, not the code the plugin has now
        assert _snapshot_id(joined) == original
        assert _snapshot_id(joined) != current
        assert _lines(counter) == 1

    # the K-1 knowing change is documented in its delimited block (MC-05); the markers are spelled
    # in two pieces so this file is not itself a carrier of the block
    text = (ROOT / "docs" / "agents.md").read_text(encoding="utf-8")
    opening, closing = "<!-- K" + "-1 -->", "<!-- /K" + "-1 -->"
    assert text.count(opening) == 1 and text.count(closing) == 1
    body = text[text.index(opening) : text.index(closing)]
    assert "JOIN_ACROSS_REPUBLISH" in body and "outcome.identity.snapshot_id" in body
