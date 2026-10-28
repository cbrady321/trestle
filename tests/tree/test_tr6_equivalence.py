"""L.TR-6.1: D6 position equivalence executed in-library (WR-UNIT-1, V-8's closed list, SV-7a).

One root-eligible unit (`work` of `root_eligible_both`) is run directly, as the root, and inside the
fixture's two-level parent, with identical params and machine state; `differ d6` compares the node's
verdicts, its effect-record set and its disposition after normalizing only V-8's list
(`d6_direct_child.NORMALIZED`), and reports any other difference by name. LOGIC runs the real loop
in-library (MC-26's rig: real lane and services under a manual clock); PROC runs the same pair
through a kernel, the wrapper and a child process. The after-stop variant compares the pair only up
to the first whole-root stop record of the parent run, where a sibling's uncaught exception (B1-E6)
reaches the node mid-wait, and then requires the node to be reported stopped (V-8 L-8).

The mode's own self-test plants differences: one outside V-8's list in each compared field, one
before the first stop in the after-stop variant, and the noise inside the list, which must not
count."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import differ, harness, records
from tests.proof.differ_modes import d6_direct_child as d6
from tests.tree import d6_pairs as pairs
from tests.tree import d6_proc as proc
from trestle.server.main import Kernel

LABEL_ARGS = ("A", "tree", "LOGIC+PROC+MCP", "CI")


def proves(label: str) -> pytest.MarkDecorator:
    return pytest.mark.proves("WR-UNIT-1", f"WR-UNIT-1:{label}", *LABEL_ARGS)


PLANTED = "tests.tree.test_tr6_equivalence:planted_pairs"


# ---- the pairs, run for real ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[pairs.Run, pairs.Run]]:
    """Every in-library scenario's two runs, made once: they are read by several tests."""
    root = tmp_path_factory.mktemp("d6")
    return {name: pairs.scenario(name, root / name) for name in pairs.SCENARIOS}


def _pair(logic: dict[str, tuple[pairs.Run, pairs.Run]], name: str) -> d6.Pair:
    direct, child = logic[name]
    return d6.Pair(name, direct.position(""), child.position(pairs.NODE))


