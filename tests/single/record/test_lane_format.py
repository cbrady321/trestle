"""L.SV-1.1: the written entry classes of the attempt lane, their V-13 bounds, and the read side
(MC-19; SA-01, SA-05, SA-14)."""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from tests.single.record import support as sup
from trestle.common import clock
from trestle.common import lane_format as lf
from trestle.common.fsutil import append_ndjson
from trestle.common.plan import bounds

REPO = Path(__file__).resolve().parents[3]


def _bytes_of(entry: lf.Entry, seq: int) -> bytes:
    return lf.encode_entry(entry, seq)


def test_every_entry_class_roundtrips() -> None:
    seen: set[str] = set()
    for seq, entry in enumerate(sup.every_entry_class(), start=1):
        raw = _bytes_of(entry, seq)
        assert (
            raw == json.dumps(json.loads(raw), separators=(",", ":"), ensure_ascii=False).encode()
        )
        decoded = lf.decode_entry(raw, sup.ROOT)
        assert decoded == entry  # every field, present or absent
        assert decoded.seq == seq
        assert lf.encode_entry(decoded) == raw  # byte-stable
        seen.add(json.loads(raw)["class"])
    assert seen == set(lf.ENTRY_CLASSES)


def test_descriptor_forms_roundtrip() -> None:
    for descriptor in sup.DESCRIPTORS:
        entry = sup.issue_entry("svc", release=descriptor)
        decoded = lf.decode_entry(_bytes_of(entry, 2), sup.ROOT)
        assert isinstance(decoded, lf.IssueEntry)
        assert decoded.release == descriptor
        assert type(decoded.release) is type(descriptor)
    assert sup.ARGV.remove_argv is not None and sup.ARGV_NO_REMOVE.remove_argv is None
    # the step's handle carries a descriptor too
    step = sup.step_entry("svc", with_handle=sup.handle("svc"))
    decoded_step = lf.decode_entry(_bytes_of(step, 3), sup.ROOT)
    assert isinstance(decoded_step, lf.StepEntry) and decoded_step.handle == sup.handle("svc")
    assert lf.ArgvRelease("/x", (), frozenset(), (), timedelta(seconds=1)).remove_argv is None


def test_format_version_on_first_entry() -> None:
    first = json.loads(_bytes_of(sup.plan_entry(1), 1))
    second = json.loads(_bytes_of(sup.issue_entry("svc"), 2))
    assert first["lane_format"] == lf.LANE_FORMAT == 1 and first["seq"] == 1
    assert "lane_format" not in second
    lane = sup.lane_file(Path("/nonexistent-root-for-format"))  # path only, nothing is read
    assert lane.name == "lane.ndjson" and lane.parent.name == "evidence"


def test_reader_skips_unknown_class_reports_it(tmp_path: Path) -> None:
    lane = sup.lane_file(tmp_path)
    append_ndjson(lane, lf.encode_record(sup.plan_entry(1), 1))
    append_ndjson(lane, {"class": "ticket", "seq": 2, "path": "svc"})  # never a written class
    append_ndjson(lane, {"class": "from-the-future", "seq": 3})
    append_ndjson(lane, lf.encode_record(sup.issue_entry("svc"), 4))
    read = lf.read_lane(lane)
    assert [type(e) for e in read.entries] == [lf.PlanEntry, lf.IssueEntry]
    assert read.unknown == 2 and not read.torn and read.format == 1


def test_undecodable_and_torn_lines_set_torn(tmp_path: Path) -> None:
    lane = sup.lane_file(tmp_path)
    append_ndjson(lane, lf.encode_record(sup.plan_entry(1), 1))
    append_ndjson(lane, {"class": "issue", "seq": 2, "path": "svc"})  # fields missing
    append_ndjson(lane, lf.encode_record(sup.issue_entry("svc"), 3))
    read = lf.read_lane(lane)
    assert read.torn and len(read.entries) == 2
    good = tmp_path / "run-b" / "evidence" / "lane.ndjson"
    append_ndjson(good, lf.encode_record(sup.plan_entry(1), 1))
    assert not lf.read_lane(good).torn
    with good.open("ab") as fh:
        fh.write(b'{"class":"issue","seq":2,"pa')  # a torn tail
    torn = lf.read_lane(good)
    assert torn.torn and len(torn.entries) == 1
    assert lf.committed_length(good) == len(_bytes_of(sup.plan_entry(1), 1)) + 1
    assert lf.read_lane(tmp_path / "absent" / "evidence" / "lane.ndjson") == lf.LaneRead(
        (), False, 0, None
    )


