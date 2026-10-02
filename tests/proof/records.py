"""Independent strict ndjson oracle over a run's ledger (L.P0-0b.1; MC-10,
MC-26 skeleton).

Deliberately does **not** reuse `trestle.common.fsutil.read_ndjson`: that
reader tolerates and silently drops a torn trailing line (R-STORE-10,
gap G-D2), so a reader defect and a product defect are indistinguishable
through it. This module reads the bytes itself and reports `torn` and
`merged` conditions explicitly instead of swallowing them, so proof tests
can assert on the raw file shape rather than on what the product's own
reader chose to show them.

`lane_rows` (L.SV-1.4; MC-10 lane-aware) reads `evidence/lane.ndjson` the same way: byte for
byte, with its own strict schema per written entry class (MC-19), and never through
`trestle.common.lane_format` (a codec defect and a product defect must not hide each other).
`node_record` answers for the root node's ledger kinds (`path=()`), and for any node path its
lane entries: tickets, steps (a `NodeEnd` is the vertex's end and never a step), ends.
`LANE_FORMAT` is the format version the oracle parses ("absent" before L.SV-1.4; TM-P0-3's
probe reads it).

`await_record` is the test kit's one wait on a run's record: it returns when the awaited
condition holds, or when the root reaches a terminal row first (any terminating event), or at its
bound, and says which. Tests wait through it (or its two shapes `await_node_end` and
`await_confirmations`), never through their own polling loop (SA-05's lane-poll ratchet).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from tests.proof import tolerances
from trestle.server.ledger import TERMINAL_KINDS, ledger_path

LANE_FORMAT = 1


@dataclass(frozen=True)
class Rows:
    rows: list[dict[str, Any]] = field(default_factory=list)
    torn: bool = False
    merged: bool = False


@dataclass(frozen=True)
class NodeRecord:
    path: tuple[str, ...]
    kinds: list[str]  # the ledger's row kinds; only the root node has any
    terminal: str | None
    tickets: list[dict[str, Any]] = field(default_factory=list)  # issue + its follow-up entries
    steps: list[dict[str, Any]] = field(default_factory=list)  # step entries, never a NodeEnd
    ends: list[dict[str, Any]] = field(default_factory=list)  # NodeEnds, in lane order


def ledger_rows(run_dir: Path) -> Rows:
    """Read `run_dir`'s ledger.ndjson byte-for-byte.

    - `torn`: the file's last physical line is not valid JSON (a write was
      interrupted before its trailing newline landed).
    - `merged`: two JSON objects were found folded onto one physical line
      with no separating newline between them (the product writer never
      does this — R-STORE-9 fsyncs one line per append — but a planted
      fixture can, and this oracle must notice rather than drop the
      second object).

    Both conditions are reported on the `Rows` result; neither one causes
    a well-formed row to be dropped.
    """
    path = ledger_path(run_dir)
    if not path.exists():
        return Rows(rows=[], torn=False, merged=False)

    text = path.read_text(encoding="utf-8")
    if not text:
        return Rows(rows=[], torn=False, merged=False)

    torn = not text.endswith("\n")
    physical_lines = text.split("\n")
    if physical_lines and physical_lines[-1] == "":
        physical_lines.pop()

    decoder = json.JSONDecoder()
    rows: list[dict[str, Any]] = []
    merged = False
    last_index = len(physical_lines) - 1
    for index, line in enumerate(physical_lines):
        if not line.strip():
            continue
        pos = 0
        length = len(line)
        objects_on_line = 0
        try:
            while pos < length:
                while pos < length and line[pos].isspace():
                    pos += 1
                if pos >= length:
                    break
                obj, end = decoder.raw_decode(line, pos)
                rows.append(obj)
                objects_on_line += 1
                pos = end
        except json.JSONDecodeError:
            if index == last_index:
                torn = True
            continue
        if objects_on_line > 1:
            merged = True

    return Rows(rows=rows, torn=torn, merged=merged)


# ---------------------------------------------------------------------------- the lane (MC-19)

# The oracle's own schema for every written entry class: key -> allowed JSON types, and, for the
# closed vocabularies, the allowed strings. Written out here, not read from the product.
_STR, _INT, _BOOL, _DICT, _NONE = str, int, bool, dict, type(None)
_STATUS = {"applied", "not_applied", "unknown"}
_RESEND = {"succeeds_after_action", "will_not_succeed", "unknown"}
_CONDITION = {
    "satisfied",
    "unsatisfied",
    "converging",
    "stale",
    "in_doubt",
    "incompatible",
    "blocked",
    "failed",
}
_TICKET_KEY: dict[str, tuple[type, ...]] = {"path": (_STR,), "effect": (_STR,), "attempt": (_INT,)}
LANE_SCHEMA: dict[str, dict[str, tuple[type, ...]]] = {
    "plan": {
        "declaration_digest": (_STR,),
        "args_hash": (_STR,),
        "selection": (_DICT,),
        "observations_digest": (_STR,),
    },
    "issue": {
        **_TICKET_KEY,
        "facet": (_STR,),
        "repeat": (_STR,),
        "lifetime": (_STR,),
        "release": (_DICT,),
        "remedy": (_DICT, _NONE),
        "issued_at": (_STR,),
    },
    "confirmation": {
        **_TICKET_KEY,
        "status": (_STR,),
        "code": (_STR, _NONE),
        "identity": (_STR, _NONE),
    },
    "result": {**_TICKET_KEY, "passed": (_BOOL,), "code": (_STR, _NONE), "counts": (_DICT, _NONE)},
    "released": {**_TICKET_KEY, "released_at": (_STR,), "outcome": (_STR, _NONE)},
    "step": {
        "path": (_STR,),
        "at": (_STR,),
        "kind": (_STR,),
        "code": (_STR,),
        "human_action": (_STR, _NONE),
        "resend": (_STR, _NONE),
        "handle": (_DICT, _NONE),
    },
    "end": {
        "path": (_STR,),
        "at": (_STR,),
        "condition": (_STR, _NONE),
        "code": (_STR, _NONE),
        "human_action": (_STR, _NONE),
        "resend": (_STR, _NONE),
        "provenance": (_STR, _NONE),
        "cut": (_STR, _NONE),
    },
}
_RELEASE_FORMS = {"in_run_group", "argv", "durable"}


@dataclass(frozen=True)
class LaneRow:
    """One lane line the oracle accepted: its byte span in the file and its object."""

    offset: int  # the first byte of the line
    end: int  # the byte after its newline
    entry: dict[str, Any]

    @property
    def cls(self) -> str:
        return str(self.entry["class"])

    @property
    def path(self) -> str | None:
        value = self.entry.get("path")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class LaneRows:
    rows: list[LaneRow] = field(default_factory=list)
    torn: bool = False  # the file's last line is not a complete entry
    unknown: int = 0  # well-formed lines of a class outside MC-19's written set
    problems: list[str] = field(default_factory=list)  # schema violations (each line rejected)
    format: int | None = None  # `lane_format` as the first entry that carries it wrote it


def encode_path(path: tuple[str, ...]) -> str:
    """The lane's canonical encoded node path: segments joined by "/" (the root is "")."""
    return "/".join(path)


