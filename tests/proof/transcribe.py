"""`python -m tests.proof.transcribe` (CSC-5): verbatim/derived diffs between
the committed proof-court registries and the read-only design packet.

`L.P0-0c.1` builds only `--check matrix`. `L.P0-0c.2` extends this module
with `--check rows|all`. Every mode needs the main-checkout design docs
(never copied into this worktree, never edited — the packet is read-only);
when the relevant path/env var is not available (CI, or a host without the
main checkout) the mode prints a HOST-only notice and exits 0, matching the
leaf's own accept text ("skips in CI and reads UNPROVEN": the *result* is
UNPROVEN, but a skipped verifier step is not this tool's failure).
"""

from __future__ import annotations

import argparse
import os
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX_MAP_PATH = ROOT / "tests" / "proof" / "matrix_map.toml"

MATRIX_CLAUSE_RE = re.compile(r"^[AB]\d+\.\d+$")

# The nine guarantee rows of the requirements' proof matrix, in table order,
# mapped to the MC-03 guarantee token used in matrix_map.toml's `cell` field
# (plan-workflow-runtime.md ~L710-780 groups A<N>.*/B<N>.* by this same
# guarantee-row index).
GUARANTEE_TOKENS = [
    "one_terminal_answer",
    "admitted_plan",
    "no_repeat_work",
    "checked_before_done",
    "deadlines_enforced",
    "cancellation_stops_work",
    "errors_repaired_within_budgets",
    "cleanup_scoped",
    "evidence_honest",
]


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_matrix_map() -> list[dict[str, object]]:
    data = tomllib.loads(MATRIX_MAP_PATH.read_text())
    return list(data.get("clause", []))


def matrix_ids() -> set[str]:
    """Every valid `proves(clause=...)` id: the 65 clause ids plus every
    `<clause>:<part>` id for a clause that declares parts."""
    ids: set[str] = set()
    for clause in load_matrix_map():
        cid = clause["id"]
        ids.add(cid)
        for part in clause.get("parts", []):
            ids.add(f"{cid}:{part['name']}")
    return ids


def _requirements_matrix_cells(requirements_text: str) -> dict[int, tuple[str, str]]:
    """Parse the "Guarantee x slice proof matrix" markdown table into
    {row_index (0-based, table order): (slice_a_text, slice_b_text)}."""
    lines = requirements_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("| Guarantee | Slice A"):
            start = i
            break
    if start is None:
        raise ValueError("requirements matrix table header not found")

    rows: dict[int, tuple[str, str]] = {}
    idx = 0
    for line in lines[start + 2 :]:  # skip header + separator row
        if not line.strip().startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            break
        rows[idx] = (cells[1], cells[2])
        idx += 1
    return rows


def cmd_check_matrix(args: argparse.Namespace) -> int:
    requirements_path = args.requirements or os.environ.get("TRESTLE_REQUIREMENTS")
    if not requirements_path:
        print("transcribe --check matrix: no requirements packet available (HOST-only); UNPROVEN")
        return 0
    path = Path(requirements_path)
    if not path.exists():
        print(f"transcribe --check matrix: {path} not found; UNPROVEN")
        return 0

    text = path.read_text()
    cells = _requirements_matrix_cells(text)
    ok = True
    for clause in load_matrix_map():
        cid = clause["id"]
        letter = cid[0]
        num = int(cid[1:].split(".")[0])
        row_idx = num - 1
        if row_idx not in cells:
            print(f"DIFF: {cid}: requirements matrix has no row {num}")
            ok = False
            continue
        cell_text = cells[row_idx][0 if letter == "A" else 1]
        fragment = _normalize_ws(str(clause["fragment"]))
        if fragment not in _normalize_ws(cell_text):
            print(
                f"DIFF: {cid}: fragment {fragment!r} not found in requirements cell {cell_text!r}"
            )
            ok = False
    return 0 if ok else 1


def cmd_check_rows(_args: argparse.Namespace) -> int:
    # Extended by L.P0-0c.2.
    print("transcribe --check rows: not yet built (L.P0-0c.2)")
    return 2


def cmd_check_all(args: argparse.Namespace) -> int:
    # Extended by L.P0-0c.2 to also run --check rows.
    return cmd_check_matrix(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.transcribe")
    parser.add_argument("--check", choices=["matrix", "rows", "all"], required=True)
    parser.add_argument("--requirements", default=None)
    parser.add_argument("--decomposition", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check == "matrix":
        return cmd_check_matrix(args)
    if args.check == "rows":
        return cmd_check_rows(args)
    if args.check == "all":
        return cmd_check_all(args)
    parser.error(f"unknown --check {args.check}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
