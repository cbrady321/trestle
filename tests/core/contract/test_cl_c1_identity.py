"""L.CL-C1.4: the snapshot identity has its full shape (MC-18); declared packages are recorded
and checked at run start; the run's provenance view lists them (WR-PLAN-5)."""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.proof import harness, tolerances
from trestle.child.validate import ProvenanceMismatch, check_package_digests, package_digest
from trestle.common import codes, ids
from trestle.common.types import AdmitRequest, PublishView, RequestOutcome, RunView, WorkOrder
from trestle.server.ledger import RunLedger, ledger_path, run_dir_for
from trestle.server.plugin_schema import schema_digest
from trestle.server.snapshots import load_declared

PACKAGE = """\
from pathlib import Path

Path(__file__).with_name("imported.marker").write_text("imported", encoding="utf-8")
VALUE = 1
"""

PLUGIN = """\
import ci_pkg
from trestle.plugin.surface import Context, trestle


@trestle(packages=["ci_pkg"])
def uses_pkg(ctx: Context) -> dict[str, int]:
    return {"value": ci_pkg.VALUE}
"""

PLAIN = """\
from trestle.plugin.surface import Context, trestle


@trestle
def plain(ctx: Context) -> dict[str, int]:
    return {"n": 1}
"""

DEADLINE = """\
from trestle.plugin.surface import Context, trestle


@trestle(deadline={seconds})
def timed(ctx: Context) -> dict[str, int]:
    return {{"n": 1}}
"""

MISSING = """\
from trestle.plugin.surface import Context, trestle


@trestle(packages=["ci_no_such_package_anywhere"])
def missing(ctx: Context) -> dict[str, int]:
    return {"n": 1}
"""


def _kernel(tmp_path: Path):  # type: ignore[no-untyped-def]
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    return harness.fresh_kernel([plugin_dir], home=tmp_path / "home")


def _package_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lib = tmp_path / "lib"
    lib.mkdir()
    module = lib / "ci_pkg.py"
    module.write_text(PACKAGE, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(lib) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return module


def _admit(kernel, plugin: str) -> WorkOrder:  # type: ignore[no-untyped-def]
    """Admission alone: a run id and its spec are minted, the child is not started."""
    result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args={}))
    assert result.tag == "admitted", result
    ledger = RunLedger.open(ledger_path(run_dir_for(kernel.home, result.run_id)))
    created = ledger.last_kind("created")
    assert created is not None
    snap = kernel.registry.get(plugin)
    assert snap is not None
    return WorkOrder(
        run_id=result.run_id, snapshot_id=snap.snapshot_id, spec_hash=str(created["spec_hash"])
    )


