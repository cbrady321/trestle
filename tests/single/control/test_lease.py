"""L.SL-8.1: the environment lease key at admission, and the lease defined over the ledger.

The key is the canonical JSON of the request argument `declared.env_arg` names (opaque to the
host), recorded in the `created` row and in the plan's `lease_set`; a run holds the lease from its
`created` row until a terminal row or its admitted deadline; there is no lease store file; a
restart rebuilds the same holder set from the ledgers (WR-OWN-8, L.SL-8.2 queues on it)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.single.control import support
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.types import AdmitRequest, AdmitResultAdmitted
from trestle.server import lease
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import Kernel, create_kernel

ENV_PLAIN = """
from __future__ import annotations

from trestle.plugin import Context, trestle


@trestle(env_arg="env")
def envplain(ctx: Context, env: str = "dev", note: str = "") -> dict[str, str]:
    return {"env": env}
"""


def _env_workflow() -> str:
    """The one-leaf workflow plugin, its root keyed on the argument `name` (`env_key_field` equal
    to `env_arg`, the D-b rule)."""
    return (
        support.workflow_source("envwf")
        .replace("@trestle(deadline=", '@trestle(env_arg="name", deadline=')
        .replace("max_attempts=1,", 'max_attempts=1,\n            env_key_field="name",')
    )


def _kernel(tmp_path: Path) -> Kernel:
    return support.make_kernel(
        tmp_path,
        {
            "echo": support.ECHO.read_text(encoding="utf-8"),
            "envplain": ENV_PLAIN,
            "envwf": _env_workflow(),
        },
    )


def _admit(kernel: Kernel, plugin: str, args: dict[str, object] | None = None) -> str:
    result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args=args or {}))
    assert isinstance(result, AdmitResultAdmitted), result
    return result.run_id


def _created(kernel: Kernel, run_id: str) -> dict[str, object]:
    run_dir = support.run_dir_of(kernel, run_id)
    row = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert row is not None
    return row


def _plan(kernel: Kernel, run_id: str) -> AdmittedPlan:
    raw = support.read_spec(support.run_dir_of(kernel, run_id))["plan"]
    return AdmittedPlan.from_json(json.dumps(raw))


def _end(kernel: Kernel, run_id: str, kind: str = "failed") -> None:
    ledger = RunLedger.open(ledger_path(support.run_dir_of(kernel, run_id)))
    ledger.append(kind, run_id=run_id)


def test_key_from_env_arg_opaque(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    plain = _admit(kernel, "envplain", {"env": "prod", "note": "a"})
    same = _admit(kernel, "envplain", {"env": "prod", "note": "b"})
    other = _admit(kernel, "envplain", {"env": "staging"})
    tree = _admit(kernel, "envwf", {"name": "prod"})

    # opaque bytes: the canonical JSON of the argument value, whatever else the request carries
    assert _created(kernel, plain)["lease_key"] == '"prod"'
    assert _created(kernel, same)["lease_key"] == '"prod"'
    assert _created(kernel, other)["lease_key"] == '"staging"'
    assert _plan(kernel, plain).lease_set == ('"prod"',)
    assert _plan(kernel, other).lease_set == ('"staging"',)
    # a declared tree's compiled lease_set and the created row agree
    assert _plan(kernel, tree).lease_set == ('"prod"',)
    assert _created(kernel, tree)["lease_key"] == '"prod"'

    # a plugin that declares no environment admits as before: no key, no lease
    echo = _admit(kernel, "echo", {"message": "hi"})
    assert "lease_key" not in _created(kernel, echo)
    assert _plan(kernel, echo).lease_set == ()
    assert echo not in {h.run_id for h in kernel.control.admission.holders.snapshot()}

    # an environment plugin whose request names no environment holds nothing
    unnamed = _admit(kernel, "envplain", {})
    assert "lease_key" not in _created(kernel, unnamed)
    assert _plan(kernel, unnamed).lease_set == ()

    # no lease store file: the lease is defined over the ledgers
    assert not [p for p in kernel.home.rglob("*") if "lease" in p.name.lower()]


def test_lease_definition_over_ledger(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    holders = kernel.control.admission.holders
    run_id = _admit(kernel, "envplain", {"env": "prod"})
    run_dir = support.run_dir_of(kernel, run_id)

    held = lease.holder_of(run_dir)
    assert held is not None and held.run_id == run_id and held.key == '"prod"'
    assert holders.held('"prod"') == (held,)
    assert holders.held('"staging"') == ()

    # the deadline ends the lease (wall clock, the admitted deadline)
    assert lease.holder_of(run_dir, now=held.deadline_epoch - 1) == held
    assert lease.holder_of(run_dir, now=held.deadline_epoch) is None
    assert holders.held('"prod"', now=held.deadline_epoch + 1) == ()

    # a terminal row ends it, before anything else is recorded (OQ-34 fixes the row, not the sweep)
    second = _admit(kernel, "envplain", {"env": "prod"})
    assert {h.run_id for h in holders.held('"prod"')} == {run_id, second}
    _end(kernel, run_id, "timed_out")
    assert lease.holder_of(run_dir) is None
    assert [h.run_id for h in holders.held('"prod"')] == [second]

    # no created row, no lease
    empty = tmp_path / "empty-run"
    (empty / "evidence").mkdir(parents=True)
    assert lease.holder_of(empty) is None


def test_lease_rebuilt_after_restart(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    first = _admit(kernel, "envplain", {"env": "prod"})
    second = _admit(kernel, "envwf", {"name": "prod"})
    third = _admit(kernel, "envplain", {"env": "staging"})
    _admit(kernel, "echo", {"message": "hi"})
    ended = _admit(kernel, "envplain", {"env": "prod", "note": "ended"})
    _end(kernel, ended, "cancelled")
    before = kernel.control.admission.holders.snapshot()
    assert {h.run_id for h in before} == {first, second, third}

    # a restart without recovery: the ledgers alone give back the identical holder set, in order
    restarted = create_kernel(
        home=kernel.home, plugin_dirs=[tmp_path / "plugin-src"], skip_recovery=True
    )
    after = restarted.control.admission.holders
    assert after.snapshot() == before
    assert [h.run_id for h in after.held()] == sorted([first, second, third])
    assert [h.run_id for h in after.held('"prod"')] == sorted([first, second])
    assert lease.rebuild_holders(kernel.home).snapshot() == before

    # a restart with recovery ends every unfinished run at a terminal row: nothing is held
    recovered = create_kernel(home=kernel.home, plugin_dirs=[tmp_path / "plugin-src"])
    assert recovered.control.admission.holders.snapshot() == frozenset()