def test_root_derived_from_run_dir_not_field(tmp_path: Path) -> None:
    lane = sup.lane_file(tmp_path, "run-from-dir")
    planted = lf.encode_record(sup.issue_entry("svc"), 2)
    planted["root_run_id"] = "someone-elses-run"
    append_ndjson(lane, lf.encode_record(sup.plan_entry(1), 1))
    append_ndjson(lane, planted)
    read = lf.read_lane(lane)
    issue = read.entries[1]
    assert isinstance(issue, lf.IssueEntry)
    assert issue.lineage == lf.Lineage("run-from-dir", ("svc",))
    # the writer never emits a root id
    assert "root_run_id" not in lf.encode_record(sup.issue_entry("svc"), 2)


def test_ticket_entry_assembled_never_written() -> None:
    issue = sup.issue_entry("svc", release=sup.ARGV)
    entries: list[lf.Entry] = [
        issue,
        sup.confirmation_entry("svc"),
        sup.result_entry("svc", effect="up"),
        sup.released_entry("svc", outcome="release.done"),
        sup.issue_entry("svc", effect="other", attempt=1, facet=lf.EffectFacetClass.EVENT),
        sup.confirmation_entry("svc", effect="other", identity=None),
        sup.confirmation_entry("ghost", effect="none"),  # no issue entry: ignored
    ]
    tickets = lf.assemble_tickets(entries)
    assert [t.effect for t in tickets] == ["up", "other"]
    up, other = tickets
    assert up.confirmation is not None and up.result is not None
    assert up.handle == lf.CreatedHandle(issue.lineage, "up", "sel-1", sup.ARGV)
    assert up.released_at == sup.LATER and up.release_outcome == "release.done"
    assert other.handle is None and other.confirmation is not None  # facet is not CREATE
    with pytest.raises(TypeError):
        lf.encode_entry(up)  # type: ignore[arg-type]
    assert "ticket" not in lf.ENTRY_CLASSES
    unconfirmed = lf.assemble_tickets([issue])[0]
    assert unconfirmed.confirmation is None and unconfirmed.handle is None


def test_not_applied_or_non_create_confirmation_yields_no_handle() -> None:
    issue = sup.issue_entry("svc")
    for status in (lf.ConfirmationStatus.NOT_APPLIED, lf.ConfirmationStatus.UNKNOWN):
        (ticket,) = lf.assemble_tickets(
            [issue, sup.confirmation_entry("svc", status=status, identity=None)]
        )
        assert ticket.handle is None and ticket.confirmation is not None


# --- V-13 bounds -----------------------------------------------------------------------------

Build = Callable[[str], lf.Entry]