@pytest.mark.proves("A2.2", "A2.2", "A", "core", "PROC+LOGIC", "CI")
@pytest.mark.proves(
    "WR-PLAN-5", "WR-PLAN-5:edit-after-admission-no-change", "core", "core", "PROC+LOGIC", "CI"
)
def test_edit_after_admission_does_not_change_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _package_on_path(tmp_path, monkeypatch)
    kernel = _kernel(tmp_path)
    published = kernel.control.publish_plugin(PLUGIN)
    assert isinstance(published, PublishView), published
    first_id = published.snapshot_id

    # control: unedited, the run passes and sees the package it was published with
    view = kernel.control.run(plugin="uses_pkg", wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(view, RunView), view
    assert view.state == "succeeded"
    assert view.summary == {"value": 1}

    # admitted, then the declared package is edited before the child starts
    order = _admit(kernel, "uses_pkg")
    # the edit is longer, so a stale .pyc (keyed on mtime seconds and size) cannot hide it
    module.write_text(PACKAGE.replace("VALUE = 1", "VALUE = 22"), encoding="utf-8")
    (module.parent / "imported.marker").unlink(missing_ok=True)
    kernel.control.conductor.drive(order)

    ended = kernel.control.project.await_one(order.run_id, tolerances.HARNESS_WAIT_MS)
    assert isinstance(ended, RunView), ended
    assert ended.state == "failed"
    assert ended.error is not None
    assert ended.error["code"] == codes.EXECUTION_PROVENANCE_MISMATCH
    assert "ci_pkg" in ended.error["message"]
    # neither the package nor the plugin was imported: the check ran before any plugin code
    assert not (module.parent / "imported.marker").exists()
    assert ended.summary in (None, {})

    # publishing again after the edit is a different identity, and it runs the edited code
    republished = kernel.control.publish_plugin(PLUGIN)
    assert isinstance(republished, PublishView), republished
    assert republished.snapshot_id != first_id
    again = kernel.control.run(plugin="uses_pkg", wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(again, RunView), again
    assert again.state == "succeeded"
    assert again.summary == {"value": 22}


@pytest.mark.proves(
    "WR-PLAN-5", "WR-PLAN-5:provenance-lists-imported-code", "core", "core", "PROC+LOGIC", "CI"
)
def test_provenance_view_lists_declared_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _package_on_path(tmp_path, monkeypatch)
    kernel = _kernel(tmp_path)
    assert isinstance(kernel.control.publish_plugin(PLUGIN), PublishView)
    assert isinstance(kernel.control.publish_plugin(PLAIN), PublishView)

    view = kernel.control.run(plugin="uses_pkg", wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(view, RunView) and view.state == "succeeded"
    out = kernel.control.query("run_provenance", {"run_id": view.run_id})
    assert isinstance(out, dict), out
    (row,) = out["items"]
    snap = kernel.registry.get("uses_pkg")
    assert snap is not None
    recorded = load_declared(snap).package_digests
    assert set(recorded) == {"ci_pkg"}
    assert row["packages"] == recorded
    spec = json.loads(
        (run_dir_for(kernel.home, view.run_id) / "evidence" / "spec.json").read_text("utf-8")
    )
    assert spec["provenance"] == {"packages": recorded}

    plain = kernel.control.run(plugin="plain", wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(plain, RunView) and plain.state == "succeeded"
    plain_out = kernel.control.query("run_provenance", {"run_id": plain.run_id})
    assert isinstance(plain_out, dict)
    assert plain_out["items"][0]["packages"] == {}


def test_undeclared_package_is_refused_at_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _package_on_path(tmp_path, monkeypatch)
    kernel = _kernel(tmp_path)
    refused = kernel.control.publish_plugin(MISSING)
    assert isinstance(refused, RequestOutcome), refused
    assert refused.origin == "publication"
    assert refused.code == codes.PUBLICATION_VALIDATION_FAILED
    assert kernel.registry.get("missing") is None
    assert not (tmp_path / "home" / "snapshots").exists() or not list(
        (tmp_path / "home" / "snapshots").iterdir()
    )


@pytest.mark.proves(
    "WR-PLAN-5", "WR-PLAN-5:deadline-change-moves-identity", "core", "core", "PROC+LOGIC", "CI"
)
def test_deadline_change_moves_identity(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    ids_seen: list[str] = []
    for seconds in (10, 20, 10):
        published = kernel.control.publish_plugin(DEADLINE.format(seconds=seconds))
        assert isinstance(published, PublishView), published
        ids_seen.append(published.snapshot_id)
    assert ids_seen[0] != ids_seen[1]
    assert ids_seen[0] == ids_seen[2]  # the same declaration is the same identity


def test_a_schema_is_never_rewritten_under_an_existing_id(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    published = kernel.control.publish_plugin(PLAIN)
    assert isinstance(published, PublishView), published
    snap_dir = tmp_path / "home" / "snapshots" / published.snapshot_id
    before = {
        name: (snap_dir / name).stat().st_mtime_ns for name in ("schema.json", "return_schema.json")
    }
    for name in before:
        os.utime(snap_dir / name, ns=(1_000_000_000, 1_000_000_000))
    again = kernel.control.publish_plugin(PLAIN)
    assert isinstance(again, PublishView), again
    assert again.snapshot_id == published.snapshot_id
    assert {name: (snap_dir / name).stat().st_mtime_ns for name in before} == {
        name: 1_000_000_000 for name in before
    }


def _identity(**changes: Any) -> str:
    args: dict[str, Any] = {
        "source_sha256": "a" * 64,
        "package_digests": {"pkg_a": "1" * 64, "pkg_b": "2" * 64},
        "input_schema_sha256": "b" * 64,
        "return_schema_sha256": "c" * 64,
        "declared": {
            "deadline_s": None,
            "summary_fields": [],
            "packages": ["pkg_a", "pkg_b"],
            "env_arg": None,
            "secrets": [],
        },
        "summary_budget": 4096,
        "runtime_version": "0.1.0",
    }
    args.update(changes)
    return ids.generate_snapshot_id(**args)


def _variants(rng: random.Random) -> dict[str, dict[str, Any]]:
    """One change per ingredient of the identity, each drawn at random."""
    digest = "".join(rng.choice("0123456789abcdef") for _ in range(64))
    return {
        "source": {"source_sha256": digest},
        "package edited": {"package_digests": {"pkg_a": digest, "pkg_b": "2" * 64}},
        "package added": {
            "package_digests": {"pkg_a": "1" * 64, "pkg_b": "2" * 64, "pkg_c": digest}
        },
        "package removed": {"package_digests": {"pkg_a": "1" * 64}},
        "input schema": {"input_schema_sha256": digest},
        "return schema": {"return_schema_sha256": digest},
        "deadline": {
            "declared": {**_identity_declared(), "deadline_s": rng.randint(1, 10_000)},
        },
        "summary fields": {
            "declared": {**_identity_declared(), "summary_fields": [f"f{rng.randint(0, 99)}"]},
        },
        "env arg": {"declared": {**_identity_declared(), "env_arg": f"e{rng.randint(0, 99)}"}},
        "secrets": {"declared": {**_identity_declared(), "secrets": [f"s{rng.randint(0, 99)}"]}},
        "summary budget": {"summary_budget": rng.randint(1, 4095)},
        "declared tree": {"declared_tree": digest},
        "runtime version": {"runtime_version": f"9.{rng.randint(0, 99)}.0"},
    }


def _identity_declared() -> dict[str, Any]:
    return {
        "deadline_s": None,
        "summary_fields": [],
        "packages": ["pkg_a", "pkg_b"],
        "env_arg": None,
        "secrets": [],
    }


@pytest.mark.parametrize("seed", range(20))
def test_identity_moves_on_each_ingredient(seed: int) -> None:
    rng = random.Random(seed)
    base = _identity()
    assert base == _identity()  # deterministic
    assert base.startswith("snap_") and len(base) == len("snap_") + 16
    variants = _variants(rng)
    seen = {base}
    for name, change in variants.items():
        moved = _identity(**change)
        assert moved != base, f"identity did not move when the {name} changed"
        seen.add(moved)
    assert len(seen) == len(variants) + 1  # and no two ingredients collide
    # package order in the mapping is not identity
    assert _identity(package_digests={"pkg_b": "2" * 64, "pkg_a": "1" * 64}) == base


def test_declared_tree_slot_empty_for_plain_plugin(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path)
    published = kernel.control.publish_plugin(PLAIN)
    assert isinstance(published, PublishView), published
    snap = kernel.registry.get("plain")
    assert snap is not None
    snap_dir = Path(snap.source_path).parent
    declared = load_declared(snap)
    assert declared.package_digests == {}
    assert ids.DECLARED_TREE_SLOT_EMPTY == ""
    return_schema = json.loads((snap_dir / "return_schema.json").read_text("utf-8"))
    identity_declared = declared.declared_dict()
    del identity_declared["package_digests"]
    expected = ids.generate_snapshot_id(
        source_sha256=snap.source_sha256,
        package_digests={},
        input_schema_sha256=snap.schema_sha256,
        return_schema_sha256=schema_digest(return_schema),
        declared=identity_declared,
        summary_budget=snap.summary_budget,
        runtime_version=__import__("trestle").__version__,
        declared_tree=ids.DECLARED_TREE_SLOT_EMPTY,
    )
    assert snap.snapshot_id == expected
    # the slot is part of the digest's shape: a filled slot is another identity
    filled = ids.generate_snapshot_id(
        source_sha256=snap.source_sha256,
        package_digests={},
        input_schema_sha256=snap.schema_sha256,
        return_schema_sha256=schema_digest(return_schema),
        declared=identity_declared,
        summary_budget=snap.summary_budget,
        runtime_version=__import__("trestle").__version__,
        declared_tree="x",
    )
    assert filled != expected


def test_package_digest_covers_every_file_of_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "lib"
    pkg = lib / "ci_tree"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    leaf = pkg / "sub" / "leaf.py"
    leaf.write_text("X = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(lib))

    whole = package_digest("ci_tree")
    dotted = package_digest("ci_tree.sub.leaf")
    assert package_digest("ci_tree") == whole  # stable, and nothing was imported
    assert "ci_tree" not in sys.modules
    leaf.write_text("X = 2\n", encoding="utf-8")
    assert package_digest("ci_tree") != whole
    assert package_digest("ci_tree.sub.leaf") != dotted
    with pytest.raises(ProvenanceMismatch, match="ci_tree.sub.nothing"):
        package_digest("ci_tree.sub.nothing")

    check_package_digests({"ci_tree": package_digest("ci_tree")})
    with pytest.raises(ProvenanceMismatch, match="changed since publication"):
        check_package_digests({"ci_tree": whole})
