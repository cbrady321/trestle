"""L.CL-B1.3 (WR-EVID-4, WR-EVID-6, WR-EVID-7; K-17): the run's artifact count and byte caps hold on
every path by which a run produces an artifact (attach, `outputs/` promotion, staged promotion); a
staged `ctx.artifact()` file is promoted or refused with a marker, never left out; markers are one
per (stream, limit), so a drop storm cannot grow them; and every promoted artifact passes the
write-path scrub (MC-CORE-13).

`TRESTLE_TEST_LIMITS=1` is the S0 test limit set: 10 artifacts, 64 KiB of artifact bytes,
50 events.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.core.answer.test_cl_b2_redaction import SENTINEL, kernel_with, occurrences, run_ok
from tests.proof import harness
from trestle.child.context import RuntimeContext
from trestle.common import redact
from trestle.common.limits import CaptureLimits
from trestle.common.types import PublishView, RunView
from trestle.server.ledger import run_dir_for

KIB = 1024

ARTIFACTS_SOURCE = """\
from pathlib import Path

from trestle.plugin.surface import Context, trestle


@trestle
def artifacts(ctx: Context, mode: str = "attach") -> dict[str, list[str]]:
    ids: list[str] = []
    if mode == "attach":  # twelve attaches against a cap of ten
        for n in range(12):
            path = ctx.tmp / f"a{n:02d}.txt"
            path.write_text(f"body-{n}", encoding="utf-8")
            ids.append(ctx.attach(path, name=f"a{n:02d}.txt"))
    elif mode == "staged":  # one within the byte cap, one over it, one attached from staging
        small = ctx.artifact("small.bin")
        small.write_bytes(b"s" * 100)
        big = ctx.artifact("big.bin")
        big.write_bytes(b"b" * (128 * 1024))
        both = ctx.artifact("attached.bin")
        both.write_bytes(b"x" * 10)
        ids.append(ctx.attach(both, name="attached.bin"))
    elif mode == "fail":  # a staged file, then a raise
        ctx.artifact("partial-report.txt").write_text("half", encoding="utf-8")
        raise RuntimeError("stopped part way")
    elif mode == "outputs":  # fifteen output files against a cap of ten
        for n in range(15):
            (ctx.outputs / f"o{n:02d}.txt").write_text(f"out-{n}", encoding="utf-8")
    return {"ids": ids}
"""

SECRET_STAGED_SOURCE = """\
from trestle.plugin.surface import Context, trestle


@trestle(secrets=["token"])
def secret_staged(ctx: Context, token: str) -> dict[str, str]:
    text = ctx.artifact("report.txt")
    text.write_text(f"report {token}", encoding="utf-8")
    blob = ctx.artifact("blob.bin")
    blob.write_bytes(b"\\x00\\x01" + token.encode("utf-8") + b"\\xff")
    clean = ctx.artifact("clean.bin")
    clean.write_bytes(b"\\x00\\x01\\xff")
    return {"ok": "yes"}
