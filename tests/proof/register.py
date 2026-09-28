"""The temporary register (CM-7) and the rollback-boundary register (MC-31).

This module is exactly CM-7: it builds the register, restates none of its
rules, and is the one implementation `meta register[, --probe, --final]`
calls (`L.P0-0d.1`).

Files: `tests/proof/temporary.toml` (base) plus one fragment per phase,
`tests/proof/temporary.d/<phase>.toml`. `tests/proof/rollback.toml` holds
MC-31's rollback-boundary entries.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TEMPORARY_PATH = ROOT / "tests" / "proof" / "temporary.toml"
TEMPORARY_D_DIR = ROOT / "tests" / "proof" / "temporary.d"
ROLLBACK_PATH = ROOT / "tests" / "proof" / "rollback.toml"

REQUIRED_KEYS = {
    "id",
    "mechanism",
    "introduced_by",
    "serves",
    "probe",
    "removal_condition",
    "removed_by",
    "permanent",
}
OPTIONAL_KEYS = {"phases", "citation"}
ALL_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS


class RegisterLoadError(ValueError):
    """A `[[entry]]` or `[[boundary]]` violates CM-7's load rules."""


def _load_toml_entries(path: Path, table: str = "entry") -> list[dict[str, Any]]:
    data = tomllib.loads(path.read_text())
    return list(data.get(table, []))


