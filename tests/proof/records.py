"""Independent strict ndjson oracle over a run's ledger (L.P0-0b.1; MC-10,
MC-26 skeleton).

Deliberately does **not** reuse `trestle.common.fsutil.read_ndjson`: that
reader tolerates and silently drops a torn trailing line (R-STORE-10,
gap G-D2), so a reader defect and a product defect are indistinguishable
through it. This module reads the bytes itself and reports `torn` and
`merged` conditions explicitly instead of swallowing them, so proof tests
can assert on the raw file shape rather than on what the product's own
reader chose to show them.

`node_record` answers for the root node only (`path=()`); a real run tree
has no lane reader yet (SV-1/SV-5 are future leaves). `LANE_FORMAT` is
"absent" until `L.SV-1.4` lands one — the probe
`python -c "import sys, tests.proof.records as r; sys.exit(0 if r.LANE_FORMAT == 'absent' else 1)"`
verifies the seam (`ledger_rows`/`node_record`) stays even though the
format name changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trestle.server.ledger import TERMINAL_KINDS, ledger_path

LANE_FORMAT = "absent"


@dataclass(frozen=True)
class Rows:
    rows: list[dict[str, Any]] = field(default_factory=list)
    torn: bool = False
    merged: bool = False


@dataclass(frozen=True)
class NodeRecord:
    path: tuple[int, ...]
    kinds: list[str]
    terminal: str | None


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


def node_record(run_dir: Path, path: tuple[int, ...] = ()) -> NodeRecord:
    """The root node's record, read through `ledger_rows` only.

    Only `path=()` (the root) is answered today — there is no lane reader
    yet. A caller asking for any other path gets `NotImplementedError`
    naming the gap, rather than a silently wrong answer.
    """
    if path != ():
        raise NotImplementedError(
            f"node_record: path={path!r} is not the root; no lane reader "
            "exists yet (LANE_FORMAT='absent'; L.SV-1.4 widens this)"
        )
    rows = ledger_rows(run_dir).rows
    kinds = [row["kind"] for row in rows if isinstance(row.get("kind"), str)]
    terminal: str | None = None
    for kind in reversed(kinds):
        if kind in TERMINAL_KINDS:
            terminal = kind
            break
    return NodeRecord(path=path, kinds=kinds, terminal=terminal)
