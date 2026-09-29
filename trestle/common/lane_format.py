"""Lane record format v1 (L.SV-1.1; MC-19): the written entry classes of the attempt lane.

The lane is `<run_dir>/evidence/lane.ndjson`, one JSON object per line, framed and made durable
by `trestle.common.fsutil.append_ndjson` (CS-1 framing: a newline-less valid tail is terminated,
a torn tail truncated, no committed byte rewritten). This module is the codec and nothing else:
one encoder/decoder per written entry class of V-4 and V-4.8 (the plan entry, the issue entry,
the confirmation, the result, the released entry, `StepEntry`, `NodeEnd`), the V-10 release
descriptors as data, the V-4 read views the fold assembles (`TicketEntry`, `NodeRecord`), and
`read_lane`. The writer is `trestle/child/attempt_lane.py` (L.SV-1.2), the fold
`trestle/server/fold.py` (L.SV-1.3); `tests/proof/records.py` reads the same file with its own
oracle (L.SV-1.4).

The lane never stores a root run id: the root is the run directory's (B2-I3), so the lineage key
of a decoded entry is (the run directory's name, `entry.lineage.path`). A `NodePath` is written
as its canonical encoded string (segments joined by "/", the root path being ""), the form V-13's
`PATH_MAX` measures; a segment is non-empty and holds no "/".

Every text field is bounded by its V-13 bound (`trestle.common.plan.bounds`), measured on its JSON
string encoding, escapes included. The encoder refuses a field over its bound with `OverBound`,
writing nothing and truncating nothing (V-4 `LaneRefusal.OVER_BOUND`). A `TicketEntry` is a fold
view assembled from written entries and is never written (V-13 `LANE_ENTRY_MAX`).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, final

from trestle.common.plan import bounds

LANE_FORMAT: Final[int] = 1
LANE_FILE: Final[str] = "lane.ndjson"

# The fixed part of PLAN_ENTRY_MAX (V-13: fixed part + 2 x CHOICE vertices in scope x PATH_MAX).
# Executor-chosen: three TOKEN_MAX digests, the keys, and the JSON punctuation of every selection
# pair (6 B a pair, up to VERTEX_MAX pairs) fit with room to spare.
PLAN_ENTRY_FIXED: Final[int] = 8 * 1024

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


# ------------------------------------------------------------------ vocabulary (V-0..V-10)


class Repeat(StrEnum):
    SAFE = "safe"
    ONCE = "once"


class Lifetime(StrEnum):
    RUN = "run"
    DURABLE = "durable"


class EffectFacetClass(StrEnum):
    CREATE = "create"
    OWNED = "owned"
    SAFE_START = "safe_start"
    EVENT = "event"


class ConfirmationStatus(StrEnum):
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"


class StepKind(StrEnum):
    BLOCKED = "blocked"
    FAILED = "failed"
    NO_ACTION = "no_action"


class Cut(StrEnum):
    STOPPED = "stopped"
    NOT_STARTED = "not_started"


class Condition(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    CONVERGING = "converging"
    STALE = "stale"
    IN_DOUBT = "in_doubt"
    INCOMPATIBLE = "incompatible"
    BLOCKED = "blocked"
    FAILED = "failed"


class Provenance(StrEnum):
    ABSENT = "absent"
    FOUND = "found"
    CLAIMED = "claimed"
    CREATED = "created"


class Resend(StrEnum):
    SUCCEEDS_AFTER_ACTION = "succeeds_after_action"
    WILL_NOT_SUCCEED = "will_not_succeed"
    UNKNOWN = "unknown"


class DurableOwner(StrEnum):
    HOST = "host"
    ENVIRONMENT = "environment"


class LaneRefusal(StrEnum):
    """What a lane write returns instead of None (V-4, B2-C7)."""

    UNAVAILABLE = "unavailable"
    FULL = "full"
    OVER_BOUND = "over_bound"
    DUPLICATE = "duplicate"


class OverBound(ValueError):  # noqa: N818 (named for the V-4 refusal it carries)
    """A field over its V-13 bound (or an entry over LANE_ENTRY_MAX / PLAN_ENTRY_MAX). Nothing
    is written and nothing is truncated; the writer maps it to `LaneRefusal.OVER_BOUND`."""

    refusal = LaneRefusal.OVER_BOUND


class DecodeError(ValueError):
    """A committed line that parses as JSON but is not a well-formed entry of its class."""


@final
@dataclass(frozen=True, slots=True)
class Lineage:
    root_run_id: str  # the run directory's name; never read from an entry (B2-I3)
    path: tuple[str, ...]  # declared names from the root; () is the root itself


@final
@dataclass(frozen=True, slots=True)
class TestCounts:
    __test__ = False  # not a pytest class
    passed: int
    failed: int
    errors: int
    skipped: int


@final
@dataclass(frozen=True, slots=True)
class RecordedResult:
    passed: bool
    code: str | None
    counts: TestCounts | None


@final
@dataclass(frozen=True, slots=True)
class RemedyGrant:
    code: str
    effect: str
    attempt: int


@final
@dataclass(frozen=True, slots=True)
class Confirmation:
    status: ConfirmationStatus
    code: str | None
    identity: str | None


@final
@dataclass(frozen=True, slots=True)
class InRunGroup:
    helpers_disclosed: bool = False


@final
@dataclass(frozen=True, slots=True)
class ArgvRelease:
    executable: str
    observe_argv: tuple[str, ...]
    observe_ok_exit: frozenset[int]
    stop_argv: tuple[str, ...]
    timeout: timedelta
    remove_argv: tuple[str, ...] | None = None


@final
@dataclass(frozen=True, slots=True)
class Durable:
    owner: DurableOwner


type ReleaseDescriptor = InRunGroup | ArgvRelease | Durable


@final
@dataclass(frozen=True, slots=True)
class CreatedHandle:
    lineage: Lineage
    effect: str
    selector: str
    release: ReleaseDescriptor


@final
@dataclass(frozen=True, slots=True)
class PlanIdentity:
    declaration_digest: str
    args_hash: str
    selection: Mapping[tuple[str, ...], tuple[str, ...]]  # CHOICE path -> selected alternative
    observations_digest: str


# ----------------------------------------------------------------------------- written entries


@dataclass(frozen=True, slots=True)
class _Entry:
    """Base of every written entry: `seq` is the append order the writer assigned (1 on the first
    entry, strictly increasing). It is a property of the file, not of the entry's value."""

    seq: int = field(default=0, compare=False, kw_only=True)