TEXT_CASES: dict[str, tuple[int, Build]] = {
    "issue.effect": (bounds.NAME_MAX, lambda v: sup.issue_entry("svc", effect=v)),
    "issue.path segment": (bounds.NAME_MAX, lambda v: sup.issue_entry(v)),
    "issue.remedy.code": (
        bounds.CODE_MAX,
        lambda v: sup.issue_entry("svc", remedy=lf.RemedyGrant(v, "up", 1)),
    ),
    "issue.remedy.effect": (
        bounds.NAME_MAX,
        lambda v: sup.issue_entry("svc", remedy=lf.RemedyGrant("c", v, 1)),
    ),
    "issue.release.executable": (
        bounds.EXEC_PATH_MAX,
        lambda v: sup.issue_entry("svc", release=replace(sup.ARGV_NO_REMOVE, executable=v)),
    ),
    "confirmation.code": (bounds.CODE_MAX, lambda v: sup.confirmation_entry("svc", code=v)),
    "confirmation.identity": (
        bounds.TOKEN_MAX,
        lambda v: sup.confirmation_entry("svc", identity=v),
    ),
    "result.code": (bounds.CODE_MAX, lambda v: sup.result_entry("t", code=v)),
    "released.outcome": (bounds.CODE_MAX, lambda v: sup.released_entry("svc", outcome=v)),
    "step.code": (bounds.CODE_MAX, lambda v: sup.step_entry("svc", code=v)),
    "step.human_action": (
        bounds.HUMAN_ACTION_MAX,
        lambda v: sup.step_entry("svc", human_action=v),
    ),
    "step.handle.selector": (
        bounds.TOKEN_MAX,
        lambda v: sup.step_entry("svc", with_handle=sup.handle("svc", selector=v)),
    ),
    "step.handle.effect": (
        bounds.NAME_MAX,
        lambda v: sup.step_entry("svc", with_handle=sup.handle("svc", effect=v)),
    ),
    "end.code": (bounds.CODE_MAX, lambda v: sup.node_end("svc", code=v)),
    "end.human_action": (bounds.HUMAN_ACTION_MAX, lambda v: sup.node_end("svc", human_action=v)),
    "plan.declaration_digest": (
        bounds.TOKEN_MAX,
        lambda v: lf.PlanEntry(replace(sup.plan_entry(1).plan, declaration_digest=v)),
    ),
    "plan.args_hash": (
        bounds.TOKEN_MAX,
        lambda v: lf.PlanEntry(replace(sup.plan_entry(1).plan, args_hash=v)),
    ),
    "plan.observations_digest": (
        bounds.TOKEN_MAX,
        lambda v: lf.PlanEntry(replace(sup.plan_entry(1).plan, observations_digest=v)),
    ),
}


@pytest.mark.parametrize("name", sorted(TEXT_CASES))
def test_field_over_bound_refused_over_bound_nothing_written(name: str, tmp_path: Path) -> None:
    limit, build = TEXT_CASES[name]
    at_bound = build("a" * limit)
    lf.encode_entry(at_bound, 2)  # at the bound it encodes
    over = build("a" * (limit + 1))
    lane = sup.lane_file(tmp_path)
    with pytest.raises(lf.OverBound) as refused:
        lf.encode_entry(over, 2)
    assert refused.value.refusal is lf.LaneRefusal.OVER_BOUND
    assert not lane.exists()  # the codec wrote nothing and truncated nothing


@pytest.mark.parametrize(
    ("name", "limit", "build"),
    [
        ("issue.effect", bounds.NAME_MAX, TEXT_CASES["issue.effect"][1]),
        ("confirmation.identity", bounds.TOKEN_MAX, TEXT_CASES["confirmation.identity"][1]),
        ("end.human_action", bounds.HUMAN_ACTION_MAX, TEXT_CASES["end.human_action"][1]),
        ("step.code", bounds.CODE_MAX, TEXT_CASES["step.code"][1]),
    ],
)
def test_bound_is_measured_on_the_json_encoding_escapes_included(
    name: str, limit: int, build: Build
) -> None:
    """A quote costs two bytes on the lane (`\\"`), so half a bound of quotes is at the bound
    and one quote more is over it (V-13 'What a byte bound measures')."""
    half = limit // 2
    assert bounds.text_bytes('"' * half) == 2 * half
    lf.encode_entry(build('"' * half), 2)
    with pytest.raises(lf.OverBound):
        lf.encode_entry(build('"' * (half + 1)), 2)
    # a multi-byte character counts its UTF-8 bytes
    assert bounds.text_bytes("é" * 5) == 10


