"""L.SL-8.3: the WR-OWN-8 falsifier over the lane, the OQ-29 and OQ-26 variants, the unconfirmed
path (not claimed), and the D-b publication rule re-verified through the MCP host.

The falsifier reads each run's lane (the proof court's oracle, `tests.proof.records`): a run's
mutation interval runs from its first APPLIED claim to its last APPLIED release, and two runs of
one environment must never hold overlapping intervals. The fixture `env_leaf` (one leaf over the
fake marker) declares its environment (`env_arg` = `env_key_field`); the waiter is a copy with a
later declared deadline, because admission refuses a same-deadline request behind a holder whose
release walk it could not outlast (`admission.environment_busy`, L.SL-8.2). The key is plugin
independent (L.SL-8.1), so the two plugins share one environment when the request's `env` is equal.

Variants that an open question leaves open are written both ways and never counted as a pass:
OQ-29 (does the exclusion reach a root that declines to declare its environment), OQ-26 (is
host-wide credential and toolchain state serialized across environments). Today's build reads
OQ-29 as *not covered* and OQ-26 as *no host-wide serialization*."""

from __future__ import annotations

import json
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support as spine
from tests.proof import harness, mcp_host, records, tolerances
from tests.single.control import support
from trestle.common import codes
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.types import RunView
from trestle.server.main import Kernel

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "env_leaf.py"
HOLDER_DEADLINE_S = 60  # env_leaf.py's own literal
LATE_DEADLINE_S = 300  # the waiter's: it needs the holder's release walk to fit before its own
RUN_BOUND_S = tolerances.JOIN_WAIT_S * 6
LABELS = Path(__file__).resolve().parents[2] / "proof" / "labels.d" / "single.toml"

Interval = tuple[datetime, datetime]


def _variant(source: str, name: str, *, deadline_s: int = HOLDER_DEADLINE_S) -> str:
    """A copy of the fixture published under `name`, with its own declared deadline."""
    source = source.replace("def env_leaf(", f"def {name}(")
    source = source.replace(f"deadline={HOLDER_DEADLINE_S}", f"deadline={deadline_s}")
    return source.replace(f"seconds={HOLDER_DEADLINE_S}", f"seconds={deadline_s}")


def _undeclared(source: str, name: str) -> str:
    """The same root with no environment declared and its ports imported dynamically: the
    publication rule (D-b) reads only the plugin's own import statements, so this is the case F-7
    reports, a plugin that declines to declare and that no host mechanism detects."""
    source = _variant(source, name)
    source = source.replace('@trestle(deadline=60, env_arg="env")', "@trestle(deadline=60)")
    source = source.replace('env_key_field="env",', "")
    source = source.replace(
        "from trestle_packs.fakes import FakeMarker\n",
        "import importlib\n\n_fakes = importlib.import_module('trestle_packs.fakes')\n"
        "FakeMarker = _fakes.FakeMarker\n",
    )
    head, _, rest = source.partition("from trestle.workflow.ports import (")
    names, _, tail = rest.partition(")\n")
    bound = "".join(
        f"{n.strip()} = _ports.{n.strip()}\n"
        for n in names.replace("\n", "").split(",")
        if n.strip()
    )
    return head + "_ports = importlib.import_module('trestle.workflow.ports')\n" + bound + tail


def _plugins(tmp_path: Path, **sources: str) -> Path:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    for name, source in sources.items():
        (plugin_dir / f"{name}.py").write_text(source, encoding="utf-8")
    return plugin_dir


def _kernel(tmp_path: Path, **sources: str) -> Kernel:
    return harness.fresh_kernel(plugin_dirs=[_plugins(tmp_path, **sources)])


def _start(kernel: Kernel, plugin: str, **args: Any) -> Path:
    view = kernel.control.run(plugin, dict(args), wait_ms=0)
    assert isinstance(view, RunView), view
    return spine.run_dir_of(kernel, view.run_id)


def _terminal(run_dir: Path) -> str | None:
    return records.node_record(run_dir).terminal


def _wait_all(*run_dirs: Path) -> None:
    assert spine.wait_until(lambda: all(_terminal(d) for d in run_dirs), RUN_BOUND_S), [
        spine.kinds(d) for d in run_dirs
    ]
    assert [_terminal(d) for d in run_dirs] == ["succeeded"] * len(run_dirs)


def _at(text: object) -> datetime:
    assert isinstance(text, str), text
    return datetime.fromisoformat(text)


