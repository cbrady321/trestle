"""The claims and rank record-fact sub-suite (L.SV-5.11): record facts about what a run did to the
world, read from the run directory after the wrapper and the child have exited (MC-13: no process
of the run is alive, so nothing can be told to the plugin, it is dead), through the proof court's
seam only (`tests.proof.records`, the strict lane oracle and the ledger reader, MC-10).

Each fact is a function over the lane and ledger rows that returns a `Verdict` and is not
vacuous: it fails on an empty lane, and a planted defect makes it fail (the planted-defect tests
below). The real runs are the harness's one-vertex fixture (`spine_leaf`):

    claims_precede_effects      every APPLIED attempt has an earlier claim (an issue entry carrying
                                its release descriptor) in lane order (WR-OWN, B2-C7)
    descriptors_equal           every attempt of one effect carries an equal descriptor (V-10.1)
    lane_folded_equals_lane     the host's `lane_folded` ledger rows are the lane's entries, one
                                each, in lane order (B2-C7)
    release_reverse_issue       the loop's `released` entries go in reverse issue order (B1-O6)
    sweep_rank_order            the host sweep's `sweep_disposition` rows go in descending release
                                rank, then reverse issue order (B2-C9)
    durable_never_released      a DURABLE ticket has no `released` entry and no released sweep row
    found_never_released        no found handle is in any release set (WR-OWN-2)
    released_means_absent       every target recorded released is observed absent after the run
                                (V-10.4, WR-OWN-6)

A one-vertex run has one create target, so the two ordering facts are non-trivial only on planted
multi-target records; the real runs assert what they can (one create, one release, in order).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker

from tests.core.spine import support
from tests.proof import harness, record_facts, records
from tests.proof.record_facts import Verdict
from tests.proof.records import LaneRows

pytestmark = pytest.mark.spine

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "fixtures" / "workflows" / "spine_leaf.py"
Key = tuple[str, str, int]  # a ticket: (path, effect, attempt)


def _vacuous(lane: LaneRows) -> Verdict | None:
    if not lane.rows:
        return Verdict(False, ("the lane holds no entry: nothing is proven",), vacuous=True)
    return None


def _tickets(lane: LaneRows) -> list[dict[str, Any]]:
    """The oracle's assembly of each ticket in issue order: its issue, confirmation and released
    entries (record_facts' reading of `records.lane_tickets`)."""
    out = []
    for t in records.lane_tickets(lane):
        out.append(
            {
                "key": (
                    t["issue"].path or "",
                    t["issue"].entry["effect"],
                    t["issue"].entry["attempt"],
                ),
                "issue": t["issue"].entry,
                "confirmation": None if t["confirmation"] is None else t["confirmation"].entry,
                "released": None if t["released"] is None else t["released"].entry,
                "released_pos": None if t["released"] is None else t["released"].offset,
                "issue_pos": t["issue"].offset,
            }
        )
    return out


# ---- the facts --------------------------------------------------------------------------------


def claims_precede_effects(lane: LaneRows) -> Verdict:
    """`record_facts.claim_before_effect`, plus: every APPLIED confirmation is one of a ticket that
    was issued (its claim carries a release descriptor) before it."""
    verdict = record_facts.claim_before_effect(lane)
    if verdict.vacuous or not verdict.ok:
        return verdict
    applied = [
        row for row in lane.rows if row.cls == "confirmation" and row.entry["status"] == "applied"
    ]
    if not applied:
        return Verdict(False, ("no APPLIED attempt: nothing is proven",), vacuous=True)
    return verdict


def descriptors_equal(lane: LaneRows) -> Verdict:
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    seen: dict[tuple[str, str], Mapping[str, Any]] = {}
    violations: list[str] = []
    for row in lane.rows:
        if row.cls != "issue":
            continue
        key = (row.path or "", row.entry["effect"])
        if key in seen and seen[key] != row.entry["release"]:
            violations.append(f"{key}: attempt {row.entry['attempt']} carries another descriptor")
        seen.setdefault(key, row.entry["release"])
    return Verdict(not violations, tuple(violations))


def lane_folded_equals_lane(ledger: Sequence[Mapping[str, Any]], lane: LaneRows) -> Verdict:
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    folded = [r for r in ledger if r.get("kind") == "lane_folded"]
    if len(folded) != len(lane.rows):
        return Verdict(False, (f"{len(folded)} lane_folded rows, {len(lane.rows)} lane entries",))
    violations = []
    for row, entry in zip(folded, lane.rows, strict=True):
        if (row["lane_seq"], row["entry_class"], row["path"]) != (
            entry.entry["seq"],
            entry.cls,
            entry.entry.get("path"),
        ):
            violations.append(f"lane_folded row for seq {row['lane_seq']} differs from the lane")
        elif entry.cls == "issue" and row["descriptor"] != entry.entry["release"]:
            violations.append(f"lane_folded row for seq {row['lane_seq']}: descriptor differs")
    return Verdict(not violations, tuple(violations))


def release_reverse_issue(lane: LaneRows) -> Verdict:
    """The `released` entries appear in the reverse of the order their tickets' creations were
    issued (the loop releases newest first, B1-O6)."""
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    tickets = _tickets(lane)
    creates = [
        t["key"]
        for t in tickets
        if t["issue"]["facet"] == "create"
        and t["confirmation"] is not None
        and t["confirmation"]["status"] == "applied"
    ]
    released = [
        t["key"] for t in sorted((t for t in tickets if t["released"]), key=_by_release_pos)
    ]
    if not released:
        return Verdict(False, ("no `released` entry: nothing is proven",), vacuous=True)
    positions = [creates.index(key) for key in released if key in creates]
    unmatched = [key for key in released if key not in creates]
    violations = [f"released {key} is not a created ticket" for key in unmatched]
    if positions != sorted(positions, reverse=True):
        violations.append(f"released in order {released}, not the reverse of issue {creates}")
    return Verdict(not violations, tuple(violations))


def _by_release_pos(ticket: Mapping[str, Any]) -> int:
    return int(ticket["released_pos"])


def sweep_rank_order(
    ledger: Sequence[Mapping[str, Any]], lane: LaneRows, release_rank: Mapping[str, int]
) -> Verdict:
    """The `sweep_disposition` rows go in descending release rank of the target's node, then in
    reverse issue order of the target's earliest issue entry."""
    rows = [r for r in ledger if r.get("kind") == "sweep_disposition"]
    if not rows:
        return Verdict(False, ("no sweep_disposition row: nothing is proven",), vacuous=True)
    first_issue: dict[tuple[str, str], int] = {}
    for row in lane.rows:
        if row.cls == "issue":
            first_issue.setdefault((row.path or "", row.entry["effect"]), row.offset)

    def order_key(row: Mapping[str, Any]) -> tuple[int, int]:
        path, effect = row["target"]["path"], row["target"]["effect"]
        return (-int(release_rank.get(path, 0)), -first_issue.get((path, effect), 0))

    keys = [order_key(r) for r in rows]
    if keys != sorted(keys):
        return Verdict(
            False,
            (f"sweep rows in order {[r['target'] for r in rows]}, not rank then reverse issue",),
        )
    return Verdict(True)


def durable_never_released(ledger: Sequence[Mapping[str, Any]], lane: LaneRows) -> Verdict:
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    durable = {
        t["key"]
        for t in _tickets(lane)
        if t["issue"]["lifetime"] == "durable" or t["issue"]["release"].get("form") == "durable"
    }
    if not durable:
        return Verdict(False, ("no durable ticket: nothing is proven",), vacuous=True)
    violations = [
        f"durable {t['key']} has a released entry"
        for t in _tickets(lane)
        if t["key"] in durable and t["released"]
    ]
    for row in ledger:
        if row.get("kind") == "sweep_disposition" and row["disposition"] == "released":
            target = (row["target"]["path"], row["target"]["effect"])
            if any(key[:2] == target for key in durable):
                violations.append(f"durable {target} swept as released")
    return Verdict(not violations, tuple(violations))


def found_never_released(lane: LaneRows, found_selectors: Sequence[str]) -> Verdict:
    """No ticket whose confirmation identity is a found selector has a `released` entry: what
    Trestle did not create is never in a release set."""
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    violations = [
        f"found {t['confirmation']['identity']} was released as {t['key']}"
        for t in _tickets(lane)
        if t["released"] and t["confirmation"] and t["confirmation"]["identity"] in found_selectors
    ]
    return Verdict(not violations, tuple(violations))


def released_means_absent(
    ledger: Sequence[Mapping[str, Any]], lane: LaneRows, present: frozenset[str]
) -> Verdict:
    """Every target recorded released (a `released` lane entry, or a released sweep row for the
    target's path and effect) is observed absent after the run: its selector is not in `present`,
    what the port's own inventory reports (V-10.4)."""
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    swept = {
        (r["target"]["path"], r["target"]["effect"])
        for r in ledger
        if r.get("kind") == "sweep_disposition" and r["disposition"] == "released"
    }
    recorded = [
        t
        for t in _tickets(lane)
        if t["confirmation"] is not None
        and t["confirmation"]["identity"] is not None
        and (t["released"] or t["key"][:2] in swept)
    ]
    if not recorded:
        return Verdict(False, ("no target recorded released: nothing is proven",), vacuous=True)
    violations = [
        f"{t['key']} is recorded released and {t['confirmation']['identity']} still exists"
        for t in recorded
        if t["confirmation"]["identity"] in present
    ]
    return Verdict(not violations, tuple(violations))


# ---- the real runs (one-vertex fixture) -------------------------------------------------------


@dataclass(frozen=True)
class Ran:
    run_dir: Path
    lane: LaneRows
    ledger: list[dict[str, Any]]
    markers: Path

    @property
    def present(self) -> frozenset[str]:
        return frozenset(FakeMarker(self.markers, "run").inventory()["containers"])


def _ran(mode: str) -> Ran:
    run_dir = harness.run_tree(FIXTURE, {"env": "dev", "mode": mode})
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    # the plugin is dead: the wrapper and the child have exited, so the record is all there is
    assert not support.marked(run_dir.name), "a process of the run outlived its terminal row"
    return Ran(
        run_dir, lane, records.ledger_rows(run_dir).rows, run_dir / "work" / "tmp" / "markers"
    )


@pytest.fixture(scope="module")
def advanced() -> Ran:
    return _ran("advance")


@pytest.fixture(scope="module")
def skipped() -> Ran:
    return _ran("skip")


def test_every_applied_attempt_has_an_earlier_claim_with_a_descriptor(advanced: Ran) -> None:
    verdict = claims_precede_effects(advanced.lane)
    assert verdict.ok, verdict
    issues = [r.entry for r in advanced.lane.rows if r.cls == "issue"]
    assert len(issues) == 2 and all(i["release"]["form"] == "in_run_group" for i in issues)


def test_descriptor_equal_across_attempts_of_one_effect(advanced: Ran) -> None:
    assert descriptors_equal(advanced.lane).ok
    # one create attempt and one release attempt: the create's descriptor is the handle's own
    (create,) = [
        r.entry for r in advanced.lane.rows if r.cls == "issue" and r.entry["effect"] == "up"
    ]
    (stop,) = [
        r.entry for r in advanced.lane.rows if r.cls == "issue" and r.entry["effect"] == "stop"
    ]
    assert create["release"] == stop["release"]


def test_lane_folded_rows_equal_lane_entries(advanced: Ran) -> None:
    verdict = lane_folded_equals_lane(advanced.ledger, advanced.lane)
    assert verdict.ok, verdict


def test_loop_release_order_is_reverse_issue(advanced: Ran) -> None:
    verdict = release_reverse_issue(advanced.lane)
    assert verdict.ok and not verdict.vacuous, verdict
    classes = [row.cls for row in advanced.lane.rows]
    # every vertex ended before anything was given back (B1-C9)
    assert classes.index("end") < classes.index("released")


def test_host_sweep_left_nothing_for_an_in_run_group_release(advanced: Ran) -> None:
    """The created marker is `InRunGroup`: the group target's, so the host sweep writes no
    per-target row for a run whose loop released everything, and the rank-order fact is
    vacuous here (it is exercised on planted multi-target records)."""
    assert not [r for r in advanced.ledger if r.get("kind") == "sweep_disposition"]
    verdict = sweep_rank_order(advanced.ledger, advanced.lane, {"": 0})
    assert verdict.vacuous


def test_durable_is_never_released_vacuous_without_a_durable_ticket(advanced: Ran) -> None:
    assert durable_never_released(advanced.ledger, advanced.lane).vacuous


def test_no_found_handle_in_any_release_set(skipped: Ran) -> None:
    """A found instance satisfies the postcondition and is left exactly as it was: no claim, no
    release, and the port still reports it there after the run."""
    assert [r.cls for r in skipped.lane.rows] == ["plan", "end"]
    assert skipped.present == {"found-pre-existing"}
    verdict = found_never_released(skipped.lane, sorted(skipped.present))
    assert verdict.ok, verdict


def test_created_target_recorded_released_is_observed_absent(advanced: Ran) -> None:
    assert advanced.present == frozenset()
    verdict = released_means_absent(advanced.ledger, advanced.lane, advanced.present)
    assert verdict.ok and not verdict.vacuous, verdict


# ---- planted multi-target records -------------------------------------------------------------


def _lane_of(
    tmp_path: Path, entries: list[dict[str, Any]], *, well_formed: bool = True
) -> LaneRows:
    """A lane file of `entries` (seq assigned in order), read through the oracle."""
    run = tmp_path / f"planted-{len(list(tmp_path.iterdir()))}"
    (run / "evidence").mkdir(parents=True)
    lines = [json.dumps({**e, "seq": n}, separators=(",", ":")) for n, e in enumerate(entries, 1)]
    (run / "evidence" / "lane.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lane = records.lane_rows(run)
    assert well_formed == (not lane.problems), lane.problems
    return lane


RUN_GROUP = {"form": "in_run_group", "helpers_disclosed": False}
DURABLE = {"form": "durable", "owner": "environment"}
AT = "2026-09-30T00:00:00Z"
PLAN = {
    "class": "plan",
    "lane_format": 1,
    "declaration_digest": "d" * 64,
    "args_hash": "a" * 64,
    "selection": {},
    "observations_digest": "o" * 64,
}


def _issue(
    path: str,
    effect: str,
    *,
    release: Mapping[str, Any] = RUN_GROUP,
    lifetime: str = "run",
    attempt: int = 1,
    facet: str = "create",
) -> dict[str, Any]:
    return {
        "class": "issue", "path": path, "effect": effect, "attempt": attempt, "facet": facet,
        "repeat": "safe", "lifetime": lifetime, "release": dict(release), "remedy": None,
        "issued_at": AT,
    }  # fmt: skip


def _confirm(path: str, effect: str, identity: str | None, *, attempt: int = 1) -> dict[str, Any]:
    return {
        "class": "confirmation", "path": path, "effect": effect, "attempt": attempt,
        "status": "applied", "code": None, "identity": identity,
    }  # fmt: skip


def _released(path: str, effect: str, *, attempt: int = 1) -> dict[str, Any]:
    return {
        "class": "released", "path": path, "effect": effect, "attempt": attempt,
        "released_at": AT, "outcome": None,
    }  # fmt: skip


def test_release_order_is_checked_on_two_targets(tmp_path: Path) -> None:
    creates = [
        PLAN,
        _issue("", "a"), _confirm("", "a", "sel-a"),
        _issue("", "b"), _confirm("", "b", "sel-b"),
    ]  # fmt: skip
    newest_first = _lane_of(tmp_path, [*creates, _released("", "b"), _released("", "a")])
    oldest_first = _lane_of(tmp_path, [*creates, _released("", "a"), _released("", "b")])
    assert release_reverse_issue(newest_first).ok
    assert not release_reverse_issue(oldest_first).ok


def test_sweep_order_is_checked_on_planted_rows(tmp_path: Path) -> None:
    """Ranks: node `db` is released before node `web` (higher rank first); inside one node the
    later issue first."""
    lane = _lane_of(
        tmp_path,
        [
            PLAN,
            _issue("web", "w1", release=DURABLE), _issue("web", "w2", release=DURABLE),
            _issue("db", "d1", release=DURABLE),
        ],
    )  # fmt: skip
    rank = {"db": 1, "web": 0}

    def rows(*targets: tuple[str, str]) -> list[dict[str, Any]]:
        return [
            {
                "kind": "sweep_disposition",
                "target": {"path": p, "effect": e},
                "disposition": "released",
            }
            for p, e in targets
        ]

    assert sweep_rank_order(rows(("db", "d1"), ("web", "w2"), ("web", "w1")), lane, rank).ok
    assert not sweep_rank_order(rows(("web", "w2"), ("db", "d1"), ("web", "w1")), lane, rank).ok
    assert not sweep_rank_order(rows(("db", "d1"), ("web", "w1"), ("web", "w2")), lane, rank).ok


def test_durable_released_is_caught(tmp_path: Path) -> None:
    base = [
        PLAN,
        _issue("", "keep", release=DURABLE, lifetime="durable"),
        _confirm("", "keep", "sel-k"),
    ]
    clean = _lane_of(tmp_path, base)
    assert durable_never_released([], clean).ok
    released = _lane_of(tmp_path, [*base, _released("", "keep")])
    assert not durable_never_released([], released).ok
    swept = [
        {
            "kind": "sweep_disposition",
            "target": {"path": "", "effect": "keep"},
            "disposition": "released",
        }
    ]
    assert not durable_never_released(swept, clean).ok


# ---- planted defects: each must be caught -----------------------------------------------------


def test_planted_effect_before_claim_is_caught(tmp_path: Path) -> None:
    good = _lane_of(tmp_path, [PLAN, _issue("", "a"), _confirm("", "a", "sel-a")])
    assert claims_precede_effects(good).ok
    effect_first = _lane_of(tmp_path, [PLAN, _confirm("", "a", "sel-a"), _issue("", "a")])
    assert not claims_precede_effects(effect_first).ok
    no_claim = _lane_of(tmp_path, [PLAN, _confirm("", "a", "sel-a")])
    assert not claims_precede_effects(no_claim).ok
    bare = _issue("", "a")
    bare["release"] = {}
    # the strict oracle drops a claim without a descriptor (and says so), leaving the effect bare
    no_descriptor = _lane_of(tmp_path, [PLAN, bare, _confirm("", "a", "sel-a")], well_formed=False)
    assert no_descriptor.problems and not claims_precede_effects(no_descriptor).ok
    assert claims_precede_effects(_lane_of(tmp_path, [PLAN])).vacuous  # nothing applied


def test_planted_release_of_found_is_caught(tmp_path: Path) -> None:
    found = "found-pre-existing"
    lane = _lane_of(tmp_path, [PLAN, _issue("", "a"), _confirm("", "a", found), _released("", "a")])
    assert not found_never_released(lane, [found]).ok
    assert found_never_released(lane, ["another-selector"]).ok


def test_planted_surviving_marker_swept_as_released_is_caught(tmp_path: Path) -> None:
    """A marker that is a plain file cannot be removed by stopping a process group; a sweep that
    calls it released while the file exists is wrong (V-10.4: released means observed absent)."""
    markers = tmp_path / "markers"
    selector = FakeMarker(markers, "run").plant_found("marker", "surviving")
    present = frozenset(FakeMarker(markers, "run").inventory()["containers"])
    assert present == {selector}
    lane = _lane_of(
        tmp_path, [PLAN, _issue("", "up", release=RUN_GROUP), _confirm("", "up", selector)]
    )
    swept = [
        {
            "kind": "sweep_disposition",
            "target": {"path": "", "effect": "up"},
            "disposition": "released",
        }
    ]
    assert not released_means_absent(swept, lane, present).ok
    assert released_means_absent(swept, lane, frozenset()).ok  # absent: fine
    assert released_means_absent([], lane, present).vacuous  # nothing was recorded released
