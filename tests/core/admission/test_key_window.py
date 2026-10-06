"""v0.4 "Fix first": an idempotency key's window is sized from the run's own deadline.

`key_expires_at = admitted_at + deadline_s + finalization_margin + idempotency_ttl_s`, with
`deadline_s` the plugin's declared deadline (`deadline_of`), never the 300 s snapshot default.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from trestle.common import clock
from trestle.common.types import AdmitRequest
from trestle.server.idempotency import IdempotencyStore
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import create_kernel
from trestle.server.recovery import find_run_dir

DEADLINE_S = 1234
TTL_S = 77

PLUGIN = f"""
from trestle.plugin import Context, trestle


@trestle(deadline={DEADLINE_S})
def long_gate(ctx: Context, n: int = 1) -> dict[str, int]:
    return {{"n": n}}
"""


def test_key_expiry_is_deadline_plus_margin_plus_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRESTLE_IDEMPOTENCY_TTL_S", str(TTL_S))
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "long_gate.py").write_text(PLUGIN, encoding="utf-8")
    kernel = create_kernel(home=tmp_path / "home", plugin_dirs=[plugins], skip_recovery=True)
    result = kernel.control.admission.admit(
        AdmitRequest(plugin="long_gate", args={"n": 1}, idempotency_key="gate:part-1")
    )
    assert result.tag == "admitted", result
    run_dir = find_run_dir(kernel.home, result.run_id)
    assert run_dir is not None
    created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert created is not None
    created_at = datetime.fromisoformat(str(created["at"]).replace("Z", "+00:00")).timestamp()
    record = IdempotencyStore.open(kernel.home).lookup("gate:part-1")
    assert record is not None and record.run_id == result.run_id
    want = DEADLINE_S + clock.finalization_margin + TTL_S
    # the created row's time is whole seconds (truncated), so the difference is within 1 s
    assert abs((record.expires_at - created_at) - want) <= 1.0, record.expires_at - created_at
