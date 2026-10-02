"""L.RB-12.4: the RV-2 packet lists every stub-proven label with its MC-B-05 text and a doc line.

The planted half pins the packet's shape on synthetic labels, rows and docs. The live half builds
the packet from the declared labels, `stub_labels.toml` and `docs/environment.md`, and writes it as
a CI artifact (`tests/proof/results/rv2-packet-slice-b.md`, uploaded with the `test` job's results)
for the stage critic of L.J-SLICE-B.1's RV-2.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.proof import meta as meta_mod
from tests.proof.b import rv2_packet as rv2
from tests.proof.b import stub_labels as sl

SUFFIX = sl.TWIN_SUFFIX
ROOT = Path(__file__).resolve().parents[3]
OUT_ENV = "TRESTLE_RV2_PACKET_OUT"
DEFAULT_OUT = ROOT / "tests" / "proof" / "results" / "rv2-packet-slice-b.md"

LABELS = [
    {"id": "WR-ENV-3:allowlisted-noninteractive-identity", "posture": "stub_proven"},
    {"id": "WR-ENV-16:jvm-wrapper-fetch-disabled-or-blocked", "posture": "stub_proven"},
    {"id": "WR-ENV-8:mfa-blocked-human-action", "posture": "stub_proven"},
    {"id": "WR-ENV-4:authoritative-probe@host", "posture": "claim"},
    {"id": "WR-ENV-4:authoritative-probe@host" + SUFFIX, "posture": "stub_proven"},
    {"id": "WR-ENV-1:plain-claim", "posture": "claim"},
]
STUB = sl.StubLabels(
    twin_suffix=SUFFIX,
    rows=[
        {
            "label": "WR-ENV-3:allowlisted-noninteractive-identity",
            "group": "D-1",
            "text": "the stub stands for mise; the real tool is unverified",
            "deferral": "OPEN-MISE-HOST",
        },
        {
            "label": "WR-ENV-16:jvm-wrapper-fetch-disabled-or-blocked",
            "group": "D-19",
            "text": "the stub stands for the JDK running GradleWrapperMain; unverified (D-19)",
            "deferral": "OQ-25",
        },
    ],
)
DOCS = {
    "docs/environment.md": "\n".join(
        [
            "# Environment",
            "The toolchain section covers D-19 (the Gradle wrapper) and its stub.",
            "Identity: WR-ENV-3:allowlisted-noninteractive-identity is proven on a stub of mise.",
            "Note on OPEN-MISE-HOST: the real mise is not run here.",
            "WR-ENV-4:authoritative-probe is read-only (a select).",
        ]
    )
}


def _write(text: str) -> None:
    out = Path(os.environ.get(OUT_ENV) or DEFAULT_OUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)


@pytest.mark.proves("WR-PROOF-3", "WR-PROOF-3:b-rv2-presence", "B", "B", "LOGIC", "CI")
def test_packet_lists_every_stub_proven_label_with_doc_line() -> None:
    # planted: one row per stub_proven label (twins included, claims never), each with its MC-B-05
    # text (a twin: the standing twin statement) and a doc line
    packet = rv2.build(LABELS, STUB, DOCS)
    assert [row.label for row in packet] == [
        "WR-ENV-3:allowlisted-noninteractive-identity",
        "WR-ENV-16:jvm-wrapper-fetch-disabled-or-blocked",
        "WR-ENV-8:mfa-blocked-human-action",
        "WR-ENV-4:authoritative-probe@host" + SUFFIX,
    ]
    by_label = {row.label: row for row in packet}
    first = by_label["WR-ENV-3:allowlisted-noninteractive-identity"]
    assert first.text == "the stub stands for mise; the real tool is unverified"
    assert first.group == "D-1" and first.deferral == "OPEN-MISE-HOST"
    assert first.doc_line == (
        "docs/environment.md:3: Identity: WR-ENV-3:allowlisted-noninteractive-identity is proven "
        "on a stub of mise."
    )  # the label id itself first
    jvm = by_label["WR-ENV-16:jvm-wrapper-fetch-disabled-or-blocked"]
    assert jvm.doc_line is not None and jvm.doc_line.startswith("docs/environment.md:2:")  # D-19
    assert "D-19" in jvm.doc_line
    twin = by_label["WR-ENV-4:authoritative-probe@host" + SUFFIX]
    assert twin.kind == "twin" and twin.origin == "WR-ENV-4:authoritative-probe@host"
    assert "unverified" in twin.text
    assert twin.doc_line == (  # a twin's doc line is its origin's (the `@host` suffix dropped)
        "docs/environment.md:5: WR-ENV-4:authoritative-probe is read-only (a select)."
    )
    # a token is whole: D-1 is not D-19
    assert rv2.find_doc_line(DOCS, ["D-1"]) is None
    # the label whose row is absent has no text and no doc line, and is listed; when its row leaf
    # has not landed yet only the doc line is
    missing = by_label["WR-ENV-8:mfa-blocked-human-action"]
    assert missing.text == "" and missing.doc_line is None
    assert rv2.incomplete(packet) == [
        "WR-ENV-8:mfa-blocked-human-action: no MC-B-05 text",
        "WR-ENV-8:mfa-blocked-human-action: no doc line",
    ]
    assert rv2.incomplete(packet, pending={"WR-ENV-8:mfa-blocked-human-action"}) == [
        "WR-ENV-8:mfa-blocked-human-action: no doc line"
    ]
    rendered = rv2.render(packet)
    assert all(f"## {row.label}" in rendered for row in packet)
    assert "the stub stands for mise" in rendered and "(none found)" in rendered

    # live: every declared stub_proven label has a row in the packet (twins included), every
    # non-twin label whose MC-B-05 row exists carries its text, and the packet is written
    labels = list(meta_mod._load_all_labels())  # noqa: SLF001
    stub = sl.load()
    live = rv2.build(labels, stub, rv2.load_docs())
    declared = [str(lb["id"]) for lb in labels if lb.get("posture") == "stub_proven"]
    assert [row.label for row in live] == declared
    rows = {str(r["label"]): r for r in stub.rows}
    for row in live:
        if row.kind == "stub" and row.label in rows:
            assert row.text == rows[row.label]["text"] and row.text.strip()
    _write(rv2.render(live))
