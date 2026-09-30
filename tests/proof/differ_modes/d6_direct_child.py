"""`python -m tests.proof.differ d6 [--pairs <module:function>]` (MC-06 mode d6; CSC-5 builder
L.TR-6.1).

Position equivalence (WR-UNIT-1, the HLD item-10 list, V-8): one unit run *directly* (it is the
root) and *inside a parent* (a child of a two-level tree), with identical params and machine state,
behaves the same. The two runs are compared node-for-node after normalizing only what V-8's closed
list of nine position rules (L-1..L-9) says may differ, and any other difference is a contract
defect and is reported by name.

The mode judges runs, it does not make them: a *pair source* is a callable `pairs() -> [Pair]`
(default `tests.tree.d6_pairs:pairs`, the in-library rig's runs; a planted source lets the mode's
own self-test run first). A `Pair` holds a `Position` for the direct run and one for the child
run; a `Position` is the node's own lane rows (its verdicts, every effect record it wrote, its
`NodeEnd` disposition and its release records) and the node's account in its run's answer.

What each rule normalizes (`NORMALIZED`), and nothing else:

* L-3 deadline: the clock readings on a row (`at`, `issued_at`, `released_at`): a child's slice
  begins later on the root's clock, so its readings do differ;
* L-4 record scope: the row's position in the lane (`seq`): the child's lane also holds its
  siblings' rows;
* L-5 identity and view: the node's lineage path (`path`) and the selector a port derives from the
  lineage (`identity`, compared only for whether it is present);
* L-8 outcome reporting: a root reports itself as the whole answer (`rolled_up`, no condition), a
  child is a listed candidate of its root's answer (`listing`, `condition` of the answer's account;
  the node's own condition stays compared on its `NodeEnd` row);
* L-1 lease, L-2 ownership roll-up, L-6 cancel addressing, L-7 root-entry acceptance and L-9
  admission validation touch no field compared here: they are admission-time or run-group facts
  (lease, root release set, `CANCEL_NOT_ROOT`, publication, the admitted plan) proved by their own
  tests. L-2 shows only as *when* the release rows come (after every node has ended), which the
  row order per node does not depend on.

**After-stop variant (SV-7a, option (i)).** A whole-root stop recorded in the child's run (a
sibling's uncaught exception, B1-E6, first) is not a tenth difference (V-8 L-8): the two runs are
compared only up to the parent's first stop record, so the child's rows before it must equal the
direct run's first rows and the direct run must have gone at least as far. Past it the node is only
required to be reported as stopped (`STOPPED`, `UNENDED` or `NOT_STARTED`, B4-C2 rule (4)), with a
`NodeEnd` cut, if any, of `stopped` or `not_started`.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DEFAULT_PAIRS = "tests.tree.d6_pairs:pairs"

# V-8's rules -> the row / account fields each one normalizes. The list is closed: a field not named
# here is compared, so a difference in it is a defect.
NORMALIZED: dict[str, tuple[str, ...]] = {
    "L-3": ("at", "issued_at", "released_at"),
    "L-4": ("seq",),
    "L-5": ("path", "identity"),
    "L-8": ("listing", "condition"),
}
ROW_DROPPED = frozenset(f for rule in ("L-3", "L-4") for f in NORMALIZED[rule]) | {"path"}
IDENTITY = "identity"  # L-5: a selector derived from the lineage; only its presence is compared
ACCOUNT_DROPPED = frozenset({"path", *NORMALIZED["L-8"]})

# What a node's account may say of it once a whole-root stop reached it (V-8 L-8, B4-C2 rule (4)).
STOPPED_LISTINGS = frozenset({"stopped", "unended", "not_started"})
STOPPED_CUTS = frozenset({"stopped", "not_started"})
UNIT_RAISED = "execution.unit_raised"

PairSource = Callable[[], Sequence["Pair"]]


@dataclass(frozen=True)
class Position:
    """One node run in one position: its own lane rows (lane order, `seq` still present) and its
    account in its run's answer (the wire `NodeAnswer`). `stop_seq` is the `seq` of the run's first
    whole-root stop record (None: none was recorded)."""

    rows: tuple[Mapping[str, Any], ...]
    account: Mapping[str, Any] | None
    stop_seq: int | None = None

    @staticmethod
    def of(
        entries: Sequence[Mapping[str, Any]],
        answer: Mapping[str, Any],
        path: str,
        *,
        stop_seq: int | None = None,
    ) -> Position:
        """The position of the node at `path` (`""` is the root) read from a run's lane entries and
        its wire answer (`answer.to_wire_full`, or a terminal response's `answer`)."""
        rows = tuple(dict(e) for e in entries if e.get("path") == path)
        return Position(rows, account_in(answer, path), stop_seq)


@dataclass(frozen=True)
class Pair:
    """A named direct/child pair of one unit."""

    name: str
    direct: Position
    child: Position
    notes: tuple[str, ...] = field(default=())


def account_in(answer: Mapping[str, Any], path: str) -> Mapping[str, Any] | None:
    """The answer's account of the node at `path` (B4-C8): the primary, else its listed entry."""
    want = [seg for seg in path.split("/") if seg]
    primary = answer.get("primary") or {}
    if primary.get("path") == want:
        return primary
    for item in answer.get("listed") or ():
        if item.get("path") == want:
            return item  # type: ignore[no-any-return]
    return None


def first_exception_stop(entries: Sequence[Mapping[str, Any]]) -> int | None:
    """The `seq` of the first step row that records an uncaught exception (`execution.unit_raised`,
    B1-E6): the whole-root stop a sibling's raise causes."""
    for entry in entries:
        if entry.get("class") == "step" and entry.get("code") == UNIT_RAISED:
            return int(entry["seq"])
    return None


def normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """`row` with the fields of V-8's list removed (a selector kept only as present or absent)."""
    out = {k: v for k, v in row.items() if k not in ROW_DROPPED}
    if IDENTITY in out:
        out[IDENTITY] = None if out[IDENTITY] is None else "<selector>"
    return out


def normalize_account(account: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if account is None:
        return None
    return {k: v for k, v in account.items() if k not in ACCOUNT_DROPPED}


def _row_text(row: Mapping[str, Any]) -> str:
    keys = ("class", "effect", "attempt", "status", "condition", "code", "cut", "provenance")
    shown = ", ".join(f"{k}={row[k]!r}" for k in keys if row.get(k) is not None)
    return f"{{{shown}}}"


def _diff_rows(
    want: Sequence[dict[str, Any]], got: Sequence[dict[str, Any]], label: str
) -> list[str]:
    diffs: list[str] = []
    for n, (w, g) in enumerate(zip(want, got, strict=False)):
        if w != g:
            changed = sorted(k for k in set(w) | set(g) if w.get(k) != g.get(k))
            diffs.append(
                f"{label} row {n}: {', '.join(changed)} differ (direct {_row_text(w)} "
                f"vs child {_row_text(g)})"
            )
    return diffs


def check_pair(pair: Pair) -> list[str]:
    """The differences of one pair (empty: equivalent up to V-8's closed list)."""
    direct = [normalize_row(r) for r in pair.direct.rows]
    child_all = pair.child.rows
    stop = pair.child.stop_seq
    if stop is None:
        child = [normalize_row(r) for r in child_all]
        diffs = _diff_rows(direct, child, "verdict/effect record")
        if len(direct) != len(child):
            diffs.append(
                f"effect-record sets differ in size: the direct run wrote {len(direct)} rows, "
                f"the child {len(child)}"
            )
        want, got = normalize_account(pair.direct.account), normalize_account(pair.child.account)
        if want != got:
            diffs.append(f"answer account differs: direct {want} vs child {got}")
        return diffs

    before = [normalize_row(r) for r in child_all if r["seq"] < stop]
    after = [r for r in child_all if r["seq"] >= stop]
    diffs = _diff_rows(direct, before, "pre-stop verdict/effect record")
    if len(direct) < len(before):
        diffs.append(
            f"the direct run recorded {len(direct)} rows, fewer than the {len(before)} the node "
            "recorded inside the parent before the whole-root stop"
        )
    account = pair.child.account
    if account is None or account.get("listing") not in STOPPED_LISTINGS:
        listing = None if account is None else account.get("listing")
        diffs.append(f"after the whole-root stop the node is reported {listing!r}, not stopped")
    for row in after:
        if row.get("class") == "end" and row.get("cut") not in STOPPED_CUTS:
            diffs.append(
                f"after the whole-root stop the node ended {_row_text(row)}, "
                "not `stopped` or `not_started`"
            )
    return diffs


def check(pairs: Sequence[Pair]) -> list[str]:
    """Every difference over `pairs`, each line naming its pair."""
    return [f"{pair.name}: {diff}" for pair in pairs for diff in check_pair(pair)]


def run(source: PairSource) -> int:
    pairs = list(source())
    diffs = check(pairs)
    for diff in diffs:
        print(f"d6: DIFF: {diff}")
    print(f"d6: {len(pairs)} direct/child pairs checked, {len(diffs)} diffs")
    return 1 if diffs else 0


def _resolve(spec: str) -> PairSource:
    module_name, func_name = spec.split(":")
    return getattr(importlib.import_module(module_name), func_name)  # type: ignore[no-any-return]


def main(args: argparse.Namespace) -> int:
    spec = args.pairs or DEFAULT_PAIRS
    try:
        source = _resolve(spec)
    except (ImportError, AttributeError) as exc:
        print(f"d6: no pair source {spec!r} ({exc})")
        return 2
    return run(source)
