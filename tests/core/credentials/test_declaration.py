"""Change 3: `@trestle(credentials=[...])` is a literal read at publication, recorded in the
manifest and counted in the snapshot identity; `FakeContext` gives authors both shapes with fake
values."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from trestle.plugin import Context, trestle
from trestle.plugin.testing import FakeContext, call_plugin
from trestle.server.plugin_schema import DeclarationError, declared_from_source
from trestle.server.snapshots import load_declared, materialize_snapshot

HEAD = "from trestle.plugin import Context, trestle\n\n\n"
BODY = "def p(ctx: Context) -> dict[str, int]:\n    return {}\n"


def test_declared_names_are_literals_recorded_sorted() -> None:
    declared = declared_from_source(HEAD + '@trestle(credentials=["gh", "ci-reader"])\n' + BODY)
    assert declared.credentials == ("ci-reader", "gh")
    assert declared.declared_dict()["credentials"] == ["ci-reader", "gh"]
    assert "credentials" not in declared_from_source(HEAD + "@trestle\n" + BODY).declared_dict()


@pytest.mark.parametrize(
    "keyword",
    [
        'credentials="gh"',
        "credentials=[NAME]",
        'credentials=["Has.Dot"]',
        'credentials=["gh", "gh"]',
        'credentials=["a:b"]',
    ],
)
def test_bad_declarations_are_refused(keyword: str) -> None:
    with pytest.raises(DeclarationError):
        declared_from_source(HEAD + f"@trestle({keyword})\n" + BODY)


def test_declaration_counts_in_the_snapshot_identity(tmp_path: Path) -> None:
    home = tmp_path / "home"
    ids = []
    for index, keyword in enumerate(("", '(credentials=["gh"])', '(credentials=["other"])')):
        path = tmp_path / f"v{index}" / "p.py"
        path.parent.mkdir()
        path.write_text(HEAD + f"@trestle{keyword}\n" + BODY, encoding="utf-8")
        snap = materialize_snapshot(path, "p", home=home)
        ids.append(snap.snapshot_id)
        assert load_declared(snap).credentials == (
            ("gh",) if "gh" in keyword else ("other",) if keyword else ()
        )
    assert len(set(ids)) == 3


@trestle(credentials=["demo-token"])
def uses_both(ctx: Context) -> dict[str, str]:
    env = ctx.credential_env("demo-token")
    path = ctx.credential_file("demo-token")
    return {"env": next(iter(env)), "same": str(path.read_text() == env["DEMO_TOKEN"])}


def test_fake_context_gives_both_shapes(tmp_path: Path) -> None:
    ctx = FakeContext(tmp_path, credentials={"demo-token": {"DEMO_TOKEN": "FAKE-unit-value"}})
    assert call_plugin(uses_both, {}, ctx=ctx) == {"env": "DEMO_TOKEN", "same": "True"}
    path = ctx.channel / "demo-token"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(ctx.channel.stat().st_mode) == 0o700
    with pytest.raises(LookupError, match="not declared"):
        ctx.credential_env("other")
    with pytest.raises(LookupError, match="not declared"):
        FakeContext(tmp_path / "bare").credential_file("demo-token")
    with pytest.raises(NotImplementedError):
        ctx.exec(["true"], credentials=["demo-token"])  # change 5