@final
@dataclass(frozen=True, slots=True)
class PlanEntry(_Entry):
    plan: PlanIdentity


@final
@dataclass(frozen=True, slots=True)
class IssueEntry(_Entry):
    """The fields of V-4 `AttemptTicket`, plus `issued_at` (when this entry became durable)."""

    lineage: Lineage
    effect: str
    facet: EffectFacetClass
    attempt: int
    repeat: Repeat
    lifetime: Lifetime
    release: ReleaseDescriptor
    remedy: RemedyGrant | None
    issued_at: datetime


@final
@dataclass(frozen=True, slots=True)
class ConfirmationEntry(_Entry):
    lineage: Lineage
    effect: str
    attempt: int
    confirmation: Confirmation


@final
@dataclass(frozen=True, slots=True)
class ResultEntry(_Entry):
    lineage: Lineage
    effect: str
    attempt: int
    result: RecordedResult


@final
@dataclass(frozen=True, slots=True)
class ReleasedEntry(_Entry):
    lineage: Lineage
    effect: str
    attempt: int
    released_at: datetime
    outcome: str | None  # TicketEntry.release_outcome


@final
@dataclass(frozen=True, slots=True)
class StepEntry(_Entry):
    lineage: Lineage
    at: datetime
    kind: StepKind
    code: str
    human_action: str | None
    resend: Resend | None
    handle: CreatedHandle | None


@final
@dataclass(frozen=True, slots=True)
class NodeEnd(_Entry):
    lineage: Lineage
    at: datetime
    condition: Condition | None
    code: str | None
    human_action: str | None
    resend: Resend | None
    provenance: Provenance | None
    cut: Cut | None