def interval(run_dir: Path) -> Interval:
    """The run's mutation interval: its first APPLIED claim to its last APPLIED release, read from
    the lane alone (a claim is stamped `issued_at`, a release `released_at`)."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    issued = {
        (r.entry["effect"], r.entry["attempt"]): r.entry for r in lane.rows if r.cls == "issue"
    }
    applied = [
        _at(issued[(r.entry["effect"], r.entry["attempt"])]["issued_at"])
        for r in lane.rows
        if r.cls == "confirmation" and r.entry["status"] == "applied"
    ]
    released = [_at(r.entry["released_at"]) for r in lane.rows if r.cls == "released"]
    assert applied, "the run applied no effect: there is nothing to compare"
    return min(applied), max([*applied, *released])


def overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _two_runs(
    kernel: Kernel, first: tuple[str, dict[str, Any]], second: tuple[str, dict[str, Any]]
) -> tuple[Path, Path]:
    """Start two runs back to back and wait for both to end; the processes of both are reaped."""
    one = _start(kernel, first[0], **first[1])
    try:
        two = _start(kernel, second[0], **second[1])
    except BaseException:
        spine.ancestry.reap(spine.marked(one.name))
        raise
    with spine.reaping(one.name), spine.reaping(two.name):
        _wait_all(one, two)
    return one, two


def _source() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.mark.proves("WR-OWN-8", "WR-OWN-8:environment-lease", "core", "single", "PROC", "CI")
def test_two_runs_one_env_no_overlapping_mutations(tmp_path: Path) -> None:
    source = _source()
    kernel = _kernel(
        tmp_path,
        env_leaf=source,
        env_leaf_late=_variant(source, "env_leaf_late", deadline_s=LATE_DEADLINE_S),
    )
    first, second = _two_runs(
        kernel, ("env_leaf", {"env": "prod"}), ("env_leaf_late", {"env": "prod"})
    )
    one, two = interval(first), interval(second)
    # the later run waited for the earlier one: its first mutation follows the other's release
    assert one[1] <= two[0], (one, two)
    assert not overlaps(one, two)
    kinds = spine.kinds(second)
    assert kinds.index("created") < kinds.index("started")
    # the falsifier has teeth: two runs of different environments do hold overlapping intervals
    other_a, other_b = _two_runs(
        kernel, ("env_leaf", {"env": "east"}), ("env_leaf_late", {"env": "west"})
    )
    assert overlaps(interval(other_a), interval(other_b))


@pytest.mark.gated_on("OQ-29")
def test_undeclared_env_excluded_variant(tmp_path: Path) -> None:
    """OQ-29, the strong reading: a root that declines to declare its environment is still
    excluded, its two runs never overlapping. Not delivered (an undeclared root holds no lease and
    no host mechanism detects the omission, design F-7), so the variant is the strict xfail below;
    it is never counted as a pass."""
    kernel = _kernel(tmp_path, env_undeclared=_undeclared(_source(), "env_undeclared"))
    first, second = _two_runs(
        kernel, ("env_undeclared", {"env": "prod"}), ("env_undeclared", {"env": "prod"})
    )
    assert not overlaps(interval(first), interval(second))


test_undeclared_env_excluded_variant = pytest.mark.xfail(
    strict=True, reason="variant:OQ-29=excluded (not delivered, F-7)"
)(test_undeclared_env_excluded_variant)


@pytest.mark.gated_on("OQ-29")
def test_undeclared_env_not_covered_variant(tmp_path: Path) -> None:
    """OQ-29, the narrow reading: a root that declines to declare its environment is admitted,
    holds no lease and is not covered: two runs of it are admitted at once and their mutation
    intervals overlap (what today's build does)."""
    kernel = _kernel(tmp_path, env_undeclared=_undeclared(_source(), "env_undeclared"))
    first, second = _two_runs(
        kernel, ("env_undeclared", {"env": "prod"}), ("env_undeclared", {"env": "prod"})
    )
    for run_dir in (first, second):
        created = records.ledger_rows(run_dir).rows[0]
        assert "lease_key" not in created, "an undeclared root took a lease"
    assert overlaps(interval(first), interval(second))


@pytest.mark.gated_on("OQ-26")
def test_host_wide_serialization_variant(tmp_path: Path) -> None:
    """OQ-26: how host-wide credential and toolchain state is serialized across roots is open. The
    reading the interface stage assumed (a host-scoped section held only around a refresh or an
    install, never a lease) is what runs here: a run's lease set is exactly its own environment,
    so two roots on different environments are not serialized against each other."""
    source = _source()
    kernel = _kernel(
        tmp_path,
        env_leaf=source,
        env_leaf_late=_variant(source, "env_leaf_late", deadline_s=LATE_DEADLINE_S),
    )
    first, second = _two_runs(
        kernel, ("env_leaf", {"env": "one"}), ("env_leaf_late", {"env": "two"})
    )
    for run_dir, env in ((first, "one"), (second, "two")):
        spec = support.read_spec(run_dir)["plan"]
        assert AdmittedPlan.from_json(json.dumps(spec)).lease_set == (f'"{env}"',)
    assert overlaps(interval(first), interval(second))


@pytest.mark.na("OQ-34 answered 2026-09-26: exclusion not claimed on the unconfirmed-stop path")
def test_unconfirmed_path_registered_na() -> None:
    """The one path WR-OWN-8 does not cover: a stopped run whose processes cannot be confirmed
    gone still ends its lease at its terminal answer, and the answer says so (SL-8.2's
    `lease_ended_unconfirmed`, proven under WR-CANCEL-3). The label is `na`, with its citation."""
    labels = tomllib.loads(LABELS.read_text(encoding="utf-8"))["label"]
    (label,) = [x for x in labels if x["id"] == "WR-OWN-8:unconfirmed-path"]
    assert label["posture"] == "na" and "OQ-34" in label["reason"]


def test_publication_d_b_rule_via_mcp(tmp_path: Path) -> None:
    """D-b (SV-2.4) re-verified over the wire: a plugin whose own source imports a port module
    and declares no `env_arg` is refused `publication.env_arg_missing` by `publish_plugin`, and
    the same source declaring it is published."""
    porter = (
        "from __future__ import annotations\n\n"
        "from trestle.plugin import Context, trestle\n"
        "from trestle_packs.fakes import FakeMarker  # noqa: F401\n\n\n"
        "@trestle{decorator}\n"
        "def porter(ctx: Context, env: str = 'dev') -> dict[str, str]:\n"
        "    return {{'env': env}}\n"
    )
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        refused = host.call("publish_plugin", {"source": porter.format(decorator="")})
        assert refused["code"] == codes.PUBLICATION_ENV_ARG_MISSING, refused
        assert "env_arg" in refused["message"]
        assert "porter" not in {row["name"] for row in host.call("list_plugins", {})["items"]}

        declared = host.call(
            "publish_plugin", {"source": porter.format(decorator='(env_arg="env")')}
        )
        assert declared.get("code") is None, declared
        assert "porter" in {row["name"] for row in host.call("list_plugins", {})["items"]}
