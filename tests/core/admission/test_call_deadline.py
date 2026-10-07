"""v0.3.1 Feature 0: per-task deadlines above one hour.

`run(deadline_s=...)` is the run's effective deadline (else the plugin's declared one, else 300 s),
bounded by `[operator] deadline_ceiling_s` (config.toml only, read at each admission, 3,600 by
default, at most 86,400). It is recorded in spec.json and the created row with its source, shown by
the run view, joined on by a key as the call's own argument, and used by the key window and the
terminal wait bound. `describe_plugin` adds the margin and the ceiling.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from trestle.common import clock, codes
from trestle.common.types import AdmitRequest, RunView
from trestle.server import idempotency
from trestle.server.config import DEADLINE_CEILING_MAX_S, load_config
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path
from trestle.server.main import Kernel, create_kernel
from trestle.server.recovery import find_run_dir

DECLARED_S = 900
TTL_S = 77
CEILING_S = 3600  # the default ceiling
OVER_S = CEILING_S + 1
LONG_S = 7200
OTHER_S = LONG_S + 100
MID_S = 5000
HIGH_CEILING_S = 10800
OVER_LONG_S = HIGH_CEILING_S + 1
LOW_CEILING_S = 500
OVER_LOW_S = LOW_CEILING_S + 1

DECLARED = f"""
from trestle.plugin import Context, trestle


@trestle(deadline={DECLARED_S})
def declared_gate(ctx: Context, n: int = 1) -> dict[str, int]:
    return {{"n": n}}
"""

UNDECLARED = """
from trestle.plugin import Context, trestle


@trestle
def plain_gate(ctx: Context, n: int = 1) -> dict[str, int]:
    return {"n": n}
