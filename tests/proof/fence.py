"""The fence (CM-2): closed schema, loader and (from `L.P0-0d.7` /
`L.P0-0d.11` on) the rules, `fence merge` and the single-writer landing
loop. This module is exactly CM-2/CM-3/CM-4; it restates none of their
rules.

`L.P0-0d.2` builds only the schema and loader below, plus the base
`tests/proof/fence.toml` and the P0 fragment `tests/proof/fence.d/p0.toml`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from tests.proof import trailers as trailers_mod

ROOT = Path(__file__).resolve().parents[2]
FENCE_PATH = ROOT / "tests" / "proof" / "fence.toml"
FENCE_D_DIR = ROOT / "tests" / "proof" / "fence.d"

# CM-3 (b)'s limits, as plan defaults; `FENCE_PROC_MAX` is used from this
# leaf (`L.P0-0d.7`) on for every git/GitHub process this module runs. The
# landing-loop-only limits (`HOST_RUN_MAX` on, below) are stated in full
# here because `L.P0-0d.3`, `L.P0-0d.9` and B's `docker_gate` import them
# from this module, but the loop and `fence merge` that *use* them past a
# bare timeout are `L.P0-0d.11`'s own build.
FENCE_PROC_MAX = 10 * 60
HOST_RUN_MAX = 3 * 60 * 60
CI_WAIT_MAX = 7 * 60 * 60
FENCE_REMOTE_OUTAGE_MAX = 2 * 60 * 60
LANDING_MAX = 13 * 60 * 60
LANDING_RETURNS_MAX = 3

# CM-3 (b): every git and GitHub process runs with no prompts.
NO_PROMPT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
    "GH_PROMPT_DISABLED": "1",
    "GCM_INTERACTIVE": "never",
}


class FenceProcTimeout(RuntimeError):
    """A git/GitHub process exceeded `FENCE_PROC_MAX` (CM-3 (b)); the
    caller reports this as exit 5, never a prompt."""


class FenceRemoteOutage(RuntimeError):
    """A remote read (a GitHub check-run, a fetch) failed; reported as an
    outage (exit 5), never as a refusal (CM-2/CM-3)."""


def _git(
    cwd: Path, *args: str, timeout: int = FENCE_PROC_MAX, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **NO_PROMPT_ENV, **(env or {})}
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=full_env,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise FenceProcTimeout(f"git {' '.join(args)} exceeded {timeout}s") from exc


def is_ancestor(cwd: Path, ancestor: str, descendant: str) -> bool:
    return _git(cwd, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def diff_paths(cwd: Path, a: str, b: str) -> list[str]:
    proc = _git(cwd, "diff", "--name-only", f"{a}..{b}")
    return [line for line in proc.stdout.splitlines() if line]


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


def _is_checkpoint_id(merge_id: str) -> bool:
    return merge_id == "J0" or merge_id.startswith("J-")


# CM-5's success-mark table. `("tag", name)` = an annotated tag at the
# newest carrier's sha; `("check_run", name)` = a check run named `name`
# on that sha concluded `success` (read through the GitHub API).
CKPT_SUCCESS_MARK: dict[str, tuple[str, str]] = {
    "J0": ("check_run", "ckpt"),
    "J-ROOT": ("check_run", "ckpt"),
    "J-CORE": ("tag", "wr-ckpt/core"),
    "J-SINGLE": ("tag", "wr-ckpt/single"),
    "J-SLICE-A": ("tag", "wr-ckpt/slice-a"),
    "J-SLICE-B": ("tag", "wr-ckpt/slice-b"),
}


def _default_check_run_reader(cwd: Path, sha: str, name: str) -> str:
    """Read a check run's conclusion via `gh` (the executor's existing
    authentication; never a new credential, CM-3 (b)). Raises
    `FenceRemoteOutage` on any failure or timeout — never a refusal."""
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs",
                "--jq",
                f'.check_runs[] | select(.name=="{name}") | .conclusion',
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=FENCE_PROC_MAX,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise FenceRemoteOutage(f"check-run read for {sha}/{name} failed: {exc}") from exc
    if result.returncode != 0:
        raise FenceRemoteOutage(f"check-run read for {sha}/{name} failed: {result.stderr}")
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "missing"


def ckpt_succeeded(
    name: str,
    anchor: str,
    cwd: Path | None = None,
    check_run_reader=None,
) -> bool:
    """CM-5's one success test: the newest `WR-Merge: J-<NAME>` carrier
    reachable from `anchor` carries its success mark. Raises
    `FenceRemoteOutage` when a check-run read fails (CM-3: an outage, not
    a refusal) — the caller decides how to report that (exit 5)."""
    cwd = cwd or ROOT
    sha = trailers_mod.newest(name, ref=anchor, cwd=cwd)
    if sha is None:
        return False
    kind, mark = CKPT_SUCCESS_MARK.get(name, ("tag", f"wr-ckpt/{name.lower()}"))
    if kind == "tag":
        tags = _git(cwd, "tag", "--points-at", sha).stdout.split()
        return mark in tags
    reader = check_run_reader or _default_check_run_reader
    conclusion = reader(cwd, sha, mark)
    return conclusion == "success"


def missing_sha_warning(record_path: str, sha: str) -> str:
    """CM-6's one missing-sha warning text: every reader of a HOST record
    prints/raises this identically. `record.select` (`L.P0-0d.3`) emits it
    itself; `fence check` prints it too."""
    return f"WARNING: {record_path}: sha {sha} is missing from the repository"


def check_missing_records(cwd: Path, record_globs: list[str]) -> list[str]:
    """Scan the worktree for HOST record JSON files matching
    `record_globs` and warn (never raise) about any whose `sha` field is
    absent from the repository. A shallow checkout instead raises,
    naming itself as the cause (CM-6) — checked by `is_shallow`."""
    warnings = []
    for pattern in record_globs:
        for path in Path(cwd).glob(pattern):
            try:
                sha = json.loads(path.read_text()).get("sha")
            except (json.JSONDecodeError, OSError):
                continue
            if not sha:
                continue
            proc = _git(cwd, "cat-file", "-e", f"{sha}^{{commit}}")
            if proc.returncode != 0:
                if is_shallow(cwd):
                    raise FileNotFoundError(
                        f"{path}: sha {sha} not found in this shallow checkout "
                        "(fetch-depth: 0 required, CM-6)"
                    )
                warnings.append(missing_sha_warning(str(path), sha))
    return warnings


def is_shallow(cwd: Path) -> bool:
    proc = _git(cwd, "rev-parse", "--is-shallow-repository")
    return proc.stdout.strip() == "true"


@dataclass
class RuleResult:
    ok: bool
    rule: str | None = None
    message: str = "ok"
    warnings: list[str] = field(default_factory=list)


def predecessor_holds(
    requires_merge: list[str], anchor: str, cwd: Path, check_run_reader=None
) -> tuple[bool, str]:
    """CM-2's predecessor gate: a product id is satisfied by its landing
    on `anchor` (`trailers.landing`; a `WR-Fix` never satisfies it); a
    checkpoint id by `ckpt_succeeded(name, anchor)`."""
    for req in requires_merge:
        if _is_checkpoint_id(req):
            if not ckpt_succeeded(req, anchor, cwd=cwd, check_run_reader=check_run_reader):
                return False, f"checkpoint predecessor {req} has not succeeded on the base"
        else:
            if trailers_mod.landing(req, ref=anchor, cwd=cwd) is None:
                return False, f"predecessor {req} is not landed on the base"
    return True, "ok"


def check_pr(
    cfg: FenceConfig,
    cwd: Path,
    branch: str,
    head_sha: str,
    base_sha: str,
    check_run_reader=None,
) -> RuleResult:
    """`python -m tests.proof.fence check --pr`'s rules R1-R6, evaluated on
    the PR head H (`head_sha`) against the base X (`base_sha`, the current
    `origin/master`)."""
    warnings = check_missing_records(cwd, cfg.record_exempt)

    # R1
    try:
        gate = match_gate(branch, cfg.gates)
    except GateMatchError as exc:
        return RuleResult(False, "R1", str(exc), warnings)

    # R2
    ok, msg = predecessor_holds(gate.requires_merge, base_sha, cwd, check_run_reader)
    if not ok:
        return RuleResult(False, "R2", msg, warnings)

    # R3
    if not is_ancestor(cwd, base_sha, head_sha):
        return RuleResult(
            False, "R3", "base is not an ancestor of head (branch is behind)", warnings
        )

    # R4
    phase = phase_for_branch(branch, cfg.phases)
    lane = next(
        (ln for ln in cfg.lanes if ln.branch_prefix and branch.startswith(ln.branch_prefix)), None
    )
    for path in diff_paths(cwd, base_sha, head_sha):
        if path.startswith("tests/proof/fence.d/"):
            frag_phase = Path(path).stem
            if frag_phase != phase:
                return RuleResult(
                    False, "R4", f"{path}: edits another phase's fence fragment", warnings
                )
            continue
        if glob_match(path, cfg.record_exempt):
            continue
        if glob_match(path, cfg.leave):
            return RuleResult(False, "R4", f"{path}: is a Leave path", warnings)
        if lane is None or not glob_match(path, lane.globs):
            return RuleResult(False, "R4", f"{path}: is outside the lane's globs", warnings)

    # R5
    for commit in trailers_mod._commits(f"{base_sha}..{head_sha}", cwd=cwd):  # noqa: SLF001
        if commit.trailers():
            return RuleResult(False, "R5", f"commit {commit.sha} carries a trailer line", warnings)

    # R6
    if _is_checkpoint_id(gate.merge):
        if ckpt_succeeded(gate.merge, base_sha, cwd=cwd, check_run_reader=check_run_reader):
            return RuleResult(
                False, "R6", f"checkpoint {gate.merge} has already succeeded", warnings
            )

    return RuleResult(True, None, "ok", warnings)


def check_history(rng: str, cwd: Path) -> RuleResult:
    """`python -m tests.proof.fence check --history <range>`: every
    first-parent commit in `rng` is a merge commit with exactly one
    trailer, and `trailers.anomalies` is empty."""
    anomalies = trailers_mod.anomalies(rng, cwd=cwd)
    if anomalies:
        return RuleResult(False, "history-anomaly", "; ".join(anomalies))

    for commit in trailers_mod._commits(rng, cwd=cwd):  # noqa: SLF001
        if len(commit.trailers()) != 1:
            return RuleResult(
                False,
                "history-anomaly",
                f"{commit.sha}: expected exactly one trailer, found {len(commit.trailers())}",
            )
    return RuleResult(True, None, "ok")


def cmd_check_pr(cwd: Path | None = None) -> int:
    cwd = cwd or ROOT
    cfg = load_fence()
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(cwd, "fetch", "origin", "master")
    base_sha = _git(cwd, "rev-parse", "origin/master").stdout.strip()
    head_sha = _git(cwd, "rev-parse", "HEAD").stdout.strip()
    print(f"base (origin/master): {base_sha}")
    try:
        result = check_pr(cfg, cwd, branch, head_sha, base_sha)
    except FenceRemoteOutage as exc:
        print(f"fence check --pr: outage: {exc}")
        return 5
    except FenceProcTimeout as exc:
        print(f"fence check --pr: timeout: {exc}")
        return 5
    for w in result.warnings:
        print(w)
    if result.ok:
        print("fence check --pr: ok")
        return 0
    print(f"fence check --pr: refused ({result.rule}): {result.message}")
    return 3 if result.rule == "R3" else 1


def cmd_check_history(rng: str, cwd: Path | None = None) -> int:
    cwd = cwd or ROOT
    try:
        result = check_history(rng, cwd)
    except FenceRemoteOutage as exc:
        print(f"fence check --history: outage: {exc}")
        return 5
    if result.ok:
        print("fence check --history: ok")
        return 0
    print(f"fence check --history: {result.message}")
    return 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m tests.proof.fence")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check_group = check.add_mutually_exclusive_group(required=True)
    check_group.add_argument("--pr", action="store_true")
    check_group.add_argument("--history", default=None)
    args = parser.parse_args(argv)
    if args.command == "check":
        if args.pr:
            return cmd_check_pr()
        return cmd_check_history(args.history)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
