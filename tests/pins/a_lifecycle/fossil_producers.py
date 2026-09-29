"""Producers for the 16 lane-A S0 fossil states (L.P0-1A.5; MC-11).

Each `produce_<state>(home)` builds one committed S0 run directory under
`home` by driving today's kernel (never a hand-typed ledger, except where a
state is by definition a truncation or a corruption of a real run and says
so). `python -m tests.pins.a_lifecycle.fossil_producers generate` runs them
into `tests/fixtures/fossils/s0/<state>/home`, the same layout
`python -m tests.proof.fossils generate --checkpoint s0` writes once the
spine MANIFEST names these producers.

`DECLARED` is each state's declared S0 projection (`kinds`, `terminal`,
`torn`, whether `meta.json` exists); `tests/pins/a_lifecycle/test_fossils.py`
reads every committed fossil back through `tests.proof.records` and compares.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.pins.a_lifecycle import helpers
from tests.proof import ancestry, harness, records, tolerances
from trestle.common.fsutil import atomic_write
from trestle.common.types import RunView
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import Kernel
from trestle.server.recovery import recover_run_dir, seed_interrupted_run
from trestle.server.runs import cancel_flag_path

ROOT = Path(__file__).resolve().parents[3]
FOSSILS_ROOT = ROOT / "tests" / "fixtures" / "fossils"
BAND = "s0"

FULL_KINDS = ["created", "admitted", "started", "execution_ended", "evidence_finalized"]

# state id -> declared projection. `meta` is True iff evidence/meta.json exists.
DECLARED: dict[str, dict[str, Any]] = {
    "created": {"kinds": ["created"], "terminal": None, "torn": False, "meta": False},
    "admitted": {"kinds": ["created", "admitted"], "terminal": None, "torn": False, "meta": False},
    "started": {
        "kinds": ["created", "admitted", "started"],
        "terminal": None,
        "torn": False,
        "meta": False,
    },
    "execution_ended": {
        "kinds": FULL_KINDS[:4],
        "terminal": None,
        "torn": False,
        "meta": True,
    },
    "finalized_no_terminal": {"kinds": FULL_KINDS, "terminal": None, "torn": False, "meta": True},
    "cancelled": {
        "kinds": FULL_KINDS + ["cancelled"],
        "terminal": "cancelled",
        "torn": False,
        "meta": True,
    },
    "timed_out": {
        "kinds": FULL_KINDS + ["timed_out"],
        "terminal": "timed_out",
        "torn": False,
        "meta": True,
    },
    "worker_exit": {
        "kinds": FULL_KINDS + ["worker_exit"],
        "terminal": "worker_exit",
        "torn": False,
        "meta": True,
    },
    "interrupted": {
        "kinds": ["created", "admitted", "started", "evidence_finalized", "interrupted"],
        "terminal": "interrupted",
        "torn": False,
        "meta": True,
    },
    "partial_limits": {
        "kinds": ["created", "admitted", "started", "limit_exceeded"]
        + FULL_KINDS[3:]
        + ["succeeded"],
        "terminal": "succeeded",
        "torn": False,
        "meta": True,
    },
    "too_large": {
        "kinds": FULL_KINDS + ["succeeded"],
        "terminal": "succeeded",
        "torn": False,
        "meta": True,
    },
    "invalid_synthesized": {
        "kinds": FULL_KINDS + ["succeeded"],
        "terminal": "succeeded",
        "torn": False,
        "meta": True,
    },
    "torn_tail": {"kinds": FULL_KINDS, "terminal": None, "torn": True, "meta": True},
    "newline_less_tail": {
        "kinds": FULL_KINDS + ["succeeded"],
        "terminal": "succeeded",
        "torn": True,
        "meta": True,
    },
    "artifacts_present": {
        "kinds": ["created", "admitted", "started", "artifact_available"]
        + FULL_KINDS[3:]
        + ["succeeded"],
        "terminal": "succeeded",
        "torn": False,
        "meta": True,
    },
    "idempotency_keyed": {
        "kinds": FULL_KINDS + ["succeeded"],
        "terminal": "succeeded",
        "torn": False,
        "meta": True,
    },
}

STATE_IDS = list(DECLARED)


# -- scaffolding --------------------------------------------------------------


def _kernel(home: Path, plugin: str, *, lane: bool = False) -> Kernel:
    """A kernel over `home` whose only plugin is `plugin` (so the fossil's
    snapshots directory holds one snapshot, not the whole shared catalog)."""
    src_dir = helpers.LANE_PLUGIN_DIR if lane else helpers.SHARED_PLUGIN_DIR
    plugin_dir = Path(tempfile.mkdtemp(prefix="fossil-plugin-"))
    shutil.copy(src_dir / f"{plugin}.py", plugin_dir / f"{plugin}.py")
    return harness.fresh_kernel(plugin_dirs=[plugin_dir], home=home)


@contextmanager
def _env(**values: str) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _run_dir_of_only(home: Path) -> Path:
    dirs = sorted((home / "runs").glob("*/r_*"))
    assert len(dirs) == 1, dirs
    return dirs[0]


def _complete_echo(home: Path, **run_kwargs: Any) -> Path:
    kernel = _kernel(home, "echo")
    view = kernel.control.run(
        plugin="echo", args={"message": "fossil"}, wait_ms=tolerances.HARNESS_WAIT_MS, **run_kwargs
    )
    assert isinstance(view, RunView), view
    return _run_dir_of_only(home)


def _rows(run_dir: Path) -> list[str]:
    return ledger_path(run_dir).read_text(encoding="utf-8").splitlines()


def _truncate_after(run_dir: Path, kind: str) -> None:
    """Keep the ledger rows up to and including the first row of `kind`."""
    kept: list[str] = []
    for line in _rows(run_dir):
        kept.append(line)
        if json.loads(line).get("kind") == kind:
            break
    atomic_write(ledger_path(run_dir), ("\n".join(kept) + "\n").encode("utf-8"))


# -- pre-terminal states: the ledger stops where a killed server would leave it


def produce_created(home: Path) -> None:
    kernel = _kernel(home, "echo")
    helpers.admit_order(kernel, "echo", {"message": "fossil"})


def _admitted(home: Path) -> tuple[Kernel, str]:
    kernel = _kernel(home, "echo")
    order = helpers.admit_order(kernel, "echo", {"message": "fossil"})
    run_dir = _run_dir_of_only(home)
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append("admitted", run_id=order.run_id, snapshot_id=order.snapshot_id)
    return kernel, order.run_id


def produce_admitted(home: Path) -> None:
    _admitted(home)


def produce_started(home: Path) -> None:
    _kernel_, run_id = _admitted(home)
    RunLedger.open(ledger_path(_run_dir_of_only(home))).append("started", run_id=run_id)


def produce_execution_ended(home: Path) -> None:
    run_dir = _complete_echo(home)
    _truncate_after(run_dir, "execution_ended")


def produce_finalized_no_terminal(home: Path) -> None:
    run_dir = _complete_echo(home)
    _truncate_after(run_dir, "evidence_finalized")


# -- terminal states from real runs ---------------------------------------------


def produce_cancelled(home: Path) -> None:
    kernel = _kernel(home, "slow")
    order = helpers.admit_order(kernel, "slow", {"seconds": tolerances.JOIN_WAIT_S * 6})
    run_dir = _run_dir_of_only(home)
    thread = helpers.drive_in_thread(kernel, order)
    try:
        assert helpers.wait_until(
            lambda: (
                (run_dir / "work" / "tmp").exists()
                and "started" in {json.loads(x)["kind"] for x in _rows(run_dir)}
            ),
            tolerances.JOIN_WAIT_S,
        )
        atomic_write(cancel_flag_path(run_dir), b"1")
        thread.join(timeout=tolerances.JOIN_WAIT_S)
        assert not thread.is_alive()
    finally:
        ancestry.reap(helpers.marked(order.run_id))


def produce_timed_out(home: Path) -> None:
    kernel = _kernel(home, "slow")
    with harness.patch_snapshot(kernel, "slow", timeout_s=helpers.SHORT_RUN_TIMEOUT_S):
        order = helpers.admit_order(kernel, "slow", {"seconds": tolerances.JOIN_WAIT_S * 6})
    thread = helpers.drive_in_thread(kernel, order)
    try:
        thread.join(timeout=tolerances.JOIN_WAIT_S + helpers.SHORT_RUN_TIMEOUT_S)
        assert not thread.is_alive()
    finally:
        ancestry.reap(helpers.marked(order.run_id))


def produce_worker_exit(home: Path) -> None:
    kernel = _kernel(home, "exit2", lane=True)
    kernel.control.run(plugin="exit2", args={}, wait_ms=tolerances.HARNESS_WAIT_MS)


def produce_interrupted(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    kernel = _kernel(home, "echo")  # writes service_epoch; no run of its own
    run_dir = seed_interrupted_run(kernel.home, "r_fossil_interrupted", last_kind="started")
    recover_run_dir(run_dir)


def produce_partial_limits(home: Path) -> None:
    with _env(TRESTLE_TEST_LIMITS="1"):
        kernel = _kernel(home, "noisy", lane=True)
        kernel.control.run(plugin="noisy", args={}, wait_ms=tolerances.HARNESS_WAIT_MS)


def produce_too_large(home: Path) -> None:
    with _env(TRESTLE_TEST_LIMITS="1"):
        kernel = _kernel(home, "big_array")
        kernel.control.run(
            plugin="big_array", args={"count": 200_000}, wait_ms=tolerances.HARNESS_WAIT_MS
        )


def produce_artifacts_present(home: Path) -> None:
    kernel = _kernel(home, "outputs_writer")
    kernel.control.run(
        plugin="outputs_writer",
        args={"name": "report.txt", "body": "fossil-output"},
        wait_ms=tolerances.HARNESS_WAIT_MS,
    )


def produce_idempotency_keyed(home: Path) -> None:
    _complete_echo(home, idempotency_key="fossil-key")


# -- synthesized / corrupted states ---------------------------------------------


def produce_invalid_synthesized(home: Path) -> None:
    """result.json present with no result.index (`invalid`): a real echo run
    whose index is removed, with its meta and finalization row rewritten to
    say so (synthesized, not observed)."""
    run_dir = _complete_echo(home)
    evidence = run_dir / "evidence"
    (evidence / "result.index").unlink()
    meta_path = evidence / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["result_state"] = "invalid"
    atomic_write(meta_path, json.dumps(meta, separators=(",", ":")).encode("utf-8"))
    lines = _rows(run_dir)
    rewritten: list[str] = []
    for line in lines:
        row = json.loads(line)
        if row.get("kind") == "evidence_finalized":
            row["result_state"] = "invalid"
        rewritten.append(json.dumps(row, separators=(",", ":")))
    atomic_write(ledger_path(run_dir), ("\n".join(rewritten) + "\n").encode("utf-8"))


def produce_torn_tail(home: Path) -> None:
    """The terminal row's write was interrupted mid-line."""
    run_dir = _complete_echo(home)
    path = ledger_path(run_dir)
    data = path.read_bytes().rstrip(b"\n")
    cut = data.rindex(b"\n") + 1
    last = data[cut:]
    path.write_bytes(data[:cut] + last[: len(last) // 2])


def produce_newline_less_tail(home: Path) -> None:
    """The terminal row is complete JSON but its trailing newline never landed."""
    run_dir = _complete_echo(home)
    path = ledger_path(run_dir)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))