def _type_ok(value: object, allowed: tuple[type, ...]) -> bool:
    if isinstance(value, bool):
        return bool in allowed
    return isinstance(value, allowed)


def _schema_problems(entry: dict[str, Any]) -> list[str]:
    cls = entry["class"]
    problems = []
    for key, allowed in LANE_SCHEMA[cls].items():
        if key not in entry:
            problems.append(f"{cls}: missing {key}")
        elif not _type_ok(entry[key], allowed):
            problems.append(f"{cls}: {key} has the wrong type")
    if isinstance(entry.get("seq"), bool) or not isinstance(entry.get("seq"), int):
        problems.append(f"{cls}: seq is not an integer")
    if cls in ("confirmation",) and entry.get("status") not in _STATUS:
        problems.append("confirmation: unknown status")
    if cls in ("step", "end") and entry.get("resend") not in (*_RESEND, None):
        problems.append(f"{cls}: unknown resend")
    if cls == "end" and entry.get("condition") not in (*_CONDITION, None):
        problems.append("end: unknown condition")
    if cls == "issue":
        release = entry.get("release")
        if isinstance(release, dict) and release.get("form") not in _RELEASE_FORMS:
            problems.append("issue: release descriptor has no known form")
    return problems


def lane_rows(run_dir: Path) -> LaneRows:
    """Read `run_dir`'s lane (`evidence/lane.ndjson`) byte-for-byte, with the oracle's own schema.

    A line that is not a JSON object, or is a known class that breaks its schema, is reported in
    `problems` and dropped; a last line without its newline that is not a whole entry sets `torn`.
    A class outside MC-19's written set is counted in `unknown` (a `TicketEntry` is never one of
    the written classes). `seq` must strictly increase and the first entry carries
    `lane_format`. A lane that cannot be read at all is one problem and no rows. Nothing is
    swallowed silently."""
    path = run_dir / "evidence" / "lane.ndjson"
    if not path.exists():
        return LaneRows()
    try:
        data = path.read_bytes()
    except OSError as exc:  # an unreadable lane is reported, never raised into a waiting reader
        return LaneRows(problems=[f"the lane is unreadable: {type(exc).__name__}"])
    rows: list[LaneRow] = []
    problems: list[str] = []
    unknown = 0
    torn = False
    fmt: int | None = None
    last_seq = 0
    offset = 0
    lines = data.split(b"\n")
    for index, raw in enumerate(lines):
        start, offset = offset, offset + len(raw) + 1
        if not raw.strip():
            continue
        final = index == len(lines) - 1  # the piece after the last newline, or the whole tail
        try:
            entry = json.loads(raw.decode("utf-8"))
        except ValueError:
            torn = torn or final
            problems.append(f"line at byte {start} is not JSON")
            continue
        if not isinstance(entry, dict):
            problems.append(f"line at byte {start} is not one object")
            continue
        if entry.get("class") not in LANE_SCHEMA:
            unknown += 1
            continue
        bad = _schema_problems(entry)
        if bad:
            problems += [f"byte {start}: {p}" for p in bad]
            continue
        if entry["seq"] <= last_seq:
            problems.append(f"byte {start}: seq {entry['seq']} does not increase")
        last_seq = max(last_seq, entry["seq"])
        if fmt is None and isinstance(entry.get("lane_format"), int):
            fmt = entry["lane_format"]
        rows.append(LaneRow(start, min(offset, len(data)), entry))
    return LaneRows(rows=rows, torn=torn, unknown=unknown, problems=problems, format=fmt)