type Entry = (
    PlanEntry | IssueEntry | ConfirmationEntry | ResultEntry | ReleasedEntry | StepEntry | NodeEnd
)

_CLASS_OF: Final[dict[type, str]] = {
    PlanEntry: "plan",
    IssueEntry: "issue",
    ConfirmationEntry: "confirmation",
    ResultEntry: "result",
    ReleasedEntry: "released",
    StepEntry: "step",
    NodeEnd: "end",
}
ENTRY_CLASSES: Final[tuple[str, ...]] = tuple(_CLASS_OF.values())


# ----------------------------------------------------------------------------- read views (V-4)


@final
@dataclass(frozen=True, slots=True)
class TicketEntry:
    """One attempt as the fold assembles it from its written entries; never written."""

    lineage: Lineage
    effect: str
    facet: EffectFacetClass
    attempt: int
    repeat: Repeat
    lifetime: Lifetime
    release: ReleaseDescriptor
    remedy: RemedyGrant | None
    issued_at: datetime
    confirmation: Confirmation | None = None
    handle: CreatedHandle | None = None
    result: RecordedResult | None = None
    released_at: datetime | None = None
    release_outcome: str | None = None


@final
@dataclass(frozen=True, slots=True)
class NodeRecord:
    """The join's record input for one node (V-4.5): its tickets and steps in record order. A
    `NodeEnd` is the vertex's end and never a step."""

    tickets: tuple[TicketEntry, ...]
    steps: tuple[StepEntry, ...]

    def with_held(self, steps: Iterable[StepEntry]) -> NodeRecord:
        """The durable part, then the steps the lane refused to hold (kept in process by the
        loop, V-4.5), in the order given."""
        return NodeRecord(self.tickets, self.steps + tuple(steps))


@final
@dataclass(frozen=True, slots=True)
class LaneRead:
    entries: tuple[Entry, ...]  # known classes, in file order, each with its `seq`
    torn: bool  # a committed line is not a well-formed entry (torn, corrupt or undecodable)
    unknown: int  # lines of a class this reader does not know: skipped and counted
    format: int | None  # `lane_format` as the first entry that carries it wrote it


# ----------------------------------------------------------------------------- bounds


def PLAN_ENTRY_MAX(plan: PlanIdentity) -> int:  # noqa: N802 (V-13's name)
    """V-13: the fixed part + 2 x (CHOICE vertices in scope) x PATH_MAX. The selection holds at
    most one pair per CHOICE vertex, so its size is the count the bound is taken over."""
    return PLAN_ENTRY_FIXED + 2 * len(plan.selection) * bounds.PATH_MAX