def load_entries(
    *, temporary_path: Path | None = None, d_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Load and validate every `[[entry]]` from the base file plus every
    fragment in `temporary.d/`. Raises `RegisterLoadError` on any CM-7 load
    violation."""
    temporary_path = temporary_path or TEMPORARY_PATH
    d_dir = d_dir or TEMPORARY_D_DIR

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    paths = [temporary_path] + sorted(d_dir.glob("*.toml")) if d_dir.exists() else [temporary_path]
    for path in paths:
        for entry in _load_toml_entries(path):
            _validate_entry(entry, path)
            eid = entry["id"]
            if eid in seen_ids:
                raise RegisterLoadError(f"{path}: duplicate entry id {eid!r}")
            seen_ids.add(eid)
            entries.append(entry)
    return entries


def _validate_entry(entry: dict[str, Any], path: Path) -> None:
    extra = set(entry) - ALL_KEYS
    missing = REQUIRED_KEYS - set(entry)
    if extra or missing:
        raise RegisterLoadError(
            f"{path}: entry {entry.get('id')!r}: bad schema (extra={extra}, missing={missing})"
        )
    if entry["permanent"] is not False:
        raise RegisterLoadError(
            f"{path}: entry {entry['id']!r}: permanent must be false (permanent test "
            "infrastructure is not registered, CM-7)"
        )
    removed_by = entry["removed_by"]
    if (
        isinstance(removed_by, list)
        or removed_by not in (None,)
        and not isinstance(removed_by, str)
    ):
        raise RegisterLoadError(
            f"{path}: entry {entry['id']!r}: removed_by must be a single string"
        )
    if removed_by == "named-not-removed":
        if entry.get("serves") != []:
            raise RegisterLoadError(
                f"{path}: entry {entry['id']!r}: named-not-removed requires serves = []"
            )
        if not entry.get("citation"):
            raise RegisterLoadError(
                f"{path}: entry {entry['id']!r}: named-not-removed requires a citation"
            )
    if "probe" in entry and "meta register" in entry["probe"]:
        raise RegisterLoadError(
            f"{path}: entry {entry['id']!r}: a probe calling "
            "`meta register` is refused (recursive probe)"
        )
    for phase in entry.get("phases", []):
        if "meta register" in phase.get("probe", ""):
            raise RegisterLoadError(
                f"{path}: entry {entry['id']!r}: a phase probe calling `meta register` is refused"
            )


def _run_probe(cmd: str) -> bool:
    """A probe's exit status alone decides presence: 0 = present."""
    proc = subprocess.run(["bash", "-c", cmd], cwd=ROOT, capture_output=True, text=True)
    return proc.returncode == 0


def active_phase(entry: dict[str, Any]) -> str | None:
    """The first phase whose probe exits 0 (CM-7), or `"present"`/`None` for
    a single-probe entry. Returns `None` when the entry is absent."""
    phases = entry.get("phases")
    if phases:
        for phase in phases:
            if _run_probe(phase["probe"]):
                return phase["name"]
        return None
    return "present" if _run_probe(entry["probe"]) else None


def serves_of(entry: dict[str, Any], phase_name: str | None) -> list[str]:
    phases = entry.get("phases")
    if phases and phase_name is not None:
        for phase in phases:
            if phase["name"] == phase_name:
                return list(phase.get("serves", []))
        return []
    return list(entry.get("serves", []))


def find_entry(entries: list[dict[str, Any]], entry_id: str) -> dict[str, Any] | None:
    for entry in entries:
        if entry["id"] == entry_id:
            return entry
    return None


def is_family_id(entries: list[dict[str, Any]], bare_id: str) -> bool:
    """True when `bare_id` is a prefix of >=1 real entry ids but is not
    itself one (the bare family id, e.g. `TM-P0-2`, is no entry, CM-7)."""
    if find_entry(entries, bare_id) is not None:
        return False
    return any(e["id"].startswith(bare_id + ":") for e in entries)


def _load_all_labels() -> list[dict[str, Any]]:
    from tests.proof import meta as meta_mod

    return meta_mod._load_all_labels()  # noqa: SLF001


def _expand_served(served: set[str], labels: list[dict[str, Any]]) -> set[str]:
    """An entry that serves clause/part C also serves every CSC-1 label
    whose `composes = C` (CM-7)."""
    expanded = set(served)
    for label in labels:
        composes = label.get("composes")
        if composes is not None and composes in served:
            expanded.add(label["id"])
    return expanded


def register_violations(entries: list[dict[str, Any]] | None = None) -> list[str]:
    """`meta register`'s register rule: a claim of X fails while any present
    entry (or its active phase) serves X."""
    entries = entries if entries is not None else load_entries()
    served: set[str] = set()
    for entry in entries:
        phase = active_phase(entry)
        if phase is None:
            continue
        served.update(serves_of(entry, phase))

    labels = _load_all_labels()
    served = _expand_served(served, labels)

    violations = []
    for label in labels:
        if label.get("posture") != "claim":
            continue
        if label["id"] in served or label.get("row") in served:
            violations.append(label["id"])
    return violations


def load_rollback(*, path: Path | None = None) -> list[dict[str, Any]]:
    path = path or ROLLBACK_PATH
    return list(tomllib.loads(path.read_text()).get("boundary", []))


def is_landed(merge_id: str) -> bool:
    """Wired to `trailers.landing` (L.P0-0d.7): a boundary's merge is
    landed once it has a `WR-Merge: <merge_id>` carrier reachable from
    `HEAD`."""
    from tests.proof import trailers as trailers_mod

    return trailers_mod.landing(merge_id) is not None


def rollback_violations(
    boundaries: list[dict[str, Any]] | None = None, landed_check=None
) -> list[str]:
    boundaries = boundaries if boundaries is not None else load_rollback()
    landed_check = landed_check or is_landed
    violations = []
    for boundary in boundaries:
        if landed_check(boundary["merge"]) and boundary["evidence"].startswith("pending:"):
            violations.append(
                f"{boundary['merge']}: landed but evidence is still {boundary['evidence']!r}"
            )
    return violations


def cmd_register() -> int:
    try:
        entries = load_entries()
    except RegisterLoadError as exc:
        print(f"register: {exc}")
        return 1

    violations = register_violations(entries)
    ok = True
    for v in violations:
        print(f"register: {v} is claimed while a present entry serves it")
        ok = False

    try:
        rb_violations = rollback_violations()
    except FileNotFoundError:
        rb_violations = []
    for v in rb_violations:
        print(f"register: {v}")
        ok = False

    return 0 if ok else 1


def cmd_probe(entry_id: str) -> int:
    try:
        entries = load_entries()
    except RegisterLoadError as exc:
        print(f"register: {exc}")
        return 1

    entry = find_entry(entries, entry_id)
    if entry is None:
        if is_family_id(entries, entry_id):
            print(f"register: {entry_id!r} is a family, not an entry id")
        else:
            print(f"register: unknown entry id {entry_id!r}")
        return 2

    phase = active_phase(entry)
    print(phase if phase is not None else "absent")
    return 0


def cmd_final() -> int:
    try:
        entries = load_entries()
    except RegisterLoadError as exc:
        print(f"register --final: {exc}")
        return 1

    ok = True
    for entry in entries:
        if entry["removed_by"] == "named-not-removed":
            continue
        if active_phase(entry) is not None:
            print(
                f"register --final: {entry['id']} is present and its "
                f"remover is {entry['removed_by']}"
            )
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cmd_register())
