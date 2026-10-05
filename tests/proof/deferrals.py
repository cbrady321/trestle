"""CM-8: deferrals. `tests/proof/deferrals.toml` is created empty by
`L.P0-0d.4` and written once, by `L.CL-D1.3`. Not a register entry
(CM-7); blocks nothing on its own."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFERRALS_PATH = ROOT / "tests" / "proof" / "deferrals.toml"

REQUIRED_KEYS = {"label", "from_step", "closes_at", "citation", "declared_by"}
OPTIONAL_KEYS = {"until"}
ALL_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS
WITHDRAWN_KEYS = {"register_id"}  # CM-12


class DeferralLoadError(ValueError):
    pass


def load_deferrals(path: Path | None = None) -> list[dict]:
    path = path or DEFERRALS_PATH
    entries = list(tomllib.loads(path.read_text()).get("deferral", []))
    for entry in entries:
        withdrawn = set(entry) & WITHDRAWN_KEYS
        if withdrawn:
            raise DeferralLoadError(f"withdrawn deferral key(s) {sorted(withdrawn)} (CM-12)")
        extra = set(entry) - ALL_KEYS
        missing = REQUIRED_KEYS - set(entry)
        if extra or missing:
            raise DeferralLoadError(f"bad deferral schema (extra={extra}, missing={missing})")
    return entries


def band_rule_violations(
    deferrals: list[dict], band_merges: set[str], proven_labels: set[str]
) -> list[str]:
    """Every deferral whose `closes_at` lies in the checkpoint's band must
    have its label PROVEN at the candidate — read at the candidate, never
    copied from an earlier checkpoint."""
    violations = []
    for d in deferrals:
        if d["closes_at"] in band_merges and d["label"] not in proven_labels:
            violations.append(f"{d['label']}: closes_at={d['closes_at']!r} but not PROVEN")
    return violations
