"""Import-boundary gate (root C.5 step 4, DM-17; L.P0-0d.5).

AST scan, not a live import: host code (`trestle/{server,query,ops,
wrapper}/*.py`, `trestle/cli.py`) never imports `trestle.workflow`,
`trestle_packs.*`, `trestle_env` or plugin code (`trestle.child.**` is
plugin-side); `trestle.workflow` imports only stdlib, `trestle.plugin`
and `trestle.common.plan`; a new `trestle_packs` subpackage imports only
stdlib and `trestle.workflow`; `trestle_env` imports only stdlib,
`trestle.plugin`, `trestle.workflow`, and adapters only in `plugins/*.py`.
A package absent at S0 (`trestle.workflow`, `trestle_env`, new
`trestle_packs` subpackages) reports not-applicable(absent), never a
pass. P0 lane modules are the `tests/pins/<lane>/` directories.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = ROOT / "tests" / "proof" / "import_allowlist.toml"

HOST_DIRS = ["trestle/server", "trestle/query", "trestle/ops", "trestle/wrapper"]
HOST_SINGLE_FILES = ["trestle/cli.py"]

# trestle.child.index/serialize/context are shared value types the host
# legitimately reads (trestle/server/projection.py::Index, R-INV-1); the
# plugin-process boundary itself is trestle.child.main/validate, which is
# what DM-17's "trestle/child/** plugin-side" actually means to exclude
# from the host (confirmed against the real S0 import graph: excluding
# only main/validate leaves exactly the one init_cmd.py allowlist entry).
FORBIDDEN_HOST_IMPORTS = (
    "trestle.workflow",
    "trestle_packs",
    "trestle_env",
    "trestle.child.main",
    "trestle.child.validate",
)

LANE_DIRS = {
    "a_lifecycle": ROOT / "tests" / "pins" / "a_lifecycle",
    "b_contract": ROOT / "tests" / "pins" / "b_contract",
    "c_surface": ROOT / "tests" / "pins" / "c_surface",
    "d_evidence": ROOT / "tests" / "pins" / "d_evidence",
    "e_packs": ROOT / "tests" / "pins" / "e_packs",
}


def load_allowlist() -> set[tuple[str, int]]:
    data = tomllib.loads(ALLOWLIST_PATH.read_text())
    return {(e["file"], e["line"]) for e in data.get("entry", [])}


def _imported_names(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    names: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append((node.module, node.lineno))
    return names


def _starts_with_any(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(f"{p}.") for p in prefixes)


def host_modules() -> list[Path]:
    modules = []
    for d in HOST_DIRS:
        modules.extend(sorted((ROOT / d).glob("*.py")))
    for f in HOST_SINGLE_FILES:
        modules.append(ROOT / f)
    return modules


def scan_host_violations(allowlist: set[tuple[str, int]] | None = None) -> list[str]:
    allowlist = allowlist if allowlist is not None else load_allowlist()
    violations = []
    for path in host_modules():
        rel = str(path.relative_to(ROOT))
        source = path.read_text()
        for name, lineno in _imported_names(source):
            if _starts_with_any(name, FORBIDDEN_HOST_IMPORTS):
                if (rel, lineno) in allowlist:
                    continue
                violations.append(f"{rel}:{lineno}: forbidden host import {name!r}")
    return violations


def test_host_imports_no_adapter_or_plugin_code() -> None:
    violations = scan_host_violations()
    assert violations == [], violations


def test_workflow_imports_only_plugin_and_core() -> None:
    """`trestle.workflow` does not exist at S0: not-applicable(absent),
    never a pass."""
    workflow_dir = ROOT / "trestle" / "workflow"
    if not workflow_dir.exists():
        return  # not-applicable(absent)
    allowed = ("trestle.plugin", "trestle.common.plan")
    for path in sorted(workflow_dir.rglob("*.py")):
        source = path.read_text()
        for name, lineno in _imported_names(source):
            if name.split(".")[0] in ("trestle",) and not _starts_with_any(name, allowed):
                if name == "trestle" or name.startswith("trestle.workflow"):
                    continue
                raise AssertionError(f"{path}:{lineno}: trestle.workflow may only import {allowed}")


# The `trestle_packs` subpackages built for the workflow loop (L.SV-5.16 fakes, L.SL-3.2/3.3
# real adapters): each imports only the standard library, itself and `trestle.workflow` (BFD-47).
WORKFLOW_PACK_SUBPACKAGES = ("fakes", "process", "container")

# Slice B's toolchain subpackage (L.RB-4.2) is held to the same rule. It is listed on its own
# line so that the B lanes' additions to the tuple above merge cleanly (L.P0-0d.33).
TOOLCHAIN_PACK_SUBPACKAGES = ("toolchain",)


def test_new_packs_subpackages_import_only_stdlib_and_workflow() -> None:
    packs_dir = ROOT / "packages" / "trestle-packs" / "trestle_packs"
    for sub in (*WORKFLOW_PACK_SUBPACKAGES, *TOOLCHAIN_PACK_SUBPACKAGES):
        directory = packs_dir / sub
        if not directory.exists():
            continue  # not-applicable(absent)
        allowed = ("trestle.workflow", f"trestle_packs.{sub}")
        for path in sorted(directory.rglob("*.py")):
            for name, lineno in _imported_names(path.read_text()):
                if name.split(".")[0] in sys.stdlib_module_names or name == "__future__":
                    continue
                assert _starts_with_any(name, allowed), (
                    f"{path.relative_to(ROOT)}:{lineno}: trestle_packs.{sub} may import only "
                    f"stdlib, trestle.workflow and itself, not {name!r}"
                )


def test_lane_modules_import_no_other_lane() -> None:
    for lane_name, lane_dir in LANE_DIRS.items():
        if not lane_dir.exists():
            continue  # not-applicable(absent): P0-1x lanes not yet built
        other_lanes = [f"tests.pins.{other}" for other in LANE_DIRS if other != lane_name]
        for path in sorted(lane_dir.rglob("*.py")):
            source = path.read_text()
            for name, lineno in _imported_names(source):
                for other in other_lanes:
                    assert not name.startswith(other), (
                        f"{path}:{lineno}: lane {lane_name!r} imports other lane module {name!r}"
                    )


def test_scanned_at_least_40_host_modules() -> None:
    assert len(host_modules()) >= 40