def lane_tickets(lane: LaneRows) -> list[dict[str, Any]]:
    """The oracle's own assembly of a ticket: each issue entry with the first confirmation,
    result and released entry that name it, in issue order."""
    tickets: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in lane.rows:
        e = row.entry
        if row.cls == "plan" or row.cls in ("step", "end"):
            continue
        key = (e["path"], e["effect"], e["attempt"])
        if row.cls == "issue":
            tickets.setdefault(
                key, {"issue": row, "confirmation": None, "result": None, "released": None}
            )
        elif key in tickets and tickets[key][row.cls] is None:
            tickets[key][row.cls] = row
    return list(tickets.values())


def node_record(run_dir: Path, path: tuple[str, ...] = ()) -> NodeRecord:
    """A node's record, read through `ledger_rows` and `lane_rows` only.

    The root node (`path=()`) carries the ledger's kinds and terminal; every node path carries
    its lane entries. A `NodeEnd` is the vertex's end and never one of its steps."""
    kinds: list[str] = []
    terminal: str | None = None
    if path == ():
        kinds = [
            row["kind"] for row in ledger_rows(run_dir).rows if isinstance(row.get("kind"), str)
        ]
        for kind in reversed(kinds):
            if kind in TERMINAL_KINDS:
                terminal = kind
                break
    lane = lane_rows(run_dir)
    where = encode_path(path)
    tickets = [t for t in lane_tickets(lane) if t["issue"].path == where]
    steps = [r.entry for r in lane.rows if r.cls == "step" and r.path == where]
    ends = [r.entry for r in lane.rows if r.cls == "end" and r.path == where]
    return NodeRecord(
        path=path, kinds=kinds, terminal=terminal, tickets=tickets, steps=steps, ends=ends
    )


# -------------------------------------------------------------------------- waiting on the record


@dataclass(frozen=True)
class Awaited:
    """What ended a wait on the record: the condition held, the root reached a terminal row
    first (any terminating event), or the bound elapsed. `lane` and `node` are the last read."""

    why: Literal["condition", "terminal", "timed_out"]
    lane: LaneRows
    node: NodeRecord


def await_record(
    run_dir: Path,
    condition: Callable[[LaneRows, NodeRecord], bool],
    *,
    bound_s: float = tolerances.JOIN_WAIT_S,
    until_terminal: bool = True,
) -> Awaited:
    """Poll the lane and ledger every `tolerances.POLL_S` until `condition` holds, the root has a
    terminal row (when `until_terminal`), or `bound_s` elapses. A torn last line is "not yet",
    never a failure: `lane_rows` already drops it and sets `torn`. The condition is read on the
    same snapshot before the terminal row, so a condition the last write satisfied wins."""
    deadline = time.monotonic() + bound_s
    while True:
        lane = lane_rows(run_dir)
        node = node_record(run_dir)
        if condition(lane, node):
            return Awaited("condition", lane, node)
        if until_terminal and node.terminal is not None:
            return Awaited("terminal", lane, node)
        if time.monotonic() >= deadline:
            return Awaited("timed_out", lane, node)
        time.sleep(tolerances.POLL_S)


def await_node_end(run_dir: Path, *paths: str, bound_s: float = tolerances.JOIN_WAIT_S) -> Awaited:
    """Every path in `paths` (lane path text: `"raiser"`, `"branch/w1"`; `""` is the root) has
    its `NodeEnd`."""
    wanted = set(paths)
    return await_record(
        run_dir,
        lambda lane, _: wanted <= {r.path for r in lane.rows if r.cls == "end"},
        bound_s=bound_s,
    )


def await_confirmations(
    run_dir: Path,
    effect: str,
    *paths: str,
    status: str = "applied",
    bound_s: float = tolerances.JOIN_WAIT_S,
) -> Awaited:
    """Every path in `paths` has a confirmation of `effect` with `status`."""
    wanted = set(paths)
    return await_record(
        run_dir,
        lambda lane, _: (
            wanted
            <= {
                r.path
                for r in lane.rows
                if r.cls == "confirmation"
                and r.entry["effect"] == effect
                and r.entry["status"] == status
            }
        ),
        bound_s=bound_s,
    )