def _publish(kernel: Kernel, tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / "plugins" / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return path


def _run_proc(
    kernel: Kernel, tmp_path: Path, name: str, source: str
) -> tuple[Path, dict[str, Any]]:
    """Publish `source` as plugin `name` and run it to its terminal answer through the real
    kernel, wrapper and child process; the run directory and the terminal wire answer."""
    path = _publish(kernel, tmp_path, name, source)
    admitted = harness.admit_tree(path, {"env": "dev"}, kernel=kernel)
    with support.reaping(admitted.run_id):
        view = harness.drive_tree(admitted)
    return admitted.run_dir, view.to_dict()["answer"]


def _proc_position(run: tuple[Path, dict[str, Any]], path: str) -> d6.Position:
    run_dir, wire = run
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    entries = [row.entry for row in lane.rows]
    return d6.Position.of(entries, wire, path, stop_seq=d6.first_exception_stop(entries))


def _classes(position: d6.Position) -> list[tuple[str, str | None]]:
    return [(r["class"], r.get("effect")) for r in position.rows]


CREATED_AND_RELEASED = [
    ("issue", "up"),
    ("confirmation", "up"),
    ("end", None),
    ("issue", "stop"),
    ("confirmation", "stop"),
    ("released", "up"),
]


# ---- WR-UNIT-1: equivalence of verdict, effect records and disposition ---------------------------


@pytest.mark.parametrize(
    "venue",
    [
        pytest.param("logic", marks=proves("equiv-verdict"), id="logic"),
        pytest.param("proc", marks=proves("equiv-verdict"), id="proc"),
    ],
)
def test_d6_direct_vs_child(
    venue: str,
    logic: dict[str, tuple[pairs.Run, pairs.Run]],
    tree_kernel: Kernel,
    tmp_path: Path,
) -> None:
    if venue == "logic":
        pair = _pair(logic, "fresh")
    else:
        direct = _run_proc(
            tree_kernel, tmp_path, proc.DIRECT, proc.plugin_source(proc.DIRECT, "direct")
        )
        child = _run_proc(
            tree_kernel, tmp_path, proc.CHILD, proc.plugin_source(proc.CHILD, "child")
        )
        pair = d6.Pair("proc", _proc_position(direct, ""), _proc_position(child, pairs.NODE))
    assert d6.check_pair(pair) == []
    # not vacuous: the unit did create, end satisfied and give back what it made, in both positions
    for position in (pair.direct, pair.child):
        assert _classes(position) == CREATED_AND_RELEASED
        (end,) = [r for r in position.rows if r["class"] == "end"]
        assert (end["condition"], end["provenance"], end["cut"]) == ("satisfied", "created", None)
        assert position.account is not None
        assert (position.account["node_class"], position.account["disposition"]) == (
            "passed",
            "started",
        )
    # only V-8's list moved: the two positions do differ in exactly those fields
    assert pair.direct.rows[0]["path"] == "" and pair.child.rows[0]["path"] == pairs.NODE
    assert pair.direct.account["listing"] == "rolled_up"  # type: ignore[index]
    assert pair.child.account["listing"] == "candidate"  # type: ignore[index]


@proves("direct-skip-satisfied")
def test_d6_direct_skip_satisfied(logic: dict[str, tuple[pairs.Run, pairs.Run]]) -> None:
    """A unit whose postcondition already holds records no effect, called directly or as a child,
    and reuses what it found (disposition `reused`)."""
    pair = _pair(logic, "satisfied")
    assert d6.check_pair(pair) == []
    for position in (pair.direct, pair.child):
        assert [r["class"] for r in position.rows] == [
            "end"
        ]  # no issue, no confirmation, no release
        assert position.rows[0]["provenance"] == "found"
        assert position.account is not None and position.account["disposition"] == "reused"
    direct, child = logic["satisfied"]
    assert direct.marker is not None and child.marker is not None
    assert direct.marker.calls == [("observe", "work")]  # nothing was created or stopped
    assert ("create", "work") not in child.marker.calls and (
        "stop",
        "work",
    ) not in child.marker.calls


@proves("direct-releases-own-only")
def test_d6_direct_releases_own_only(logic: dict[str, tuple[pairs.Run, pairs.Run]]) -> None:
    """Called directly, the unit gives back only what it created; a resource that was there and is
    not its own is still there afterwards, and inside the parent likewise (the parent's release
    phase also gives back `prep`'s, which is the parent's, L-2)."""
    pair = _pair(logic, "foreign")
    assert d6.check_pair(pair) == []
    direct, child = logic["foreign"]
    assert direct.marker is not None and child.marker is not None
    assert direct.marker.live() == child.marker.live() == frozenset({"other"})
    assert direct.marker.paths("stop") == ["work"]
    assert sorted(child.marker.paths("stop")) == ["prep", "work"]
    for position in (pair.direct, pair.child):
        stops = [r for r in position.rows if r["class"] == "issue" and r["effect"] == "stop"]
        assert len(stops) == 1  # one release for the one thing it created


@proves("once-not-reissued")
def test_d6_once_not_reissued(logic: dict[str, tuple[pairs.Run, pairs.Run]]) -> None:
    """A ONCE effect that was issued and applied is not issued again, however the node ends, in
    either position (V-4.7)."""
    pair = _pair(logic, "once")
    assert d6.check_pair(pair) == []
    direct, child = logic["once"]
    assert direct.events is not None and child.events is not None
    assert direct.events.calls == ["work"]
    assert child.events.calls == ["prep", "work"]
    for position in (pair.direct, pair.child):
        issues = [r for r in position.rows if r["class"] == "issue"]
        assert [(r["effect"], r["attempt"], r["repeat"]) for r in issues] == [("run", 1, "once")]
        (end,) = [r for r in position.rows if r["class"] == "end"]
        assert (end["condition"], end["code"]) == ("failed", "unit.tests_failed")


# ---- the after-stop variant (SV-7a): compared up to the parent's first whole-root stop -----------


@pytest.mark.parametrize(
    "venue",
    [
        pytest.param("logic", marks=proves("equiv-verdict"), id="logic"),
        pytest.param("proc", marks=proves("equiv-verdict"), id="proc"),
    ],
)
def test_d6_after_sibling_exception_compared_to_first_stop(
    venue: str,
    logic: dict[str, tuple[pairs.Run, pairs.Run]],
    tree_kernel: Kernel,
    tmp_path: Path,
) -> None:
    if venue == "logic":
        pair = _pair(logic, "after_stop")
    else:
        direct_source, child_source = proc.exception_sources()
        direct = _run_proc(tree_kernel, tmp_path, proc.EXCEPTION_DIRECT, direct_source)
        child = _run_proc(tree_kernel, tmp_path, proc.EXCEPTION_CHILD, child_source)
        pair = d6.Pair("proc", _proc_position(direct, ""), _proc_position(child, proc.STOPPED_NODE))
    assert d6.check_pair(pair) == []
    stop = pair.child.stop_seq
    assert stop is not None, "the parent run recorded no whole-root stop"
    # not vacuous: the node acted before the stop (a create, confirmed); the runs agree there
    before = [r for r in pair.child.rows if r["seq"] < stop]
    assert [(r["class"], r.get("effect")) for r in before] == [
        ("issue", "up"),
        ("confirmation", "up"),
    ]
    assert d6.check_pair(replace(pair, child=replace(pair.child, stop_seq=None))) != [], (
        "compared in full the pair differs: only the stop cut the comparison short"
    )
    # after it the node is a stopped node of the parent's answer (V-8 L-8)
    assert pair.child.account is not None and pair.child.account["listing"] == "stopped"
    (end,) = [r for r in pair.child.rows if r["class"] == "end"]
    # `==`: it saw the goal flip before the raise row was written (B1-E6), so it is the stop
    assert end["cut"] == "stopped" and end["seq"] >= stop
    # ... while called directly, nothing stopped it: it ran to its own end
    (direct_end,) = [r for r in pair.direct.rows if r["class"] == "end"]
    assert direct_end["cut"] is None and direct_end["code"] == "execution.postcondition_timeout"


# ---- differ d6, and its planted negatives ---------------------------------------------------


def test_mode_is_built_and_passes_over_the_products_pairs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert "d6" not in differ.UNBUILT_MODES
    assert differ.main(["d6"]) == 0
    out = capsys.readouterr().out
    assert "not built" not in out and f"{len(pairs.SCENARIOS)} direct/child pairs" in out
    assert "0 diffs" in out


def test_cli_without_a_pair_source_is_not_reported_as_not_built(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = differ.main(["d6", "--pairs", "tests.tree.no_such_module:pairs"])
    out = capsys.readouterr().out
    assert rc == 2 and "no pair source" in out and "not built" not in out


def _edit(position: d6.Position, index: int, **fields: Any) -> d6.Position:
    rows = [dict(r) for r in position.rows]
    rows[index].update(fields)
    return replace(position, rows=tuple(rows))


def planted_pairs() -> list[d6.Pair]:
    """A pair whose child ended with another condition: a difference outside V-8's list."""
    fresh = _fresh_pair()
    end = next(n for n, r in enumerate(fresh.child.rows) if r["class"] == "end")
    return [replace(fresh, child=_edit(fresh.child, end, condition="failed"))]


_FRESH: list[d6.Pair] = []


def _fresh_pair() -> d6.Pair:
    """The `fresh` scenario, run once for the planting tests (cheap: in-library, manual clock)."""
    if not _FRESH:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="d6-plant-") as scratch:
            direct, child = pairs.scenario("fresh", Path(scratch))
            _FRESH.append(d6.Pair("fresh", direct.position(""), child.position(pairs.NODE)))
    return _FRESH[0]


def test_cli_plants_a_difference_outside_the_list(capsys: pytest.CaptureFixture[str]) -> None:
    rc = differ.main(["d6", "--pairs", PLANTED])
    out = capsys.readouterr().out
    assert rc == 1 and "d6: DIFF: fresh:" in out and "condition differ" in out


# (row class, field, planted value): a field V-8's list does not name, moved in the child
OUTSIDE_THE_LIST = [
    ("issue", "attempt", 2),
    ("issue", "effect", "other"),
    ("issue", "repeat", "once"),
    ("issue", "facet", "owned"),
    ("issue", "lifetime", "durable"),
    ("issue", "release", {"form": "argv", "helpers_disclosed": True}),
    ("confirmation", "status", "not_applied"),
    ("confirmation", "code", "tool.busy"),
    ("end", "condition", "failed"),
    ("end", "provenance", "found"),
    ("end", "cut", "stopped"),
    ("end", "code", "unit.other"),
    ("end", "human_action", "act"),
    ("end", "resend", "unknown"),
    ("released", "outcome", "left"),
]


@pytest.mark.parametrize(("cls", "field_name", "value"), OUTSIDE_THE_LIST)
def test_d6_fails_on_planted_difference_outside_the_list(
    cls: str, field_name: str, value: Any
) -> None:
    pair = _fresh_pair()
    index = next(n for n, r in enumerate(pair.child.rows) if r["class"] == cls)
    planted = replace(pair, child=_edit(pair.child, index, **{field_name: value}))
    diffs = d6.check_pair(planted)
    assert diffs and field_name in diffs[0], diffs


def test_d6_fails_on_a_missing_extra_or_reordered_effect_record() -> None:
    pair = _fresh_pair()
    rows = list(pair.child.rows)
    for planted_rows in (rows[:-1], [*rows, rows[0]], [rows[1], rows[0], *rows[2:]]):
        planted = replace(pair, child=replace(pair.child, rows=tuple(planted_rows)))
        assert d6.check_pair(planted), planted_rows


@pytest.mark.parametrize(
    "field_name", ["node_class", "disposition", "code", "human_action", "resend"]
)
def test_d6_fails_on_planted_answer_account_difference(field_name: str) -> None:
    pair = _fresh_pair()
    assert pair.child.account is not None
    planted = replace(
        pair, child=replace(pair.child, account={**pair.child.account, field_name: "x"})
    )
    diffs = d6.check_pair(planted)
    assert diffs and "answer account" in diffs[0]


def test_d6_ignores_exactly_the_difference_v8_names() -> None:
    """Noise in every normalized field (the lane position, the clock readings, the path, the
    selector, the answer's listing and condition) is no difference."""
    pair = _fresh_pair()
    rows = [
        {**r, "seq": r["seq"] + 100, "path": "elsewhere/" + r["path"]}
        | ({"at": "2030-01-01T00:00:00.000000Z"} if "at" in r else {})
        | ({"issued_at": "2030-01-01T00:00:00.000000Z"} if "issued_at" in r else {})
        | ({"released_at": "2030-01-01T00:00:00.000000Z"} if "released_at" in r else {})
        | ({"identity": "sel-another"} if r.get("identity") else {})
        for r in pair.child.rows
    ]
    assert any("identity" in r for r in rows) and any("at" in r for r in rows)
    account = {**(pair.child.account or {}), "listing": "stopped", "condition": None, "path": ["x"]}
    noisy = replace(pair, child=replace(pair.child, rows=tuple(rows), account=account))
    assert d6.check_pair(noisy) == []


def test_normalized_fields_are_v8s_closed_list() -> None:
    """The mode normalizes only fields of the four rules that leave a mark on a compared record; the
    other five rules name none."""
    assert set(d6.NORMALIZED) == {"L-3", "L-4", "L-5", "L-8"}
    dropped = {f for fields in d6.NORMALIZED.values() for f in fields}
    assert dropped == {
        "at",
        "issued_at",
        "released_at",
        "seq",
        "path",
        "identity",
        "listing",
        "condition",
    }
    assert d6.ROW_DROPPED | d6.ACCOUNT_DROPPED | {d6.IDENTITY} == dropped


# the after-stop variant


def _stopped_pair() -> d6.Pair:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="d6-plant-") as scratch:
        direct, child = pairs.scenario("after_stop", Path(scratch))
        return d6.Pair("after_stop", direct.position(""), child.position(pairs.NODE))


