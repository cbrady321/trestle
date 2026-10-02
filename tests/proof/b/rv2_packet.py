"""The RV-2 packet (L.RB-12.4; MC-B-05, RV-2): every `stub_proven` label with its MC-B-05 text and
its doc line, for the stage critic's review of the 'real tool unverified' wording.

Not a test module (never matches `test_*.py`, DM-80). `tests/proof/b/test_rv2_packet.py` builds the
packet from the declared labels, `stub_labels.toml` and the docs, asserts it is complete, and writes
it as a CI artifact (`tests/proof/results/rv2-packet-slice-b.md`, which the `test` job uploads);
L.J-SLICE-B.1's reviewer reads that packet, never the ledger.

A packet row is one label:

* a label that is not a twin has its MC-B-05 row in `stub_labels.toml`: group, text, deferral. A row
  that is absent because its row leaf has not landed yet (AMB-8) is a row with no text, and
  `incomplete` lists it;
* a twin (`<origin><twin_suffix>`, CSC-8) has no row of its own (its row is its origin's): its text
  is the standing statement that it is the CI twin of a DOCKER . HOST label, whose real binding is
  proven only through the host record.

The doc line of a row is the first line of the checked docs that names the label id, else its origin
label (a twin), else its deferral token (`OPEN-MISE-HOST`, `OQ-26`), else its group (`D-9`): the
disclosure a reader of `docs/environment.md` sees. It renders `path:line: text`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.proof.b import stub_labels as stub_labels_mod

ROOT = Path(__file__).resolve().parents[3]
DOC_PATHS = ("docs/environment.md",)
TWIN_TEXT = (
    "CI twin of `{origin}`: a fake stands in for the real HOST binding; the real behaviour is "
    "unverified here and proven only through the host record (CSC-8, MC-B-03)."
)


@dataclass(frozen=True)
class PacketRow:
    label: str
    kind: str  # "stub" (MC-B-05 row) or "twin" (CSC-8)
    group: str
    text: str
    deferral: str
    origin: str
    doc_line: str | None


def load_docs(root: Path | None = None, paths: tuple[str, ...] = DOC_PATHS) -> dict[str, str]:
    """path -> text of each checked doc that exists."""
    base = root or ROOT
    return {p: (base / p).read_text() for p in paths if (base / p).is_file()}


def _named(needle: str, line: str) -> bool:
    """Whether `line` names `needle` as a whole token (`D-1` is not `D-19`)."""
    return re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", line) is not None


def find_doc_line(docs: Mapping[str, str], needles: list[str]) -> str | None:
    """`path:line: text` of the first line naming the first needle that any doc names."""
    for needle in needles:
        if not needle:
            continue
        for path, text in docs.items():
            for number, line in enumerate(text.splitlines(), start=1):
                if _named(needle, line):
                    return f"{path}:{number}: {line.strip()}"
    return None


def build(
    labels: list[dict[str, Any]], stub: stub_labels_mod.StubLabels, docs: Mapping[str, str]
) -> list[PacketRow]:
    """One row per declared `stub_proven` label, in declaration order."""
    rows_by_label = {str(r["label"]): r for r in stub.rows if "label" in r}
    packet: list[PacketRow] = []
    for label in labels:
        if label.get("posture") != "stub_proven":
            continue
        label_id = str(label["id"])
        if stub_labels_mod.is_twin(label_id, stub.twin_suffix):
            origin = label_id[: -len(stub.twin_suffix)]
            packet.append(
                PacketRow(
                    label_id,
                    "twin",
                    "",
                    TWIN_TEXT.format(origin=origin),
                    "",
                    origin,
                    find_doc_line(docs, [label_id, origin, origin.removesuffix("@host")]),
                )
            )
            continue
        row = rows_by_label.get(label_id, {})
        text, group, deferral = (str(row.get(k, "")) for k in ("text", "group", "deferral"))
        packet.append(
            PacketRow(
                label_id,
                "stub",
                group,
                text,
                deferral,
                "",
                find_doc_line(docs, [label_id, deferral, group]),
            )
        )
    return packet


def incomplete(packet: list[PacketRow], *, pending: set[str] | None = None) -> list[str]:
    """Why a row is not review-ready: no MC-B-05 text, or no doc line. A label in `pending` (its row
    leaf has not landed, AMB-8) is waited for and never listed for its text."""
    out = []
    for row in packet:
        if not row.text.strip() and row.label not in (pending or set()):
            out.append(f"{row.label}: no MC-B-05 text")
        if row.doc_line is None:
            out.append(f"{row.label}: no doc line")
    return out


def render(packet: list[PacketRow]) -> str:
    """The packet as Markdown for the stage critic: one section per row."""
    lines = ["# RV-2 packet: stub-proven labels of Slice B", ""]
    lines.append(f"{len(packet)} label(s); each with its MC-B-05 text and its doc line.")
    for row in packet:
        lines += ["", f"## {row.label}", ""]
        lines.append(f"- kind: {row.kind}" + (f"; group {row.group}" if row.group else ""))
        if row.deferral:
            lines.append(f"- deferral: {row.deferral}")
        lines.append(f"- text: {row.text or '(none: its row leaf has not landed)'}")
        lines.append(f"- doc line: {row.doc_line or '(none found)'}")
    return "\n".join(lines) + "\n"