def test_code_is_printable_ascii_and_paths_are_well_formed() -> None:
    with pytest.raises(lf.OverBound):
        lf.encode_entry(sup.step_entry("svc", code="café"), 2)
    with pytest.raises(lf.OverBound):
        lf.encode_entry(sup.step_entry("svc", code="two\nlines"), 2)
    for bad in (("a", ""), ("a/b",)):
        with pytest.raises(lf.OverBound):
            lf.encode_entry(sup.node_end(*bad), 2)
    with pytest.raises(lf.OverBound):  # the whole path over PATH_MAX, each segment within NAME_MAX
        lf.encode_entry(sup.node_end(*(("a" * bounds.NAME_MAX,) * 4)), 2)
    assert lf.decode_path(lf.encode_path(())) == ()
    assert lf.decode_path(lf.encode_path(sup.max_path())) == sup.max_path()


def _max_argv() -> lf.ArgvRelease:
    """An ArgvRelease encoded at exactly ARGV_RELEASE_MAX."""
    base = lf.ArgvRelease(
        executable="/" + "e" * 100,
        observe_argv=("o",),
        observe_ok_exit=frozenset({0}),
        stop_argv=("s",),
        timeout=timedelta(seconds=30),
        remove_argv=("",),
    )

    def size(release: lf.ArgvRelease) -> int:
        assert release.remove_argv is not None
        argv = [release.executable, list(release.observe_argv), list(release.stop_argv)]
        argv.append(list(release.remove_argv))
        return len(json.dumps(argv, separators=(",", ":"), ensure_ascii=False).encode())

    pad = bounds.ARGV_RELEASE_MAX - size(base)
    release = replace(base, remove_argv=("r" * pad,))
    assert size(release) == bounds.ARGV_RELEASE_MAX
    return release


def test_argv_release_bound_is_on_the_encoded_argv_set() -> None:
    at_bound = _max_argv()
    lf.encode_entry(sup.issue_entry("svc", release=at_bound), 2)
    assert at_bound.remove_argv is not None
    over = replace(at_bound, remove_argv=(at_bound.remove_argv[0] + "r",))
    with pytest.raises(lf.OverBound):
        lf.encode_entry(sup.issue_entry("svc", release=over), 2)
    many = replace(at_bound, observe_argv=("",) * 200)  # punctuation counts, not only text
    with pytest.raises(lf.OverBound):
        lf.encode_entry(sup.issue_entry("svc", release=many), 2)