def test_d6_fails_on_planted_pre_stop_difference_in_the_after_stop_variant() -> None:
    pair = _stopped_pair()
    assert d6.check_pair(pair) == []
    stop = pair.child.stop_seq
    assert stop is not None
    before = [n for n, r in enumerate(pair.child.rows) if r["seq"] < stop]
    assert before
    planted = replace(pair, child=_edit(pair.child, before[0], attempt=2))
    diffs = d6.check_pair(planted)
    assert diffs and "pre-stop" in diffs[0] and "attempt" in diffs[0]
    # a node that did *more* before the stop than the direct run ever did is a difference too
    extra = replace(pair.child, rows=(pair.child.rows[0], *pair.child.rows))
    assert any("fewer" in d or "pre-stop" in d for d in d6.check_pair(replace(pair, child=extra)))


def test_d6_after_stop_differences_after_the_stop_are_not_compared() -> None:
    """Past the first stop record the runs are no longer the same run (SV-7a): the child's release
    rows, and their order, count for nothing; only being reported stopped does."""
    pair = _stopped_pair()
    stop = pair.child.stop_seq
    assert stop is not None
    after = [n for n, r in enumerate(pair.child.rows) if r["seq"] >= stop]
    assert after
    rows = [dict(r) for r in pair.child.rows]
    for n in after:
        if rows[n]["class"] != "end":
            rows[n]["attempt"] = 9
    assert d6.check_pair(replace(pair, child=replace(pair.child, rows=tuple(rows)))) == []
    for listing in ("stopped", "unended", "not_started"):  # V-8 L-8 / B4-C2 rule (4)
        account = {**(pair.child.account or {}), "listing": listing}
        assert d6.check_pair(replace(pair, child=replace(pair.child, account=account))) == []
    for wrong in ("candidate", "rolled_up", None):
        account = {**(pair.child.account or {}), "listing": wrong}
        diffs = d6.check_pair(replace(pair, child=replace(pair.child, account=account)))
        assert diffs and "not stopped" in diffs[0], wrong
    end = next(n for n in after if rows[n]["class"] == "end")
    planted = replace(pair, child=_edit(pair.child, end, cut=None))
    assert any("not `stopped` or `not_started`" in d for d in d6.check_pair(planted))