"""


@pytest.fixture(autouse=True)
def _test_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRESTLE_TEST_LIMITS", "1")


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    assert isinstance(kernel.control.publish_plugin(ARTIFACTS_SOURCE), PublishView)
    return kernel


def _run(kernel, mode: str) -> tuple[RunView, Path]:  # type: ignore[no-untyped-def]
    view = run_ok(kernel, "artifacts", {"mode": mode})
    assert view.state == "succeeded", view
    return view, run_dir_for(kernel.home, view.run_id)


def _ledger(run_dir: Path, kind: str) -> list[dict[str, Any]]:
    lines = (run_dir / "evidence" / "ledger.ndjson").read_text(encoding="utf-8").splitlines()
    return [row for row in map(json.loads, lines) if row.get("kind") == kind]


def _stored(run_dir: Path) -> list[Path]:
    root = run_dir / "evidence" / "artifacts"
    return sorted(root.iterdir()) if root.exists() else []


def _artifact_markers(view: RunView) -> list[dict[str, Any]]:
    return [m for m in view.limits_exceeded or [] if m.get("stream") == "artifacts"]


@pytest.mark.proves("WR-EVID-6", "WR-EVID-6:count-byte-cap-marked", "core", "core", "PROC", "CI")
@pytest.mark.proves(
    "WR-EVID-6", "WR-EVID-6:staged-promoted-or-refused", "core", "core", "PROC", "CI"
)
@pytest.mark.proves("WR-EVID-4", "WR-EVID-4:attach-bounded", "core", "core", "PROC", "CI")
def test_artifact_caps_marked_and_staged_promoted_or_refused(tmp_path: Path) -> None:
    cap = CaptureLimits.from_env().max_artifact_count
    assert cap == 10
    kernel = _kernel(tmp_path)

    # 12 attaches: 10 artifacts and one marker; the two refused attaches return no handle, and
    # every handle a plugin was given fetches
    view, run_dir = _run(kernel, "attach")
    result = json.loads((run_dir / "evidence" / "result.json").read_text(encoding="utf-8"))
    ids = result["ids"]
    assert len(ids) == 12 and ids[:10] == [i for i in ids[:10] if i] and ids[10:] == ["", ""]
    assert len(_stored(run_dir)) == cap
    assert sorted(p.name for p in _stored(run_dir)) == sorted(ids[:10])
    markers = _artifact_markers(view)
    assert [m["limit"] for m in markers] == ["max_artifact_count"]
    assert markers[0]["bytes_suppressed"] > 0
    for artifact_id in ids[:10]:
        fetched = kernel.control.fetch(artifact_id, {"kind": "head", "count": 4})
        assert isinstance(fetched, dict) and "tag" in fetched, fetched

    # 15 outputs against the same cap: ten promoted, one coalesced marker for the other five
    view, run_dir = _run(kernel, "outputs")
    assert len(_ledger(run_dir, "artifact_available")) == cap
    assert view.artifact_count == cap
    markers = _artifact_markers(view)
    assert [m["limit"] for m in markers] == ["max_artifact_count"]
    assert markers[0]["bytes_suppressed"] == sum(len(f"out-{n}") for n in range(10, 15))

    # staged: within the byte cap promoted, over it refused and marked, an attached one once
    view, run_dir = _run(kernel, "staged")
    rows = _ledger(run_dir, "artifact_available")
    assert sorted(row["name"] for row in rows) == ["small.bin"]
    assert rows[0]["source"] == "staged"
    (attached,) = json.loads((run_dir / "evidence" / "result.json").read_text("utf-8"))["ids"]
    assert attached and (run_dir / "evidence" / "artifacts" / attached).read_bytes() == b"x" * 10
    assert len(_stored(run_dir)) == 2  # the attached one and the promoted small one
    markers = _artifact_markers(view)
    assert [m["limit"] for m in markers] == ["max_artifact_bytes"]
    assert markers[0]["bytes_suppressed"] == 128 * KIB
    staged_dir = run_dir / "work" / "artifact-staging"
    assert not (staged_dir / "attached.bin.partial").exists()  # moved by attach, not promoted twice
    recorded = _ledger(run_dir, "limit_exceeded")
    assert [m["limit"] for m in recorded[0]["markers"]] == ["max_artifact_bytes"]
    fetched = kernel.control.fetch(rows[0]["artifact_id"], {"kind": "head", "count": 4})
    assert isinstance(fetched, dict) and "tag" in fetched, fetched


def test_a_pass_that_fits_marks_nothing(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(SECRET_STAGED_SOURCE), PublishView)
    view = run_ok(kernel, "secret_staged", {"token": SENTINEL})
    assert view.state == "succeeded"
    # no cap was reached: the only marker is the refused binary, none of the artifact caps
    assert [m["limit"] for m in _artifact_markers(view)] == [redact.BINARY_LIMIT]


@pytest.mark.proves("WR-EVID-4", "WR-EVID-4:markers-bounded", "core", "core", "PROC", "CI")
@pytest.mark.proves("WR-EVID-7", "WR-EVID-7:dropped-event-marker", "core", "core", "PROC", "CI")
def test_drop_storm_marker_volume_bounded(tmp_path: Path) -> None:
    def storm(root: Path, drops: int) -> tuple[list[dict[str, object]], int]:
        evidence = root / "evidence"
        evidence.mkdir(parents=True)
        ctx = RuntimeContext(
            work=root / "work",
            evidence=evidence,
            deadline=datetime.now(UTC),
            events_path=evidence / "events.ndjson",
            limits=CaptureLimits(max_event_count=5, max_events_per_second=10**9),
        )
        sizes = 0
        for n in range(5 + drops):
            ctx.log(f"e{n}")
            if n >= 5:
                sizes += len(
                    json.dumps(
                        {"kind": "log", "payload": {"message": f"e{n}"}}, separators=(",", ":")
                    )
                )
        ctx.flush_limits()
        lines = [
            json.loads(line)
            for line in (evidence / "capture_limits.ndjson").read_text("utf-8").splitlines()
        ]
        return lines, sizes

    few, few_bytes = storm(tmp_path / "few", 300)
    many, many_bytes = storm(tmp_path / "many", 30_000)
    # one line for the one (stream, limit), whatever the drops; the dropped bytes are its total
    assert len(few) == len(many) == 1
    assert (many[0]["stream"], many[0]["limit"]) == ("events", "max_event_count")
    assert few[0]["bytes_suppressed"] == few_bytes
    assert many[0]["bytes_suppressed"] == many_bytes
    # a dropped event is marked, not absent (WR-EVID-7): the first drop wrote its line at once
    first = tmp_path / "first"
    (first / "evidence").mkdir(parents=True)
    ctx = RuntimeContext(
        work=first / "work",
        evidence=first / "evidence",
        deadline=datetime.now(UTC),
        events_path=first / "evidence" / "events.ndjson",
        limits=CaptureLimits(max_event_count=1),
    )
    ctx.log("kept")
    ctx.log("dropped")
    assert (first / "evidence" / "capture_limits.ndjson").read_text("utf-8").count("\n") == 1

    # two kinds of drop are two lines, and only two
    ctx = RuntimeContext(
        work=tmp_path / "two" / "work",
        evidence=tmp_path / "two" / "evidence",
        deadline=datetime.now(UTC),
        events_path=tmp_path / "two" / "evidence" / "events.ndjson",
        limits=CaptureLimits(max_event_count=1, max_single_event_bytes=64),
    )
    ctx.log("kept")
    for _ in range(100):
        ctx.log("dropped")
        ctx.log("x" * 200)
    kinds = sorted((m["stream"], m["limit"]) for m in ctx.limits_markers())
    assert kinds == [("events", "max_event_count"), ("events", "max_single_event_bytes")]


def test_staged_artifacts_are_scrubbed_and_the_sentinel_stays_out(tmp_path: Path) -> None:
    kernel = kernel_with(tmp_path, SECRET_STAGED_SOURCE, "secret_staged")
    view = run_ok(kernel, "secret_staged", {"token": SENTINEL})
    assert view.state == "succeeded", view
    run_dir = run_dir_for(kernel.home, view.run_id)

    rows = _ledger(run_dir, "artifact_available")
    assert sorted(row["name"] for row in rows) == ["clean.bin", "report.txt"]  # blob.bin refused
    bodies = {p.read_bytes() for p in _stored(run_dir)}
    assert bodies == {f"report {redact.REDACTED}".encode(), b"\x00\x01\xff"}
    markers = [m for m in view.limits_exceeded or [] if m["limit"] == redact.BINARY_LIMIT]
    assert len(markers) == 1 and markers[0]["stream"] == "artifacts"
    assert occurrences(run_dir, SENTINEL) == []
    assert occurrences(kernel.home, SENTINEL) == []
    assert SENTINEL not in json.dumps(view.to_dict())


def test_a_failed_run_still_promotes_what_it_staged(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    view = run_ok(kernel, "artifacts", {"mode": "fail"})
    assert view.state == "failed", view
    run_dir = run_dir_for(kernel.home, view.run_id)
    assert [row["name"] for row in _ledger(run_dir, "artifact_available")] == ["partial-report.txt"]


# ---- K-17 documented (L.CL-B1.3 "K-17 is documented"; L.P0-0d.28) -----------------------------

K17_DOC = Path(__file__).resolve().parents[3] / "docs" / "plugins.md"  # k_doc_map.toml K-17
K17_SECTION = "## Artifacts and their limits"


def k17_problems(doc: str) -> list[str]:
    """Why `doc` does not state K-17 (requirements K-17: artifact limits are enforced on every
    path, and staged artifacts are promoted or refused); empty when it does."""
    if K17_SECTION not in doc:
        return [f"no {K17_SECTION!r} section"]
    section = doc.split(K17_SECTION, 1)[1].split("\n## ", 1)[0]
    limits = CaptureLimits()
    wanted = {
        "the K-17 statement": "the same limits hold on all three (K-17)",
        "the outputs/ path": "`outputs/`",
        "the attach path": "`ctx.attach(path, name=...)`",
        "the staging path": "`ctx.artifact(name)`",
        "a staged file is promoted": "promoted when the run ends",
        "the count limit": f"at most {limits.max_artifact_count:,} artifacts".replace(",", " "),
        "the byte limit": f"{limits.max_artifact_bytes // 1024**3} GiB of artifact bytes",
        "an over-limit artifact is refused": "is **not** stored",
        "the count marker": '"max_artifact_count"',
        "the byte marker": '"max_artifact_bytes"',
        "a refused promotion stays in place": "a refused promotion leaves the file",
    }
    return [f"missing {what}: {text!r}" for what, text in wanted.items() if text not in section]


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-17", "core", "core", "INSPECT", "CI")
def test_k17_documented() -> None:
    assert k17_problems(K17_DOC.read_text(encoding="utf-8")) == []


def test_k17_planted_doc_without_the_statement_fails() -> None:
    doc = K17_DOC.read_text(encoding="utf-8")
    assert k17_problems(doc.replace(K17_SECTION, "## Something else")) == [
        f"no {K17_SECTION!r} section"
    ]
    unstated = doc.replace("the same limits hold on all three (K-17)", "limits apply")
    assert k17_problems(unstated) == [
        "missing the K-17 statement: 'the same limits hold on all three (K-17)'"
    ]
    unrefused = doc.replace("is **not** stored", "is stored anyway")
    assert any("refused" in p for p in k17_problems(unrefused))