"""


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    monkeypatch.setenv("TRESTLE_IDEMPOTENCY_TTL_S", str(TTL_S))
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "declared_gate.py").write_text(DECLARED, encoding="utf-8")
    (plugins / "plain_gate.py").write_text(UNDECLARED, encoding="utf-8")
    return create_kernel(home=tmp_path / "home", plugin_dirs=[plugins], skip_recovery=True)


def _ceiling(kernel: Kernel, seconds: object) -> None:
    (kernel.home / "config.toml").write_text(
        f"[operator]\ndeadline_ceiling_s = {seconds}\n", encoding="utf-8"
    )


def _admit(kernel: Kernel, plugin: str = "declared_gate", **fields: Any) -> Any:
    return kernel.control.admission.admit(AdmitRequest(plugin=plugin, args={"n": 1}, **fields))


def _runs(kernel: Kernel) -> list[Path]:
    root = kernel.home / "runs"
    return sorted(root.glob("*/*")) if root.exists() else []


def _created(kernel: Kernel, run_id: str) -> dict[str, Any]:
    run_dir = find_run_dir(kernel.home, run_id)
    assert run_dir is not None
    created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert created is not None
    return created


def test_deadline_above_the_ceiling_is_refused_before_a_run_id(kernel: Kernel) -> None:
    assert load_config(kernel.home).operator_limits.deadline_ceiling == 3600.0  # the default
    refused = _admit(kernel, deadline_s=OVER_S, idempotency_key="k")
    assert refused.tag == "refused"
    assert refused.outcome.code == codes.BUDGET_DOES_NOT_FIT
    assert "3601" in refused.outcome.message and "3600" in refused.outcome.message
    assert _runs(kernel) == [] and idempotency.lookup(kernel.home, "k") is None
    assert _admit(kernel, deadline_s=CEILING_S).tag == "admitted"  # at the ceiling is fine
    # zero and below hold no run; they are refused the same way, and nothing is minted
    for seconds in (0, -5):
        assert _admit(kernel, deadline_s=seconds).outcome.code == codes.BUDGET_DOES_NOT_FIT
    not_a_number = _admit(kernel, deadline_s="soon")
    assert not_a_number.outcome.code == codes.INVALID_ARGS
    assert len(_runs(kernel)) == 1


def test_ceiling_comes_from_config_toml_at_each_admission_without_a_restart(
    kernel: Kernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRESTLE_DEADLINE_CEILING_S", "99999")  # no environment override
    assert _admit(kernel, deadline_s=LONG_S).outcome.code == codes.BUDGET_DOES_NOT_FIT
    _ceiling(kernel, HIGH_CEILING_S)
    assert _admit(kernel, deadline_s=LONG_S).tag == "admitted"  # same kernel, no restart
    assert _admit(kernel, deadline_s=OVER_LONG_S).outcome.code == codes.BUDGET_DOES_NOT_FIT
    _ceiling(
        kernel, LOW_CEILING_S
    )  # lowered: both admission sites follow it, a declared deadline too
    assert _admit(kernel, deadline_s=OVER_LOW_S).outcome.code == codes.BUDGET_DOES_NOT_FIT
    declared = _admit(kernel, deadline_s=None)
    assert declared.tag == "refused" and "declared deadline" in declared.outcome.message
    assert _admit(kernel, "plain_gate").tag == "admitted"  # the 300 s default still fits
    assert len(_runs(kernel)) == 2


def test_the_ceiling_is_checked_at_load(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    for good in (1, 3600, DEADLINE_CEILING_MAX_S):
        (home / "config.toml").write_text(f"[operator]\ndeadline_ceiling_s = {good}\n")
        assert load_config(home).operator_limits.deadline_ceiling == float(good)
    for bad in (DEADLINE_CEILING_MAX_S + 1, 0, -1, '"1h"', "true"):
        (home / "config.toml").write_text(f"[operator]\ndeadline_ceiling_s = {bad}\n")
        with pytest.raises(ValueError, match="deadline_ceiling_s"):
            load_config(home)


def test_effective_deadline_is_recorded_with_its_source_and_shown(kernel: Kernel) -> None:
    _ceiling(kernel, HIGH_CEILING_S)
    cases = {
        "call": (_admit(kernel, deadline_s=LONG_S), LONG_S),
        "declared": (_admit(kernel), DECLARED_S),
        "default": (_admit(kernel, "plain_gate"), 300),
    }
    for source, (result, seconds) in cases.items():
        assert result.tag == "admitted", (source, result)
        run_dir = find_run_dir(kernel.home, result.run_id)
        assert run_dir is not None
        spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
        assert spec["timeout_s"] == seconds
        stamped = datetime.fromisoformat(
            _created(kernel, result.run_id)["at"].replace("Z", "+00:00")
        )
        assert (
            abs(
                datetime.fromisoformat(spec["deadline"]).timestamp() - stamped.timestamp() - seconds
            )
            <= 1
        )
        created = _created(kernel, result.run_id)
        assert (created["deadline_s"], created["deadline_source"]) == (seconds, source)
        view = kernel.control.project.status(result.run_id)
        assert isinstance(view, RunView)
        assert (view.deadline_s, view.deadline_source) == (seconds, source)
        wire = view.to_dict()
        assert (wire["deadline_s"], wire["deadline_source"]) == (seconds, source)


def test_key_joins_on_the_calls_own_deadline_argument(kernel: Kernel) -> None:
    _ceiling(kernel, HIGH_CEILING_S)
    first = _admit(kernel, deadline_s=LONG_S, idempotency_key="gate")
    assert first.tag == "admitted" and not first.existing
    assert _created(kernel, first.run_id)["call_deadline_s"] == 7200
    entry = idempotency.read_entries(kernel.home, "gate")[0]
    assert (entry.call_deadline_s, entry.deadline_s) == (7200.0, 7200.0)
    again = _admit(kernel, deadline_s=LONG_S, idempotency_key="gate")
    assert again.tag == "admitted" and again.existing and again.run_id == first.run_id
    other = _admit(kernel, deadline_s=OTHER_S, idempotency_key="gate")
    assert other.outcome.code == codes.IDEMPOTENCY_KEY_CONFLICT
    # omitted is not the declared value: the argument is compared, not the effective deadline
    omitted = _admit(kernel, idempotency_key="gate")
    assert omitted.outcome.code == codes.IDEMPOTENCY_KEY_CONFLICT
    keyed = _admit(kernel, idempotency_key="declared")
    assert _created(kernel, keyed.run_id)["call_deadline_s"] is None
    same_value = _admit(kernel, deadline_s=DECLARED_S, idempotency_key="declared")
    assert same_value.outcome.code == codes.IDEMPOTENCY_KEY_CONFLICT
    assert len(_runs(kernel)) == 2


def test_key_window_and_terminal_wait_use_the_effective_deadline(kernel: Kernel) -> None:
    _ceiling(kernel, HIGH_CEILING_S)
    result = _admit(kernel, deadline_s=LONG_S, idempotency_key="long")
    assert result.tag == "admitted"
    created = _created(kernel, result.run_id)
    at = datetime.fromisoformat(created["at"].replace("Z", "+00:00")).timestamp()
    want = 7200 + clock.finalization_margin + TTL_S
    assert abs((created["key_expires_at"] - at) - want) <= 1.0
    bound = kernel.control.project._terminal_bound_s(result.run_id)
    assert abs(bound - (7200 + clock.finalization_margin)) <= 2.0


def test_environment_busy_precheck_uses_the_effective_deadline(
    kernel: Kernel,
) -> None:
    """The busy pre-check compares the holder's recorded deadline with this request's would-be
    deadline: the call's, not the declared one (`_environment_busy`)."""
    from trestle.server.admission import Admission

    seen: list[float] = []
    admission: Admission = kernel.control.admission
    original = admission._environment_busy

    def spy(plan: Any, deadline_s: float, state: Any) -> Any:
        seen.append(deadline_s)
        return original(plan, deadline_s, state)

    admission._environment_busy = spy  # type: ignore[method-assign]
    _ceiling(kernel, HIGH_CEILING_S)
    assert _admit(kernel, deadline_s=MID_S).tag == "admitted"
    assert _admit(kernel).tag == "admitted"
    assert seen == [5000.0, float(DECLARED_S)]


def test_describe_plugin_adds_the_margin_and_the_ceiling(kernel: Kernel) -> None:
    described = kernel.control.describe_plugin("declared_gate")
    assert isinstance(described, dict)
    assert described["finalization_margin_s"] == clock.finalization_margin
    assert described["deadline_ceiling_s"] == 3600.0
    assert described["deadline_s"] == DECLARED_S  # the declaration, as before
    assert described["max_call_duration_s"] == DECLARED_S + clock.finalization_margin
    _ceiling(kernel, 7200)
    again = kernel.control.describe_plugin("declared_gate")
    assert isinstance(again, dict) and again["deadline_ceiling_s"] == 7200.0