def _bounded(name: str, value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise OverBound(f"{name} is not text")
    try:
        size = bounds.text_bytes(value)
    except UnicodeEncodeError as exc:
        raise OverBound(f"{name} is not encodable") from exc
    if size > limit:
        raise OverBound(f"{name} is {size} B over its {limit} B bound")
    return value


def _code(name: str, value: object) -> str:
    text = _bounded(name, value, bounds.CODE_MAX)
    if not text.isascii() or not text.isprintable():
        raise OverBound(f"{name} is not printable ASCII")
    return text


def _opt(fn: Any, name: str, value: object, *args: Any) -> str | None:
    return None if value is None else str(fn(name, value, *args))


def _segments_ok(name: str, path: tuple[str, ...]) -> str:
    for seg in path:
        if not seg or "/" in seg:
            raise OverBound(f"{name} has an empty or '/'-holding segment")
        _bounded(name, seg, bounds.NAME_MAX)
    return _bounded(name, "/".join(path), bounds.PATH_MAX)


def encode_path(path: tuple[str, ...]) -> str:
    """The canonical encoded NodePath: segments joined by "/" (the root path is "")."""
    return _segments_ok("path", path)


def decode_path(text: str) -> tuple[str, ...]:
    return tuple(text.split("/")) if text else ()


# ----------------------------------------------------------------------------- encoding


def _iso(name: str, when: datetime) -> str:
    if when.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return when.astimezone(UTC).strftime(_TIME_FORMAT)


def _enc_descriptor(release: ReleaseDescriptor) -> dict[str, Any]:
    if isinstance(release, InRunGroup):
        return {"form": "in_run_group", "helpers_disclosed": bool(release.helpers_disclosed)}
    if isinstance(release, Durable):
        return {"form": "durable", "owner": DurableOwner(release.owner).value}
    if isinstance(release, ArgvRelease):
        _bounded("release.executable", release.executable, bounds.EXEC_PATH_MAX)
        remove = None if release.remove_argv is None else list(release.remove_argv)
        record: dict[str, Any] = {
            "form": "argv",
            "executable": release.executable,
            "observe_argv": list(release.observe_argv),
            "observe_ok_exit": sorted(release.observe_ok_exit),
            "stop_argv": list(release.stop_argv),
            "timeout_s": release.timeout.total_seconds(),
            "remove_argv": remove,
        }
        argv_part = [record["executable"], record["observe_argv"], record["stop_argv"], remove]
        try:
            encoded = _dumps(argv_part)
        except UnicodeEncodeError as exc:
            raise OverBound("release argv is not encodable") from exc
        if len(encoded) > bounds.ARGV_RELEASE_MAX:
            raise OverBound(f"release argv is {len(encoded)} B over its bound")
        return record
    raise TypeError(f"not a release descriptor: {type(release).__name__}")


def _enc_handle(handle: CreatedHandle | None) -> dict[str, Any] | None:
    if handle is None:
        return None
    return {
        "path": encode_path(handle.lineage.path),
        "effect": _bounded("handle.effect", handle.effect, bounds.NAME_MAX),
        "selector": _bounded("handle.selector", handle.selector, bounds.TOKEN_MAX),
        "release": _enc_descriptor(handle.release),
    }


def _enc_remedy(remedy: RemedyGrant | None) -> dict[str, Any] | None:
    if remedy is None:
        return None
    return {
        "code": _code("remedy.code", remedy.code),
        "effect": _bounded("remedy.effect", remedy.effect, bounds.NAME_MAX),
        "attempt": int(remedy.attempt),
    }


def _enc_counts(counts: TestCounts | None) -> dict[str, int] | None:
    if counts is None:
        return None
    return {
        "passed": int(counts.passed),
        "failed": int(counts.failed),
        "errors": int(counts.errors),
        "skipped": int(counts.skipped),
    }


def _enc_selection(sel: Mapping[tuple[str, ...], tuple[str, ...]]) -> dict[str, str]:
    return {encode_path(k): encode_path(v) for k, v in sel.items()}


def _ticket_key(e: IssueEntry | ConfirmationEntry | ResultEntry | ReleasedEntry) -> dict[str, Any]:
    return {
        "path": encode_path(e.lineage.path),
        "effect": _bounded("effect", e.effect, bounds.NAME_MAX),
        "attempt": int(e.attempt),
    }


def _body(entry: Entry) -> dict[str, Any]:
    match entry:
        case PlanEntry(plan=plan):
            return {
                "declaration_digest": _bounded(
                    "plan.declaration_digest", plan.declaration_digest, bounds.TOKEN_MAX
                ),
                "args_hash": _bounded("plan.args_hash", plan.args_hash, bounds.TOKEN_MAX),
                "selection": _enc_selection(plan.selection),
                "observations_digest": _bounded(
                    "plan.observations_digest", plan.observations_digest, bounds.TOKEN_MAX
                ),
            }
        case IssueEntry():
            return {
                **_ticket_key(entry),
                "facet": EffectFacetClass(entry.facet).value,
                "repeat": Repeat(entry.repeat).value,
                "lifetime": Lifetime(entry.lifetime).value,
                "release": _enc_descriptor(entry.release),
                "remedy": _enc_remedy(entry.remedy),
                "issued_at": _iso("issued_at", entry.issued_at),
            }
        case ConfirmationEntry(confirmation=conf):
            return {
                **_ticket_key(entry),
                "status": ConfirmationStatus(conf.status).value,
                "code": _opt(_code, "confirmation.code", conf.code),
                "identity": _opt(
                    _bounded, "confirmation.identity", conf.identity, bounds.TOKEN_MAX
                ),
            }
        case ResultEntry(result=res):
            return {
                **_ticket_key(entry),
                "passed": bool(res.passed),
                "code": _opt(_code, "result.code", res.code),
                "counts": _enc_counts(res.counts),
            }
        case ReleasedEntry():
            return {
                **_ticket_key(entry),
                "released_at": _iso("released_at", entry.released_at),
                "outcome": _opt(_code, "release_outcome", entry.outcome),
            }
        case StepEntry():
            return {
                "path": encode_path(entry.lineage.path),
                "at": _iso("at", entry.at),
                "kind": StepKind(entry.kind).value,
                "code": _code("step.code", entry.code),
                "human_action": _opt(
                    _bounded, "human_action", entry.human_action, bounds.HUMAN_ACTION_MAX
                ),
                "resend": None if entry.resend is None else Resend(entry.resend).value,
                "handle": _enc_handle(entry.handle),
            }
        case NodeEnd():
            return {
                "path": encode_path(entry.lineage.path),
                "at": _iso("at", entry.at),
                "condition": None if entry.condition is None else Condition(entry.condition).value,
                "code": _opt(_code, "end.code", entry.code),
                "human_action": _opt(
                    _bounded, "human_action", entry.human_action, bounds.HUMAN_ACTION_MAX
                ),
                "resend": None if entry.resend is None else Resend(entry.resend).value,
                "provenance": (
                    None if entry.provenance is None else Provenance(entry.provenance).value
                ),
                "cut": None if entry.cut is None else Cut(entry.cut).value,
            }
    raise TypeError(
        f"not a written lane entry: {type(entry).__name__} (a TicketEntry is a fold view)"
    )


def _dumps(record: object) -> bytes:
    """The one serialization (identical to `fsutil.append_ndjson`'s), so an encoded size is the
    size of the bytes on disk less the newline."""
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode_record(entry: Entry, seq: int | None = None) -> dict[str, Any]:
    """The JSON object for `entry`, every bound enforced. `seq` (default: the entry's own) is 1
    on the first entry of a lane, and that entry also carries `lane_format` = LANE_FORMAT."""
    if type(entry) not in _CLASS_OF:
        raise TypeError(
            f"not a written lane entry: {type(entry).__name__} (a TicketEntry is a fold view)"
        )
    number = entry.seq if seq is None else seq
    body = _body(entry)
    record: dict[str, Any] = {"class": _CLASS_OF[type(entry)], "seq": int(number)}
    if number == 1:
        record["lane_format"] = LANE_FORMAT
    record.update(body)
    size = len(_dumps(record))
    if isinstance(entry, PlanEntry):
        if size > PLAN_ENTRY_MAX(entry.plan):
            raise OverBound(f"plan entry is {size} B over PLAN_ENTRY_MAX")
    elif size > bounds.LANE_ENTRY_MAX:
        raise OverBound(f"entry is {size} B over LANE_ENTRY_MAX")
    return record


def encode_entry(entry: Entry, seq: int | None = None) -> bytes:
    """The bytes of one entry, without the newline."""
    return _dumps(encode_record(entry, seq))


# ----------------------------------------------------------------------------- decoding


class UnknownClass(Exception):  # noqa: N818
    """A line of an entry class this reader does not know."""


def _get(rec: Mapping[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in rec:
        raise DecodeError(f"missing {key}")
    value = rec[key]
    kinds = kind if isinstance(kind, tuple) else (kind,)
    if isinstance(value, bool) and bool not in kinds:
        raise DecodeError(f"{key} has the wrong type")
    if not isinstance(value, kind):
        raise DecodeError(f"{key} has the wrong type")
    return value


def _get_opt(rec: Mapping[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in rec or rec[key] is None:
        return None
    return _get(rec, key, kind)


def _enum[E: StrEnum](cls: type[E], value: Any) -> E:
    try:
        return cls(value)
    except ValueError as exc:
        raise DecodeError(f"{value!r} is not a {cls.__name__}") from exc


def _time(rec: Mapping[str, Any], key: str) -> datetime:
    text = _get(rec, key, str)
    try:
        return datetime.strptime(text, _TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise DecodeError(f"{key} is not an instant") from exc


def _strs(rec: Mapping[str, Any], key: str) -> tuple[str, ...]:
    items = _get(rec, key, list)
    if not all(isinstance(i, str) for i in items):
        raise DecodeError(f"{key} holds a non-string")
    return tuple(items)


def _dec_descriptor(rec: Mapping[str, Any]) -> ReleaseDescriptor:
    form = _get(rec, "form", str)
    if form == "in_run_group":
        return InRunGroup(helpers_disclosed=_get(rec, "helpers_disclosed", bool))
    if form == "durable":
        return Durable(owner=_enum(DurableOwner, _get(rec, "owner", str)))
    if form == "argv":
        exits = _get(rec, "observe_ok_exit", list)
        if not all(isinstance(i, int) and not isinstance(i, bool) for i in exits):
            raise DecodeError("observe_ok_exit holds a non-integer")
        remove = None if rec.get("remove_argv") is None else _strs(rec, "remove_argv")
        return ArgvRelease(
            executable=_get(rec, "executable", str),
            observe_argv=_strs(rec, "observe_argv"),
            observe_ok_exit=frozenset(exits),
            stop_argv=_strs(rec, "stop_argv"),
            timeout=timedelta(seconds=_get(rec, "timeout_s", (int, float))),
            remove_argv=remove,
        )
    raise DecodeError(f"unknown release form {form!r}")


def _dec_handle(rec: Mapping[str, Any] | None, root: str) -> CreatedHandle | None:
    if rec is None:
        return None
    return CreatedHandle(
        lineage=Lineage(root, decode_path(_get(rec, "path", str))),
        effect=_get(rec, "effect", str),
        selector=_get(rec, "selector", str),
        release=_dec_descriptor(_get(rec, "release", dict)),
    )


def _dec_counts(rec: Mapping[str, Any] | None) -> TestCounts | None:
    if rec is None:
        return None
    return TestCounts(
        passed=_get(rec, "passed", int),
        failed=_get(rec, "failed", int),
        errors=_get(rec, "errors", int),
        skipped=_get(rec, "skipped", int),
    )


def _lineage(rec: Mapping[str, Any], root: str) -> Lineage:
    return Lineage(root, decode_path(_get(rec, "path", str)))


def decode_record(rec: Mapping[str, Any], root_run_id: str = "") -> Entry:
    """The entry a record holds. `root_run_id` is the run directory's (any `root_run_id` field
    in the record is ignored, B2-I3). Raises `UnknownClass` for a class this reader does not
    know and `DecodeError` for a malformed entry of a known class."""
    name = rec.get("class")
    if name not in ENTRY_CLASSES:
        raise UnknownClass(str(name))
    seq = _get(rec, "seq", int)
    if name == "plan":
        selection_raw = _get(rec, "selection", dict)
        if not all(isinstance(v, str) for v in selection_raw.values()):
            raise DecodeError("selection holds a non-string")
        return PlanEntry(
            PlanIdentity(
                declaration_digest=_get(rec, "declaration_digest", str),
                args_hash=_get(rec, "args_hash", str),
                selection={decode_path(k): decode_path(v) for k, v in selection_raw.items()},
                observations_digest=_get(rec, "observations_digest", str),
            ),
            seq=seq,
        )
    lin = _lineage(rec, root_run_id)
    if name == "issue":
        remedy_raw = rec.get("remedy")
        return IssueEntry(
            lineage=lin,
            effect=_get(rec, "effect", str),
            facet=_enum(EffectFacetClass, _get(rec, "facet", str)),
            attempt=_get(rec, "attempt", int),
            repeat=_enum(Repeat, _get(rec, "repeat", str)),
            lifetime=_enum(Lifetime, _get(rec, "lifetime", str)),
            release=_dec_descriptor(_get(rec, "release", dict)),
            remedy=(
                None
                if remedy_raw is None
                else RemedyGrant(
                    code=_get(remedy_raw, "code", str),
                    effect=_get(remedy_raw, "effect", str),
                    attempt=_get(remedy_raw, "attempt", int),
                )
            ),
            issued_at=_time(rec, "issued_at"),
            seq=seq,
        )
    if name == "confirmation":
        return ConfirmationEntry(
            lineage=lin,
            effect=_get(rec, "effect", str),
            attempt=_get(rec, "attempt", int),
            confirmation=Confirmation(
                status=_enum(ConfirmationStatus, _get(rec, "status", str)),
                code=_get_opt(rec, "code", str),
                identity=_get_opt(rec, "identity", str),
            ),
            seq=seq,
        )
    if name == "result":
        return ResultEntry(
            lineage=lin,
            effect=_get(rec, "effect", str),
            attempt=_get(rec, "attempt", int),
            result=RecordedResult(
                passed=_get(rec, "passed", bool),
                code=_get_opt(rec, "code", str),
                counts=_dec_counts(_get_opt(rec, "counts", dict)),
            ),
            seq=seq,
        )
    if name == "released":
        return ReleasedEntry(
            lineage=lin,
            effect=_get(rec, "effect", str),
            attempt=_get(rec, "attempt", int),
            released_at=_time(rec, "released_at"),
            outcome=_get_opt(rec, "outcome", str),
            seq=seq,
        )
    resend = _get_opt(rec, "resend", str)
    if name == "step":
        return StepEntry(
            lineage=lin,
            at=_time(rec, "at"),
            kind=_enum(StepKind, _get(rec, "kind", str)),
            code=_get(rec, "code", str),
            human_action=_get_opt(rec, "human_action", str),
            resend=None if resend is None else _enum(Resend, resend),
            handle=_dec_handle(_get_opt(rec, "handle", dict), root_run_id),
            seq=seq,
        )
    condition = _get_opt(rec, "condition", str)
    provenance = _get_opt(rec, "provenance", str)
    cut = _get_opt(rec, "cut", str)
    return NodeEnd(
        lineage=lin,
        at=_time(rec, "at"),
        condition=None if condition is None else _enum(Condition, condition),
        code=_get_opt(rec, "code", str),
        human_action=_get_opt(rec, "human_action", str),
        resend=None if resend is None else _enum(Resend, resend),
        provenance=None if provenance is None else _enum(Provenance, provenance),
        cut=None if cut is None else _enum(Cut, cut),
        seq=seq,
    )


def decode_entry(line: bytes | str, root_run_id: str = "") -> Entry:
    """`decode_record` over one line of the file."""
    try:
        rec = json.loads(line)
    except ValueError as exc:
        raise DecodeError("not JSON") from exc
    if not isinstance(rec, dict):
        raise DecodeError("not one object")
    return decode_record(rec, root_run_id)


# ----------------------------------------------------------------------------- the file


def lane_path(run_dir: Path) -> Path:
    return run_dir / "evidence" / LANE_FILE


def run_dir_of(lane_file: Path) -> Path:
    return lane_file.parent.parent


def _lines(data: bytes) -> list[tuple[int, int, bytes]]:
    """(start, end, bytes) of every non-blank physical line, newline excluded."""
    lines: list[tuple[int, int, bytes]] = []
    pos = 0
    for raw in data.split(b"\n"):
        end = pos + len(raw)
        if raw.strip():
            lines.append((pos, end, raw))
        pos = end + 1
    return lines


def committed_length(path: Path) -> int:
    """The byte length of the committed prefix of the lane: every leading line that is one
    complete JSON object (a valid final line without its newline counts: CS-1 framing keeps
    it), so a torn tail is excluded and a repair only ever drops what lies past this length."""
    if not path.exists():
        return 0
    data = path.read_bytes()
    committed = 0
    for _start, end, raw in _lines(data):
        try:
            ok = isinstance(json.loads(raw.decode("utf-8")), dict)
        except ValueError:
            ok = False
        if not ok:
            break
        committed = min(end + 1, len(data))
    return committed


def read_lane(path: Path) -> LaneRead:
    """Every entry of the lane file at `path` (`<run_dir>/evidence/lane.ndjson`); the root of a
    decoded lineage is that run directory's name. A missing file reads as an empty lane. An
    unparseable or undecodable line sets `torn` and is skipped, a line of an unknown class is
    skipped and counted in `unknown`. An unreadable file raises `OSError`."""
    if not path.exists():
        return LaneRead((), False, 0, None)
    root = run_dir_of(path).name
    entries: list[Entry] = []
    torn = False
    unknown = 0
    fmt: int | None = None
    for _start, _end, raw in _lines(path.read_bytes()):
        try:
            rec = json.loads(raw.decode("utf-8"))
        except ValueError:
            torn = True
            continue
        if not isinstance(rec, dict):
            torn = True
            continue
        version = rec.get("lane_format")
        if fmt is None and isinstance(version, int) and not isinstance(version, bool):
            fmt = version
        try:
            entries.append(decode_record(rec, root))
        except UnknownClass:
            unknown += 1
        except DecodeError:
            torn = True
    return LaneRead(tuple(entries), torn, unknown, fmt)


# ----------------------------------------------------------------------------- assembly


def assemble_tickets(entries: Iterable[Entry]) -> tuple[TicketEntry, ...]:
    """The `TicketEntry` of every issue entry, in issue order, with the confirmation, result and
    released entries that name it attached. The handle is derived here from the issue entry's
    descriptor and the confirmation (APPLIED, facet CREATE), never stored. An entry naming a
    ticket that has no issue entry, and a second entry of one kind for a ticket, are ignored."""
    tickets: dict[tuple[tuple[str, ...], str, int], TicketEntry] = {}
    for entry in entries:
        if isinstance(entry, IssueEntry):
            key = (entry.lineage.path, entry.effect, entry.attempt)
            tickets.setdefault(
                key,
                TicketEntry(
                    lineage=entry.lineage,
                    effect=entry.effect,
                    facet=entry.facet,
                    attempt=entry.attempt,
                    repeat=entry.repeat,
                    lifetime=entry.lifetime,
                    release=entry.release,
                    remedy=entry.remedy,
                    issued_at=entry.issued_at,
                ),
            )
        elif isinstance(entry, (ConfirmationEntry, ResultEntry, ReleasedEntry)):
            key = (entry.lineage.path, entry.effect, entry.attempt)
            ticket = tickets.get(key)
            if ticket is None:
                continue
            if isinstance(entry, ConfirmationEntry) and ticket.confirmation is None:
                conf = entry.confirmation
                handle = None
                if (
                    conf.status == ConfirmationStatus.APPLIED
                    and ticket.facet == EffectFacetClass.CREATE
                    and conf.identity is not None
                ):
                    handle = CreatedHandle(
                        ticket.lineage, ticket.effect, conf.identity, ticket.release
                    )
                tickets[key] = replace(ticket, confirmation=conf, handle=handle)
            elif isinstance(entry, ResultEntry) and ticket.result is None:
                tickets[key] = replace(ticket, result=entry.result)
            elif isinstance(entry, ReleasedEntry) and ticket.released_at is None:
                tickets[key] = replace(
                    ticket, released_at=entry.released_at, release_outcome=entry.outcome
                )
    return tuple(tickets.values())


def node_record(entries: Iterable[Entry], path: tuple[str, ...]) -> NodeRecord:
    """V-4.5's durable part for `path`: its tickets and its steps, in record order."""
    entries = tuple(entries)
    tickets = tuple(t for t in assemble_tickets(entries) if t.lineage.path == path)
    steps = tuple(e for e in entries if isinstance(e, StepEntry) and e.lineage.path == path)
    return NodeRecord(tickets, steps)
