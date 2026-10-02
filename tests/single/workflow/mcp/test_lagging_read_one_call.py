"""L.SL-5.3: a lagging read through the MC-12 MCP host, one `run(completion="terminal")` call.

`lagging_leaf` (tests/fixtures/workflows/lagging_leaf.py) is a published workflow plugin whose
create is accepted and whose readback lags. Whatever the lag, a `ONCE` effect is claimed once:
a lag the wait policy outlasts ends `passed` on one ticket; a lag it does not ends BLOCKED
`execution.effect_unconfirmed`, still on one ticket, never a second claim.

The lane is read through the proof court's own oracle (`tests.proof.records`); every timing bound
comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import mcp_host, records, tolerances
from trestle.common import clock

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "workflows"
FIXTURE = "lagging_leaf"
BEYOND = 10_000  # far more lag than the fixture's wait policy allows polls for
HOST_TIMEOUT_S = float(clock.finalization_margin) + tolerances.JOIN_WAIT_S + 60.0


@contextmanager
def _host(tmp_path: Path) -> Iterator[mcp_host.McpHost]:
    """The fixture published with its leaf declared `Repeat.ONCE` (one source line)."""
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        source = (FIXTURES / f"{FIXTURE}.py").read_text(encoding="utf-8")
        assert source.count("REPEAT = Repeat.SAFE\n") == 1
        source = source.replace("REPEAT = Repeat.SAFE\n", "REPEAT = Repeat.ONCE\n")
        (host.home / "plugins" / f"{FIXTURE}.py").write_text(source, encoding="utf-8")
        yield host


def _terminal(host: mcp_host.McpHost, **args: Any) -> tuple[dict[str, Any], list[Any]]:
    answer = host.call(
        "run",
        {
            "plugin": FIXTURE,
            "args": {"env": "dev", **args},
            "wait_ms": int(tolerances.HARNESS_WAIT_MS),
            "completion": "terminal",
        },
    )
    assert isinstance(answer, dict), answer
    (run_dir,) = sorted((host.home / "runs").glob(f"*/{answer['run_id']}"))
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return answer, lane.rows


def _claims(rows: list[Any]) -> list[dict[str, Any]]:
    return [r.entry for r in rows if r.cls == "issue" and r.entry.get("effect") == "up"]


def test_once_lag_shorter_than_wait_passes_on_one_claim(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        answer, rows = _terminal(host, hide=2, lag=1)
    assert answer["answer"]["outcome"] == "passed", answer
    assert [c["attempt"] for c in _claims(rows)] == [1]


def test_once_lag_beyond_wait_blocks_without_a_second_claim(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        answer, rows = _terminal(host, hide=BEYOND)
    assert answer["answer"]["outcome"] != "passed", answer
    assert answer["answer"]["primary"]["code"] == "execution.effect_unconfirmed", answer
    assert [c["attempt"] for c in _claims(rows)] == [1], "no second claim for a ONCE effect"
