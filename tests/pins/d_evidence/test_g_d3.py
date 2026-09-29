"""G-D3 (BFD-35): `last_error`'s message is the terminal classification
("failed"), not the exception the plugin raised."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pins.d_evidence.plugins.sentinel_fail import SENTINEL
from tests.proof import harness
from tests.proof.markers import TargetUnmet, target_check

PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"


def _last_error_message() -> str:
    kernel = harness.fresh_kernel([PLUGIN_DIR])
    run_dir = harness.run_to_dir(kernel, "sentinel_fail", {})
    out = kernel.control.query("last_error", {"run_id": run_dir.name})
    assert isinstance(out, dict), out
    assert len(out["items"]) == 1
    return str(out["items"][0]["message"])


@pytest.mark.pin("G-D3")
def test_pin_last_error_message_is_failed() -> None:
    assert _last_error_message() == "failed"


@pytest.mark.target("G-D3")
@pytest.mark.proves("WR-EVID-1", "A9.1", "core", "core", "must", "CI")
@pytest.mark.proves("WR-EVID-1", "WR-EVID-1:sentinel-in-last-error", "core", "core", "must", "CI")
@pytest.mark.xfail(strict=True, raises=TargetUnmet, reason="defect:G-D3")
def test_target_last_error_contains_exception_message() -> None:
    message = _last_error_message()
    target_check(
        SENTINEL in message,
        "G-D3",
        f"last_error message {message!r} does not contain the raised sentinel",
    )