PRODUCERS: dict[str, Callable[[Path], None]] = {
    sid: globals()[f"produce_{sid}"] for sid in STATE_IDS
}


def declared_projection(run_dir: Path) -> dict[str, Any]:
    """The projection `DECLARED` speaks in, read back through the
    independent records seam."""
    rows = records.ledger_rows(run_dir)
    node = records.node_record(run_dir)
    return {
        "kinds": node.kinds,
        "terminal": node.terminal,
        "torn": rows.torn,
        "meta": (run_dir / "evidence" / "meta.json").exists(),
    }


def generate(states: list[str], fossils_root: Path = FOSSILS_ROOT) -> None:
    for sid in states:
        home = fossils_root / BAND / sid / "home"
        if home.exists():
            shutil.rmtree(home)
        home.mkdir(parents=True)
        PRODUCERS[sid](home)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.pins.a_lifecycle.fossil_producers")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--states", default="all")
    gen.add_argument("--fossils-root", default=None)
    args = parser.parse_args(argv)
    states = STATE_IDS if args.states == "all" else [s for s in args.states.split(",") if s]
    unknown = [s for s in states if s not in PRODUCERS]
    if unknown:
        print(f"fossil_producers: unknown state(s) {unknown}")
        return 1
    generate(states, Path(args.fossils_root) if args.fossils_root else FOSSILS_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
