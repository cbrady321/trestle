"""The fence (CM-2): closed schema, loader and (from `L.P0-0d.7` /
`L.P0-0d.11` on) the rules, `fence merge` and the single-writer landing
loop. This module is exactly CM-2/CM-3/CM-4; it restates none of their
rules.

`L.P0-0d.2` builds only the schema and loader below, plus the base
`tests/proof/fence.toml` and the P0 fragment `tests/proof/fence.d/p0.toml`.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FENCE_PATH = ROOT / "tests" / "proof" / "fence.toml"
FENCE_D_DIR = ROOT / "tests" / "proof" / "fence.d"

BASE_ALLOWED_KEYS = {"leave", "record_exempt", "phases"}
FRAGMENT_TOP_ALLOWED_KEYS = {"phase", "lane", "lanes", "gate", "gates"}
LANE_ALLOWED_KEYS = {"name", "branch_prefix", "globs"}
GATE_ALLOWED_KEYS = {"branch", "merge", "requires_merge"}
WITHDRAWN_KEYS = {"hot", "shared", "requires_tag", "produces_tag"}
LANE_KEY_ALIASES = {"branch"}  # `branch` in a lane is the `branch_prefix` alias, refused
GATE_KEY_ALIASES = {"branch_prefix"}  # `branch_prefix` in a gate is the `branch` alias, refused


class FenceLoadError(ValueError):
    """The fence schema is closed (CM-2): any other key is a load error."""


@dataclass
class Lane:
    name: str
    branch_prefix: str
    globs: list[str]
    phase: str = ""


@dataclass
class Gate:
    branch: str
    merge: str
    requires_merge: list[str] = field(default_factory=list)
    phase: str = ""


@dataclass
class FenceConfig:
    leave: list[str]
    record_exempt: list[str]
    phases: dict[str, list[str]]
    lanes: list[Lane]
    gates: list[Gate]


def _check_unknown(keys: set[str], allowed: set[str], withdrawn: set[str], where: str) -> None:
    extra = keys - allowed
    hit_withdrawn = extra & withdrawn
    if hit_withdrawn:
        raise FenceLoadError(f"{where}: withdrawn fence key(s) {sorted(hit_withdrawn)} (CM-12)")
    if extra:
        raise FenceLoadError(f"{where}: unknown fence key(s) {sorted(extra)}")


def _normalize_table_array(data: dict, name: str) -> list[dict]:
    """Normalize the table forms `[lane.<n>]` / `[lanes.<n>]` (and the gate
    equivalents) to a list. `tomllib` already turns `[[lane]]` into a list
    and `[lane.<n>]` (a table keyed by a numeric-looking string) into a
    dict; this is the loader's one normalization."""
    value = data.get(name)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # `[lane.0]`, `[lane.1]`, … keyed by insertion order.
        return list(value.values())
    raise FenceLoadError(f"{name}: expected a table array, got {type(value)}")


def _load_lane(raw: dict, where: str) -> Lane:
    _check_unknown(set(raw), LANE_ALLOWED_KEYS, WITHDRAWN_KEYS | LANE_KEY_ALIASES, where)
    if "branch" in raw:
        raise FenceLoadError(f"{where}: lane key alias 'branch' is refused (use branch_prefix)")
    return Lane(
        name=raw["name"], branch_prefix=raw["branch_prefix"], globs=list(raw.get("globs", []))
    )


def _load_gate(raw: dict, where: str) -> Gate:
    _check_unknown(set(raw), GATE_ALLOWED_KEYS, WITHDRAWN_KEYS | GATE_KEY_ALIASES, where)
    if "branch_prefix" in raw:
        raise FenceLoadError(f"{where}: gate key alias 'branch_prefix' is refused (use branch)")
    return Gate(
        branch=raw["branch"], merge=raw["merge"], requires_merge=list(raw.get("requires_merge", []))
    )


def load_fence(fence_path: Path | None = None, d_dir: Path | None = None) -> FenceConfig:
    fence_path = fence_path or FENCE_PATH
    d_dir = d_dir or FENCE_D_DIR

    base = tomllib.loads(fence_path.read_text())
    _check_unknown(set(base), BASE_ALLOWED_KEYS, WITHDRAWN_KEYS, str(fence_path))

    lanes: list[Lane] = []
    gates: list[Gate] = []
    for path in sorted(d_dir.glob("*.toml")) if d_dir.exists() else []:
        frag = tomllib.loads(path.read_text())
        _check_unknown(set(frag), FRAGMENT_TOP_ALLOWED_KEYS, WITHDRAWN_KEYS, str(path))
        phase = frag.get("phase", "")
        for raw in _normalize_table_array(frag, "lane") + _normalize_table_array(frag, "lanes"):
            lane = _load_lane(raw, str(path))
            lane.phase = phase
            lanes.append(lane)
        for raw in _normalize_table_array(frag, "gate") + _normalize_table_array(frag, "gates"):
            gate = _load_gate(raw, str(path))
            gate.phase = phase
            gates.append(gate)

    return FenceConfig(
        leave=list(base.get("leave", [])),
        record_exempt=list(base.get("record_exempt", [])),
        phases=dict(base.get("phases", {})),
        lanes=lanes,
        gates=gates,
    )


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a `**`-aware glob to a regex matched against a POSIX
    relative path (no leading `/`)."""
    out = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if pattern[i : i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern[i : i + 2] == "**":
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, globs: list[str]) -> bool:
    """A `!`-prefixed glob excludes what it matches; entries apply in
    order (CM-2)."""
    matched = False
    for g in globs:
        if g.startswith("!"):
            if _glob_to_regex(g[1:]).match(path):
                matched = False
        else:
            if _glob_to_regex(g).match(path):
                matched = True
    return matched


class GateMatchError(ValueError):
    """A `wr/` branch matching zero or two gates (CM-2)."""


def match_gate(branch: str, gates: list[Gate]) -> Gate:
    """Equality, or prefix only when the gate's `branch` ends in `/`
    (CM-2). Raises `GateMatchError` on zero or two-plus matches."""
    hits = []
    for gate in gates:
        if gate.branch == branch:
            hits.append(gate)
        elif gate.branch.endswith("/") and branch.startswith(gate.branch):
            hits.append(gate)
    if len(hits) != 1:
        raise GateMatchError(f"{branch!r} matches {len(hits)} gate(s), expected exactly 1")
    return hits[0]


def phase_for_branch(branch: str, phases: dict[str, list[str]]) -> str | None:
    for phase, prefixes in phases.items():
        for prefix in prefixes:
            if branch.startswith(prefix):
                return phase
    return None