def _maximal_entries() -> list[lf.Entry]:
    path = sup.max_path()
    code = "c" * bounds.CODE_MAX
    name = "n" * bounds.NAME_MAX
    token = "t" * bounds.TOKEN_MAX
    action = "h" * bounds.HUMAN_ACTION_MAX
    argv = _max_argv()
    handle = lf.CreatedHandle(sup.lineage(*path), name, token, argv)
    quotes = '"' * (bounds.HUMAN_ACTION_MAX // 2)
    return [
        sup.issue_entry(
            *path, effect=name, release=argv, remedy=lf.RemedyGrant(code, name, 2**31 - 1)
        ),
        sup.confirmation_entry(*path, effect=name, code=code, identity=token),
        sup.result_entry(*path, effect=name, code=code, counts=lf.TestCounts(2**31 - 1, 0, 0, 1)),
        sup.released_entry(*path, effect=name, outcome=code),
        sup.step_entry(
            *path,
            kind=lf.StepKind.BLOCKED,
            code=code,
            human_action=action,
            resend=lf.Resend.SUCCEEDS_AFTER_ACTION,
            with_handle=handle,
        ),
        sup.step_entry(*path, code=code, human_action=quotes, with_handle=handle),
        sup.node_end(
            *path,
            condition=lf.Condition.INCOMPATIBLE,
            code=code,
            human_action=action,
            resend=lf.Resend.SUCCEEDS_AFTER_ACTION,
            provenance=lf.Provenance.CREATED,
            cut=lf.Cut.STOPPED,
        ),
    ]


def test_every_entry_at_maximal_fields_fits_lane_entry_max() -> None:
    for entry in _maximal_entries():
        raw = lf.encode_entry(entry, 2**31 - 1)
        assert len(raw) <= bounds.LANE_ENTRY_MAX, (type(entry).__name__, len(raw))
        assert lf.decode_entry(raw, sup.ROOT) == replace(entry, seq=0)


@pytest.mark.parametrize("k", [0, 1, 7, 200, bounds.VERTEX_MAX])
def test_plan_entry_fits_plan_entry_max(k: int) -> None:
    long = sup.max_path()
    selection = {
        (*long[:3], f"{i:04d}" + "x" * 121): (*long[:3], f"{i:04d}" + "y" * 121) for i in range(k)
    }
    assert all(bounds.text_bytes("/".join(p)) <= bounds.PATH_MAX for p in selection)
    plan = lf.PlanIdentity("d" * bounds.TOKEN_MAX, "a" * bounds.TOKEN_MAX, selection, "o" * 256)
    raw = lf.encode_entry(lf.PlanEntry(plan), 1)
    assert len(raw) <= lf.PLAN_ENTRY_MAX(plan)
    assert lf.PLAN_ENTRY_MAX(plan) == lf.PLAN_ENTRY_FIXED + 2 * k * bounds.PATH_MAX
    decoded = lf.decode_entry(raw, sup.ROOT)
    assert isinstance(decoded, lf.PlanEntry) and dict(decoded.plan.selection) == selection


def test_node_end_human_action_at_bound_byte_identical() -> None:
    for text in ("h" * bounds.HUMAN_ACTION_MAX, "é" * (bounds.HUMAN_ACTION_MAX // 2), '"' * 512):
        assert bounds.text_bytes(text) <= bounds.HUMAN_ACTION_MAX
        end = sup.node_end(
            "svc", condition=lf.Condition.BLOCKED, human_action=text, resend=lf.Resend.UNKNOWN
        )
        decoded = lf.decode_entry(lf.encode_entry(end, 5), sup.ROOT)
        assert isinstance(decoded, lf.NodeEnd)
        assert decoded.human_action == text  # never truncated (WR-TERM-4)
        assert decoded.human_action.encode() == text.encode()
    with pytest.raises(lf.OverBound):
        lf.encode_entry(sup.node_end("svc", human_action="h" * (bounds.HUMAN_ACTION_MAX + 1)), 5)


def test_node_record_with_held_durable_then_held() -> None:
    durable = [
        sup.issue_entry("svc"),
        sup.step_entry("svc", code="first"),
        sup.node_end("svc"),
        sup.step_entry("other", code="not-mine"),
        sup.step_entry("svc", code="second"),
    ]
    record = lf.node_record(durable, ("svc",))
    assert [s.code for s in record.steps] == ["first", "second"]  # a NodeEnd is never a step
    assert len(record.tickets) == 1
    held = (sup.step_entry("svc", code="held-1"), sup.step_entry("svc", code="held-2"))
    joined = record.with_held(held)
    assert [s.code for s in joined.steps] == ["first", "second", "held-1", "held-2"]
    assert joined.tickets == record.tickets
    assert (
        record.with_held(()) == record and len(record.steps) == 2
    )  # the durable part is untouched


# --- SA-05 / SA-14: one definition of each V-13 bound, re-exported by import ------------------


def _defs(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


@pytest.mark.parametrize("name", ["LANE_ENTRY_MAX", "LANE_BASE_ENTRIES"])
def test_lane_bounds_have_one_definition_and_clock_reexports_it(name: str) -> None:
    defined_in = [
        str(path.relative_to(REPO))
        for path in sorted((REPO / "trestle").rglob("*.py"))
        if name in _defs(ast.parse(path.read_text()))
    ]
    assert defined_in == ["trestle/common/plan/bounds.py"], defined_in
    assert getattr(clock, name) == getattr(bounds, name)
    assert (clock.LANE_ENTRY_MAX, clock.LANE_BASE_ENTRIES) == (8 * 1024, 4096)


def test_bounds_module_is_stdlib_only() -> None:
    tree = ast.parse((REPO / "trestle" / "common" / "plan" / "bounds.py").read_text())
    imported = {
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [None])
    }
    assert imported <= {"__future__", "json"}, imported