def test_first_stop_is_the_first_exception_record() -> None:
    entries = [
        {"seq": 1, "class": "plan"},
        {"seq": 2, "class": "step", "code": "execution.unit_raised", "path": "boom"},
        {"seq": 3, "class": "step", "code": "execution.unit_raised", "path": "other"},
    ]
    assert d6.first_exception_stop(entries) == 2
    assert d6.first_exception_stop(entries[:1]) is None


def test_first_stop_is_a_cut_end_recorded_before_the_raise_row() -> None:
    """B1-E6 flips the goal before the raising node's step row is written, so a sibling that saw the
    flip can end (`cut=stopped`) ahead of that row: the stop is then that end, not the raise row. An
    end that was not cut, or a cut end with no raise at all, is no exception stop."""
    entries = [
        {"seq": 1, "class": "plan"},
        {"seq": 2, "class": "issue", "path": "work"},
        {"seq": 3, "class": "end", "cut": "stopped", "path": "work"},
        {"seq": 4, "class": "step", "code": "execution.unit_raised", "path": "boom"},
    ]
    assert d6.first_exception_stop(entries) == 3
    uncut = [{**e, "cut": None} if e["class"] == "end" else e for e in entries]
    assert d6.first_exception_stop(uncut) == 4
    assert d6.first_exception_stop(entries[:3]) is None
