"""L.CL-C1.1: the call-form decorator and the declared-metadata carrier (MC-18)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.proof import harness, tolerances
from trestle.child.validate import package_digest
from trestle.common import codes
from trestle.common.types import DeclaredMetadata, PublishView, RequestOutcome, RunView
from trestle.server.plugin_schema import DeclarationError, declared_from_source
from trestle.server.snapshots import load_declared

# what CALL_FORM declares as `deadline=timedelta(...)`
DECLARED_DEADLINE_S = 310

CALL_FORM = """\
from datetime import timedelta

from trestle.plugin.surface import Context, trestle


@trestle(
    deadline=310,
    summary_fields=["total", "rows"],
    packages=("trestle_packs", "json.tool"),
    env_arg="target.env",
    secrets={"token", "auth.password"},
)
def declared_plugin(ctx: Context, target: dict[str, str], token: str = "") -> dict[str, int]:
    return {"total": len(target)}
"""

BARE = """\
from trestle.plugin.surface import Context, trestle


@trestle
def bare_plugin(ctx: Context, n: int = 1) -> dict[str, int]:
    return {"n": n}
"""


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    return harness.fresh_kernel([plugin_dir], home=tmp_path / "home")


def _manifest(kernel, plugin: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    snap = kernel.registry.get(plugin)
    assert snap is not None
    loaded = json.loads((Path(snap.source_path).with_name("manifest.json")).read_text("utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.proves("WR-PLAN-4", "WR-PLAN-4:call-form-honoured", "core", "core", "must", "CI")
@pytest.mark.proves(
    "WR-OWN-8", "WR-OWN-8:env-arg-declaration-carried", "core", "core", "must", "CI"
)
def test_call_form_publishes_and_runs_with_declared_metadata(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    published = kernel.control.publish_plugin(CALL_FORM)
    assert isinstance(published, PublishView), published

    manifest = _manifest(kernel, "declared_plugin")
    assert manifest["entry"] == "declared_plugin"
    assert manifest["declared"] == {
        "deadline_s": DECLARED_DEADLINE_S,
        "summary_fields": ["total", "rows"],
        "packages": ["trestle_packs", "json.tool"],
        "env_arg": "target.env",
        "secrets": ["auth.password", "token"],
        # recorded at publication from the import path (L.CL-C1.4), not read from the source
        "package_digests": {
            "json.tool": package_digest("json.tool"),
            "trestle_packs": package_digest("trestle_packs"),
        },
    }
    snap = kernel.registry.get("declared_plugin")
    assert snap is not None
    assert load_declared(snap) == DeclaredMetadata(
        entry="declared_plugin",
        deadline_s=DECLARED_DEADLINE_S,
        summary_fields=("total", "rows"),
        packages=("trestle_packs", "json.tool"),
        env_arg="target.env",
        secrets=frozenset({"token", "auth.password"}),
        package_digests={
            "json.tool": package_digest("json.tool"),
            "trestle_packs": package_digest("trestle_packs"),
        },
    )

    view = kernel.control.run(
        plugin="declared_plugin", args={"target": {"a": "b"}}, wait_ms=tolerances.HARNESS_WAIT_MS
    )
    assert isinstance(view, RunView), view
    assert view.state == "succeeded"
    assert view.summary == {"total": 1}


def test_bare_form_carries_defaults(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(BARE), PublishView)
    manifest = _manifest(kernel, "bare_plugin")
    assert manifest["entry"] == "bare_plugin"
    assert manifest["declared"] == {
        "deadline_s": None,
        "summary_fields": [],
        "packages": [],
        "env_arg": None,
        "secrets": [],
        "package_digests": {},
    }
    snap = kernel.registry.get("bare_plugin")
    assert snap is not None
    assert load_declared(snap) == DeclaredMetadata(entry="bare_plugin")
    # the identity is no longer the source alone (L.CL-C1.4, MC-18)
    assert snap.snapshot_id != f"snap_{snap.source_sha256[:16]}"


def test_snapshot_without_declared_keys_reads_defaults(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(BARE), PublishView)
    snap = kernel.registry.get("bare_plugin")
    assert snap is not None
    manifest_path = Path(snap.source_path).with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    del manifest["declared"], manifest["entry"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_declared(snap) == DeclaredMetadata()


def _refused(kernel, source: str) -> RequestOutcome:  # type: ignore[no-untyped-def]
    result = kernel.control.publish_plugin(source)
    assert isinstance(result, RequestOutcome), result
    return result


REFUSED_DECLARATIONS = {
    "non-literal deadline": "deadline=LIMIT",
    "computed deadline": "deadline=60 * 5",
    "string deadline": 'deadline="300"',
    "zero deadline": "deadline=0",
    "negative deadline": "deadline=-5",
    "bool deadline": "deadline=True",
    "timedelta with a name": "deadline=timedelta(seconds=LIMIT)",
    "unknown keyword": "timeout=5",
    "positional": '"x"',
    "star-star": "**OPTS",
    "non-literal list": "packages=PACKAGES",
    "non-string entry": "packages=[1]",
    "bad dotted name": 'packages=["not a name"]',
    "duplicate keyword": "env_arg='a', env_arg='b'",
    "env_arg not a string": "env_arg=3",
    "duplicate secret": 'secrets=["a", "a"]',
}


@pytest.mark.parametrize(
    "declaration", list(REFUSED_DECLARATIONS.values()), ids=list(REFUSED_DECLARATIONS)
)
def test_non_literal_or_unknown_keyword_refused_at_publication(
    tmp_path: Path, declaration: str
) -> None:
    kernel = _kernel(tmp_path)
    source = (
        "from datetime import timedelta\n"
        "from trestle.plugin.surface import Context, trestle\n"
        "LIMIT = 300\nPACKAGES = ['a']\nOPTS = {}\n\n"
        f"@trestle({declaration})\n"
        "def refused(ctx: Context) -> dict[str, int]:\n    return {}\n"
    )
    refusal = _refused(kernel, source)
    assert refusal.origin == "publication"
    assert refusal.code == codes.PUBLICATION_VALIDATION_FAILED
    assert refusal.message.startswith("@trestle")
    assert kernel.registry.get("refused") is None
    assert not (tmp_path / "plugins" / "refused.py").exists()
    assert not (tmp_path / "home" / "snapshots").exists() or not list(
        (tmp_path / "home" / "snapshots").iterdir()
    )


def test_timedelta_deadline_reads_as_seconds() -> None:
    source = (
        "from datetime import timedelta\n"
        "from trestle.plugin.surface import trestle\n\n"
        "@trestle(deadline=timedelta(minutes=5, seconds=10))\n"
        "def f() -> None: ...\n"
    )
    assert declared_from_source(source).deadline_s == DECLARED_DEADLINE_S


def test_declaration_error_is_a_schema_error() -> None:
    from trestle.server.plugin_schema import SchemaError

    assert issubclass(DeclarationError, SchemaError)


def test_bare_decorator_and_call_form_mark_identically() -> None:
    from trestle.plugin.surface import is_trestle_plugin, trestle

    def bare() -> None: ...

    def called() -> None: ...

    assert trestle(bare) is bare
    assert trestle(deadline=5, secrets=["x"])(called) is called
    assert is_trestle_plugin(bare) and is_trestle_plugin(called)
    with pytest.raises(TypeError):
        trestle(deadline=5, bogus=1)  # type: ignore[call-overload]
