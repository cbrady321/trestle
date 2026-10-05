"""G-D3 (BFD-35), flipped by L.CS-3.3: `last_error`'s message is the explanation the ledger's
`error_record` row carries (the exception the plugin raised), not the terminal classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pins.d_evidence.plugins.sentinel_fail import SENTINEL
from tests.proof import harness
from tests.proof.markers import target_check

PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"


def _last_error_message() -> str:
    kernel = harness.fresh_kernel([PLUGIN_DIR])
    run_dir = harness.run_to_dir(kernel, "sentinel_fail", {})
    out = kernel.control.query("last_error", {"run_id": run_dir.name})
    assert isinstance(out, dict), out
    assert len(out["items"]) == 1
    return str(out["items"][0]["message"])


@pytest.mark.proves("WR-EVID-1", "A9.1", "core", "core", "must", "CI")
@pytest.mark.proves("WR-EVID-1", "WR-EVID-1:sentinel-in-last-error", "core", "core", "must", "CI")
def test_target_last_error_contains_exception_message() -> None:
    message = _last_error_message()
    target_check(
        SENTINEL in message,
        "G-D3",
        f"last_error message {message!r} does not contain the raised sentinel",
    )
