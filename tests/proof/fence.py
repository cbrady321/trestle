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
import time as _time
import tomllib
from dataclasses import dataclass, field
from enum import IntEnum
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
CI_POLL_S = 60  # ci-status --wait poll interval

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


# ---------------------------------------------------------------------------
# CM-3: single-writer landing (L.P0-0d.11). `fence merge`, `fence ready`,
# `fence land`, `fence ci-status`, and the required-job read (CM-4).
# ---------------------------------------------------------------------------

CI_YML_PATH = ROOT / ".github" / "workflows" / "ci.yml"


def required_jobs_from_ci(path: Path | None = None) -> list[str]:
    """CM-4: the jobs of `ci.yml` a `pull_request` event runs — the
    workflow's `on` includes `pull_request` and the job's `if:`, if any,
    does not exclude that event."""
    import yaml

    path = path or CI_YML_PATH
    data = yaml.safe_load(path.read_text())
    jobs = data.get("jobs", {})
    required = []
    for job_id, job in jobs.items():
        cond = job.get("if") if isinstance(job, dict) else None
        if cond and "pull_request" not in cond and "event_name" in cond:
            continue
        required.append(job_id)
    return required


def required_check_names(path: Path | None = None) -> list[str]:
    """CM-4's required jobs as GitHub names their check runs: a matrix job
    `ancestry` runs as `ancestry (ubuntu-latest)` and `ancestry (macos-latest)`,
    and every one of those must conclude `success`. Job ids are
    `required_jobs_from_ci`'s; this expands each to its check-run names."""
    import itertools

    import yaml

    path = path or CI_YML_PATH
    jobs = yaml.safe_load(path.read_text()).get("jobs", {})
    names: list[str] = []
    for job_id in required_jobs_from_ci(path):
        job = jobs[job_id] if isinstance(jobs[job_id], dict) else {}
        label = job.get("name")
        base = label if isinstance(label, str) and "${{" not in label else job_id
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        axes = [
            v for k, v in matrix.items() if k not in ("include", "exclude") and isinstance(v, list)
        ]
        if not axes:
            names.append(base)
            continue
        for combo in itertools.product(*axes):
            names.append(f"{base} ({', '.join(str(c) for c in combo)})")
    return names


def pr_head_via_gh(branch: str, cwd: Path) -> str | None:
    """The open PR's head sha for `branch` via `gh pr view`, or `None` when
    no open PR exists. Raises `FenceRemoteOutage` on any other failure."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "headRefOid,state"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=FENCE_PROC_MAX,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **NO_PROMPT_ENV},
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise FenceRemoteOutage(f"pr view for {branch} failed: {exc}") from exc
    if proc.returncode != 0:
        if "no pull requests found" in proc.stderr.lower():
            return None
        raise FenceRemoteOutage(f"pr view for {branch} failed: {proc.stderr}")
    data = json.loads(proc.stdout)
    return data["headRefOid"] if data.get("state") == "OPEN" else None


def job_conclusions_via_gh(sha: str, cwd: Path) -> dict[str, str | None]:
    """Read every check run's conclusion for `sha` via `gh` (the
    executor's own authentication; CM-3 (b)). Raises `FenceRemoteOutage`
    on any failure."""
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=FENCE_PROC_MAX,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise FenceRemoteOutage(f"check-run list read for {sha} failed: {exc}") from exc
    if proc.returncode != 0:
        raise FenceRemoteOutage(f"check-run list read for {sha} failed: {proc.stderr}")
    data = json.loads(proc.stdout)
    result: dict[str, str | None] = {}
    for run in data.get("check_runs", []):
        if run.get("status") == "completed":
            result[run["name"]] = run.get("conclusion")
        else:
            result[run["name"]] = None
    return result


def required_jobs_status(
    required: list[str], conclusions: dict[str, str | None]
) -> tuple[bool, str | None]:
    """`(all_concluded_success, failing_job_or_None)`. A missing job
    counts as not concluded (CM-4: "a missing run ... fail")."""
    not_concluded = [j for j in required if conclusions.get(j) is None]
    if not_concluded:
        return False, None
    failing = [j for j in required if conclusions.get(j) != "success"]
    if failing:
        return False, failing[0]
    return True, None


# --- READY state -----------------------------------------------------------


def default_state_dir(cwd: Path | None = None) -> Path:
    cwd = cwd or ROOT
    proc = _git(cwd, "rev-parse", "--git-common-dir")
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = Path(cwd) / git_dir
    return git_dir / "wr-ready"


def _ready_path(state_dir: Path, merge_id: str) -> Path:
    return state_dir / f"{merge_id}.json"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def _escalate(state_dir: Path, message: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "wr-escalations.log").open("a") as fh:
        fh.write(message.rstrip("\n") + "\n")


def ready_mark(
    state_dir: Path,
    merge_id: str,
    branch: str,
    cwd: Path,
    clock=None,
    conclusions: dict[str, str | None] | None = None,
    required: list[str] | None = None,
) -> int:
    """`fence ready mark`: refuses unless every required job concluded
    `success` on the pushed PR head (exit 2 a job failed, 5 a remote
    read failed/timed out, 6 not all concluded); exits 0 when it marks."""
    clock = clock or _time.time
    try:
        req = required if required is not None else required_check_names()
        head = _git(cwd, "rev-parse", branch).stdout.strip()
        concl = conclusions if conclusions is not None else job_conclusions_via_gh(head, cwd)
    except (FenceRemoteOutage, FenceProcTimeout) as exc:
        print(f"ready mark: outage: {exc}")
        return 5
    ok, failing = required_jobs_status(req, concl)
    if not ok and failing is not None:
        print(f"ready mark: required job failed: {failing}")
        return 2
    if not ok:
        print("ready mark: required jobs have not all concluded")
        return 6

    path = _ready_path(state_dir, merge_id)
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = {}
    marked_at = existing.get("marked_at", clock())
    _atomic_write(
        path,
        {
            "state": "ready",
            "branch": branch,
            "returns": existing.get("returns", 0),
            "ckpt_rebases": existing.get("ckpt_rebases", 0),
            "reason": None,
            "landing_sha": existing.get("landing_sha"),
            "marked_at": marked_at,
            "head_sha": head,
        },
    )
    print("ready mark: marked")
    return 0


def ready_unmark(state_dir: Path, merge_id: str) -> int:
    path = _ready_path(state_dir, merge_id)
    if path.exists():
        path.unlink()
    return 0


def ready_status(state_dir: Path, merge_id: str | None = None) -> list[dict]:
    if not state_dir.exists():
        return []
    entries = []
    for path in sorted(state_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        data["_merge_id"] = path.stem
        if merge_id is None or path.stem == merge_id:
            entries.append(data)
    return entries


def ready_clear(state_dir: Path, merge_id: str) -> int:
    """`ready mark --clear`: lifts a block (resets `returns`)."""
    path = _ready_path(state_dir, merge_id)
    if not path.exists():
        return 2
    data = json.loads(path.read_text())
    data["returns"] = 0
    data["reason"] = None
    data["state"] = "ready"
    _atomic_write(path, data)
    return 0


# --- ci-status ---------------------------------------------------------


def ci_status(
    cwd: Path,
    branch: str | None = None,
    ckpt: str | None = None,
    wait: bool = False,
    clock=None,
    sleep=None,
    conclusions_reader=None,
    required: list[str] | None = None,
    ci_wait_max: int = CI_WAIT_MAX,
    since: float | None = None,
    state_dir: Path | None = None,
) -> int:
    """`fence ci-status <branch> [--wait]` / `--ckpt <name> [--wait]`.
    Exits: 0 all required `success`; 2 one concluded non-`success`;
    5 remote unavailable; 6 not all concluded; 8 `--wait` reached
    `CI_WAIT_MAX`."""
    clock = clock or _time.time
    sleep = sleep or _time.sleep
    reader = conclusions_reader or job_conclusions_via_gh
    required = required if required is not None else required_check_names()
    started = since if since is not None else clock()

    while True:
        try:
            if ckpt is not None:
                sha = trailers_mod.newest(ckpt, ref="HEAD", cwd=cwd)
                if sha is None:
                    concl: dict[str, str | None] = {}
                else:
                    concl = {"ckpt": reader(sha, cwd)}
                req = ["ckpt"]
            else:
                sha = _git(cwd, "rev-parse", branch).stdout.strip()
                concl = reader(sha, cwd)
                req = required
        except (FenceRemoteOutage, FenceProcTimeout) as exc:
            print(f"ci-status: outage: {exc}")
            return 5

        ok, failing = required_jobs_status(req, concl)
        if ok:
            print("ci-status: ok")
            return 0
        if failing is not None:
            print(f"ci-status: required job failed: {failing}")
            return 2
        if not wait:
            print("ci-status: required jobs have not all concluded")
            return 6

        if clock() - started >= ci_wait_max:
            reason = "ckpt wait exceeded" if ckpt is not None else "CI wait exceeded (pre-READY)"
            print(f"ci-status: {reason}")
            if state_dir is not None:
                _escalate(state_dir, reason)
            return 8
        sleep(CI_POLL_S)


# --- fence merge (CM-3 (b), P1-P7) --------------------------------------


class FenceMergeExit(IntEnum):
    LANDED = 0
    CRASHED = 1
    VERDICT_REFUSED = 2
    MASTER_MOVED = 3
    PUSH_REFUSED = 4
    REMOTE_UNAVAILABLE = 5
    JOBS_NOT_CONCLUDED = 6
    HEAD_MISMATCH = 7


def fence_merge(
    cfg: FenceConfig,
    cwd: Path,
    branch: str,
    expect_sha: str,
    pr_head_sha: str | None,
    job_conclusions: dict[str, str | None] | Exception | None = None,
    push_result: str = "ok",
    required: list[str] | None = None,
) -> tuple[int, str]:
    """`fence merge <branch> --expect-sha <sha>`, run only by the loop
    (P1). `push_result` lets a test simulate a push refusal without a
    real second writer: "ok" | "non-ff" | "other" | "timeout".
    Returns `(exit_code, message)`."""
    try:
        # Step 1: fetch and check the open PR (P7).
        if pr_head_sha is None or pr_head_sha != expect_sha:
            return FenceMergeExit.HEAD_MISMATCH, "no open PR or head is not --expect-sha"
        _git(cwd, "fetch", "origin", "master")
        _git(cwd, "fetch", "origin", branch)
        X = _git(cwd, "rev-parse", "origin/master").stdout.strip()
        H = _git(cwd, "rev-parse", f"origin/{branch}").stdout.strip()
        if H != expect_sha:
            return FenceMergeExit.HEAD_MISMATCH, "fetched branch head is not --expect-sha"

        # Step 2: R3.
        if not is_ancestor(cwd, X, H):
            return FenceMergeExit.MASTER_MOVED, "origin/master is not an ancestor of H"

        # Step 3: required jobs (CM-4).
        try:
            gate = match_gate(branch, cfg.gates)
        except GateMatchError as exc:
            return FenceMergeExit.VERDICT_REFUSED, f"R1: {exc}"
        required = required if required is not None else required_check_names()
        if isinstance(job_conclusions, Exception):
            raise job_conclusions
        concl = job_conclusions if job_conclusions is not None else {}
        ok, failing = required_jobs_status(required, concl)
        if not ok and failing is None:
            return FenceMergeExit.JOBS_NOT_CONCLUDED, "required jobs have not all concluded"
        if not ok:
            return FenceMergeExit.VERDICT_REFUSED, f"required job {failing} concluded non-success"

        # Step 4: build the --no-ff merge commit and derive the trailer (CM-1).
        _git(cwd, "checkout", "-q", "-B", "_fence_merge_master", X)
        trailer_kind = "WR-Fix" if trailers_mod.landing(gate.merge, ref=X, cwd=cwd) else "WR-Merge"
        merge = _git(
            cwd,
            "merge",
            "--no-ff",
            "-m",
            f"WR-Merge: {gate.merge}" if trailer_kind == "WR-Merge" else f"WR-Fix: {gate.merge}",
            H,
        )
        if merge.returncode != 0:
            _git(cwd, "merge", "--abort")
            return FenceMergeExit.VERDICT_REFUSED, f"merge failed: {merge.stderr}"
        merge_sha = _git(cwd, "rev-parse", "HEAD").stdout.strip()

        # Step 5: the verdict on the merge commit. R1-R6 are evaluated
        # against the PR's own content (X..H, the diff and commits the
        # merge carries) — never against the merge commit's own single
        # derived trailer, which R5 would otherwise always trip on.
        result = check_pr(cfg, cwd, branch, H, X)
        if not result.ok:
            return FenceMergeExit.VERDICT_REFUSED, f"{result.rule}: {result.message}"
        from tests.proof import kdoc as kdoc_mod

        missing = kdoc_mod.missing_docs(gate.merge, merge_sha, cwd=cwd)
        if missing:
            return FenceMergeExit.VERDICT_REFUSED, f"kdoc: missing docs {missing}"

        # Step 6: push, plain and non-force (P1).
        if push_result == "non-ff":
            return FenceMergeExit.MASTER_MOVED, "non-fast-forward push refusal"
        if push_result == "other":
            return FenceMergeExit.PUSH_REFUSED, "remote refused the push"
        if push_result == "timeout":
            return FenceMergeExit.REMOTE_UNAVAILABLE, "push timed out"
        push = _git(cwd, "push", "origin", f"{merge_sha}:refs/heads/master")
        if push.returncode != 0:
            if "non-fast-forward" in push.stderr or "fetch first" in push.stderr:
                return FenceMergeExit.MASTER_MOVED, "non-fast-forward push refusal"
            return FenceMergeExit.PUSH_REFUSED, push.stderr
        return FenceMergeExit.LANDED, merge_sha
    except (FenceRemoteOutage, FenceProcTimeout) as exc:
        return FenceMergeExit.REMOTE_UNAVAILABLE, str(exc)
    except Exception as exc:  # noqa: BLE001 - P6: any crash is exit 1
        return FenceMergeExit.CRASHED, f"crash: {exc}"


def landed_merge_check(merge_id: str, landing_sha: str | None, cwd: Path) -> str | None:
    """P8: a `master` merge commit carrying `M`'s trailer whose second
    parent is `landing_sha`. Returns that merge commit's sha, or `None`."""
    if landing_sha is None:
        return None
    for commit in trailers_mod._commits("HEAD", cwd=cwd):  # noqa: SLF001
        for kind, cid in commit.trailers():
            if cid != merge_id or kind not in ("WR-Merge", "WR-Fix"):
                continue
            parents = _git(cwd, "rev-list", "--parents", "-n", "1", commit.sha).stdout.split()
            if len(parents) >= 3 and parents[2] == landing_sha:
                return commit.sha
    return None


# --- fence land: the single-writer landing loop (CM-3 (a)/(b)) ------------

# Reasons that count toward `LANDING_RETURNS_MAX` and/or escalate
# immediately (CM-3 (b)'s two tables).
_RETURN_RULES: dict[str, tuple[bool, bool]] = {  # reason -> (counted, escalates)
    "fence merge crashed": (True, True),
    "verdict refused": (True, False),
    "master moved outside fence merge": (False, True),
    "push refused": (True, True),
    "remote unavailable": (True, True),
    "head moved during landing": (True, False),
    "landing tenure exceeded": (True, True),
    "CI wait exceeded": (True, True),
    "HOST run timed out": (True, True),
    "local check timed out": (True, True),
    "checks failed": (True, False),
    "rebase conflict": (True, False),
    "ready entry corrupt": (True, True),
    "lane withdrew": (False, False),
}


@dataclass
class LandingDeps:
    """Everything one landing attempt needs, injected for testing (CM-3
    (c)): a temp repo, an injected check-run source, an injected clock, a
    fake executor."""

    cfg: FenceConfig
    cwd: Path
    state_dir: Path
    clock: object = None
    pr_head_sha: str | None = None
    job_conclusions: dict[str, str | None] | Exception | None = None
    push_result: str = "ok"
    preflight: str = "ok"  # "ok" | "rebase_conflict" | "checks_failed:<name>" |
    #                         "host_timeout" | "checkpoint_rebased" | "precondition_unmet"
    rebased_head_sha: str | None = None
    ci_wait_result: int = 0  # the ci_status()-shaped exit this attempt's CI wait returns
    required: list[str] | None = None

    def now(self) -> float:
        return (self.clock or _time.time)()


def record_return(state_dir: Path, merge_id: str, reason: str, clock=None) -> None:
    """Apply CM-3 (b)'s `counted`/`escalates` rule for `reason` to the
    entry's READY data, blocking it at `LANDING_RETURNS_MAX` and
    escalating as the table requires."""
    clock = clock or _time.time
    path = _ready_path(state_dir, merge_id)
    data = json.loads(path.read_text()) if path.exists() else {}
    counted, escalates = _RETURN_RULES.get(reason.split(":")[0], (True, False))
    if reason == "checkpoint rebased: roles 1-2 must re-run":
        data["ckpt_rebases"] = data.get("ckpt_rebases", 0) + 1
        # P4 checkpoint-first: the hold's lapse clock is anchored to this
        # (the checkpoint's most recent "checkpoint rebased" return), reset
        # on every subsequent one, per root CM-3 P4.
        data["checkpoint_first_since"] = clock()
        if data["ckpt_rebases"] >= LANDING_RETURNS_MAX:
            _escalate(state_dir, f"checkpoint rebased {data['ckpt_rebases']} times")
        data["state"] = "ready"
        data["reason"] = reason
        _atomic_write(path, data)
        return
    if counted:
        data["returns"] = data.get("returns", 0) + 1
    data["reason"] = reason
    data["state"] = "blocked" if data.get("returns", 0) >= LANDING_RETURNS_MAX else "ready"
    if escalates:
        _escalate(state_dir, f"{merge_id}: {reason}")
    if data.get("returns", 0) >= LANDING_RETURNS_MAX:
        _escalate(state_dir, f"{merge_id}: returned {data['returns']} times: {reason}")
    _atomic_write(path, data)


def attempt_landing(merge_id: str, deps: LandingDeps) -> str:
    """One pass of the loop's steps 0-7 (CM-3) for `merge_id`. Returns
    `"landed"`, a named return reason, or `"skip"` when the entry is not
    ready or not present."""
    state_dir = deps.state_dir
    path = _ready_path(state_dir, merge_id)
    if not path.exists():
        return "skip"
    try:
        entry = json.loads(path.read_text())
    except json.JSONDecodeError:
        path.rename(path.with_suffix(".corrupt"))
        _escalate(state_dir, f"{merge_id}: ready entry corrupt")
        return "ready entry corrupt"
    if entry.get("state") != "ready":
        return "skip"

    # P8: the loop writes its progress record at every step boundary, so a
    # restart (or the executor's stall check, P9) always sees where it was.
    write_progress(state_dir, merge_id, "step-0", clock=deps.clock)

    # Step 0 (restart-safety, P8): a landed merge is never re-landed.
    found = landed_merge_check(merge_id, entry.get("landing_sha"), deps.cwd)
    if found:
        path.unlink(missing_ok=True)
        return "landed"

    write_progress(state_dir, merge_id, "step-1", clock=deps.clock)
    expected_head = entry.get("landing_sha") or entry.get("head_sha")
    # A stale-refresh (step 3) publishes a new head this attempt: the PR head
    # is then `rebased_head_sha`, not the marked head.
    if deps.pr_head_sha not in (expected_head, deps.rebased_head_sha or expected_head):
        record_return(state_dir, merge_id, "head moved during landing", clock=deps.clock)
        return "head moved during landing"

    # Steps 2-3: fetch + rebase-if-stale (CM-3 step 3; simplified here to
    # the injected `preflight` outcome — a real rebase runs the actual
    # `git rebase`/merge, out of scope for this leaf's own selftest).
    write_progress(state_dir, merge_id, "step-2", clock=deps.clock)
    if deps.preflight == "rebase_conflict":
        record_return(state_dir, merge_id, "rebase conflict", clock=deps.clock)
        return "rebase conflict"
    if deps.preflight == "checkpoint_rebased":
        record_return(
            state_dir, merge_id, "checkpoint rebased: roles 1-2 must re-run", clock=deps.clock
        )
        return "checkpoint rebased: roles 1-2 must re-run"
    if deps.rebased_head_sha:
        entry["landing_sha"] = deps.rebased_head_sha
        _atomic_write(path, entry)

    # Step 4: P11 checks. A PRECONDITION_UNMET host step passes its own
    # criterion (X2): it is never "checks failed". A hung local check (e.g.
    # ruff/mypy) is bounded by `HOST_RUN_MAX` and killed, same as a HOST
    # step's own timeout, but returned under its own name (P5).
    write_progress(state_dir, merge_id, "step-4", clock=deps.clock)
    if deps.preflight.startswith("checks_failed:"):
        name = deps.preflight.split(":", 1)[1]
        record_return(state_dir, merge_id, f"checks failed: {name}", clock=deps.clock)
        return f"checks failed: {name}"
    if deps.preflight == "host_timeout":
        record_return(state_dir, merge_id, "HOST run timed out", clock=deps.clock)
        return "HOST run timed out"
    if deps.preflight == "local_check_timeout":
        record_return(state_dir, merge_id, "local check timed out", clock=deps.clock)
        return "local check timed out"
    # "precondition_unmet" and "ok" both proceed: X2, root CM-3 P11.

    # Step 5: write landing_sha before any push (P7).
    write_progress(state_dir, merge_id, "step-5", clock=deps.clock)
    landing_sha = entry.get("landing_sha") or expected_head
    entry["landing_sha"] = landing_sha
    _atomic_write(path, entry)

    # Step 6: CI wait.
    write_progress(state_dir, merge_id, "step-6", clock=deps.clock)
    if deps.ci_wait_result == 8:
        record_return(state_dir, merge_id, "CI wait exceeded", clock=deps.clock)
        return "CI wait exceeded"
    if deps.ci_wait_result == 2:
        record_return(state_dir, merge_id, "checks failed: required job", clock=deps.clock)
        return "checks failed: required job"

    # Step 7: land.
    write_progress(state_dir, merge_id, "step-7", clock=deps.clock)
    exit_code, message = fence_merge(
        deps.cfg,
        deps.cwd,
        entry.get("branch", merge_id),
        landing_sha,
        deps.pr_head_sha,
        job_conclusions=deps.job_conclusions,
        push_result=deps.push_result,
        required=deps.required,
    )
    if exit_code == FenceMergeExit.LANDED:
        path.unlink(missing_ok=True)
        return "landed"
    if exit_code == FenceMergeExit.MASTER_MOVED:
        found = landed_merge_check(merge_id, landing_sha, deps.cwd)
        if found:
            path.unlink(missing_ok=True)
            return "landed"
        record_return(state_dir, merge_id, "master moved outside fence merge", clock=deps.clock)
        return "master moved outside fence merge"
    if exit_code == FenceMergeExit.REMOTE_UNAVAILABLE:
        found = landed_merge_check(merge_id, landing_sha, deps.cwd)
        if found:
            path.unlink(missing_ok=True)
            return "landed"
        record_return(state_dir, merge_id, "remote unavailable", clock=deps.clock)
        return "remote unavailable"
    if exit_code == FenceMergeExit.JOBS_NOT_CONCLUDED:
        return "recheck-ci"
    if exit_code == FenceMergeExit.VERDICT_REFUSED:
        record_return(state_dir, merge_id, "verdict refused", clock=deps.clock)
        return "verdict refused"
    if exit_code == FenceMergeExit.PUSH_REFUSED:
        record_return(state_dir, merge_id, "push refused", clock=deps.clock)
        return "push refused"
    if exit_code == FenceMergeExit.HEAD_MISMATCH:
        record_return(state_dir, merge_id, "head moved during landing", clock=deps.clock)
        return "head moved during landing"
    record_return(state_dir, merge_id, "fence merge crashed", clock=deps.clock)
    return "fence merge crashed"


def next_fifo_entry(
    state_dir: Path, clock=None, exclude: frozenset[str] = frozenset()
) -> str | None:
    """P4: the oldest READY entry by `marked_at`, except during a bounded
    checkpoint-first period: a checkpoint whose `ckpt_rebases` reached
    `LANDING_RETURNS_MAX` holds every other landing until it lands, or
    until `LANDING_MAX` passes after its most recent "checkpoint rebased"
    return without a re-mark, which lapses the hold and resumes FIFO
    (root CM-3 P4, K11K-18)."""
    clock = clock or _time.time
    ready = [
        e
        for e in ready_status(state_dir)
        if e.get("state") == "ready" and e["_merge_id"] not in exclude
    ]
    if not ready:
        return None
    for entry in ready:
        if entry.get("ckpt_rebases", 0) < LANDING_RETURNS_MAX:
            continue
        since = entry.get("checkpoint_first_since")
        if since is None:
            continue
        if clock() - since < LANDING_MAX:
            return entry["_merge_id"]
        # the hold lapsed: escalate once and clear it so FIFO resumes.
        _escalate(state_dir, f"{entry['_merge_id']}: checkpoint-first lapsed")
        path = _ready_path(state_dir, entry["_merge_id"])
        data = json.loads(path.read_text())
        data.pop("checkpoint_first_since", None)
        _atomic_write(path, data)
    ready.sort(key=lambda e: e.get("marked_at", 0))
    return ready[0]["_merge_id"]


def write_progress(state_dir: Path, merge_id: str | None, step: str, clock=None) -> None:
    clock = clock or _time.time
    _atomic_write(state_dir / ".loop-progress", {"merge": merge_id, "step": step, "ts": clock()})


def progress_is_stale(state_dir: Path, clock=None, landing_max: float = LANDING_MAX) -> bool:
    clock = clock or _time.time
    path = state_dir / ".loop-progress"
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    return (clock() - data["ts"]) > landing_max


class SecondLandRefused(RuntimeError):
    """`fence land` exit 10: another loop is running (P1)."""


def take_land_lock(state_dir: Path):
    """The loop's lifetime `flock` on `wr-ready/.land.lock` (P1). Returns
    an open file object holding the lock; the OS releases it when the
    process (or, in tests, the object) dies. Raises `SecondLandRefused`
    if another holder is live."""
    import fcntl

    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".land.lock"
    fh = open(lock_path, "a+")  # noqa: SIM115 - lifetime lock, closed by the caller/GC
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        raise SecondLandRefused("another `fence land` holds the lifetime lock") from exc
    return fh


# --- CLI paths with real dependencies (git, gh) ------------------------------


def _current_gate(cwd: Path) -> tuple[FenceConfig, str, Gate]:
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    cfg = load_fence()
    return cfg, branch, match_gate(branch, cfg.gates)


def _required_at(cwd: Path, ref: str) -> list[str]:
    """CM-4: the required names, read from `ci.yml` as `ref` carries it
    (the checkout's own copy when `ref` has none)."""
    import tempfile

    shown = _git(cwd, "show", f"{ref}:.github/workflows/ci.yml")
    if shown.returncode != 0:
        return required_check_names()
    with tempfile.TemporaryDirectory() as tmp:
        ci = Path(tmp) / "ci.yml"
        ci.write_text(shown.stdout)
        return required_check_names(ci)


def cmd_ready(action: str, clear: bool, cwd: Path | None = None) -> int:
    cwd = cwd or ROOT
    try:
        _cfg, branch, gate = _current_gate(cwd)
    except GateMatchError as exc:
        print(f"ready {action}: R1: {exc}")
        return 2
    state_dir = default_state_dir(cwd)
    if action == "status":
        for entry in ready_status(state_dir, gate.merge):
            print(json.dumps(entry, sort_keys=True))
        return 0
    if action == "unmark":
        return ready_unmark(state_dir, gate.merge)
    if clear:
        return ready_clear(state_dir, gate.merge)
    return ready_mark(state_dir, gate.merge, branch, cwd)


def _drop_worktree(cwd: Path, path: Path) -> None:
    _git(cwd, "worktree", "remove", "--force", str(path))
    _git(cwd, "worktree", "prune")
    _git(cwd, "branch", "-D", "_fence_merge_master")


def _landing_worktree(cwd: Path, path: Path) -> Path:
    """The clean worktree the landing runs in, re-created for each landing
    and detached at the fetched `origin/master` (CM-3 step 2)."""
    _drop_worktree(cwd, path)
    if _git(cwd, "fetch", "origin", "master").returncode != 0:
        raise FenceRemoteOutage("fetch of origin/master failed")
    added = _git(cwd, "worktree", "add", "--detach", str(path), "origin/master")
    if added.returncode != 0:
        raise RuntimeError(f"landing worktree: {added.stderr}")
    return path


def cmd_merge(
    branch: str, expect_sha: str, cwd: Path | None = None, landing_dir: Path | None = None
) -> int:
    """`fence merge <branch> --expect-sha <sha>` with the real PR head,
    check-run conclusions and required jobs, and a plain non-force push."""
    cwd = cwd or ROOT
    landing_dir = landing_dir or cwd.parent / "landing"
    try:
        cfg = load_fence()
        pr_head = pr_head_via_gh(branch, cwd)
        conclusions: dict[str, str | None] | Exception = job_conclusions_via_gh(expect_sha, cwd)
    except FenceRemoteOutage as exc:
        print(f"fence merge: outage: {exc}")
        return int(FenceMergeExit.REMOTE_UNAVAILABLE)
    except Exception as exc:  # noqa: BLE001 - P6: any crash is exit 1
        print(f"fence merge: crash: {exc}")
        return int(FenceMergeExit.CRASHED)
    try:
        work = _landing_worktree(cwd, landing_dir)
        _git(work, "fetch", "origin", branch)
        code, message = fence_merge(
            cfg,
            work,
            branch,
            expect_sha,
            pr_head,
            job_conclusions=conclusions,
            required=_required_at(work, f"origin/{branch}"),
        )
    except (FenceRemoteOutage, FenceProcTimeout) as exc:
        code, message = FenceMergeExit.REMOTE_UNAVAILABLE, str(exc)
    except Exception as exc:  # noqa: BLE001
        code, message = FenceMergeExit.CRASHED, f"crash: {exc}"
    finally:
        _drop_worktree(cwd, landing_dir)
    print(f"fence merge: {message}")
    return int(code)


def _refresh_stale(work: Path, branch: str, head: str) -> str:
    """CM-3 step 3, `git rebase` being forbidden in this delivery: when
    `origin/master` is not an ancestor of `head` (a landing before this
    one moved it), merge it forward on the branch and push the branch with
    a plain non-force push (CM-2 R3 is satisfied by ancestry). Returns the
    new head, `head` itself when not stale, or the return reason."""
    if is_ancestor(work, "origin/master", head):
        return head
    if _git(work, "checkout", "-q", "--detach", head).returncode != 0:
        raise RuntimeError("refresh: cannot check out the branch head")
    merged = _git(work, "merge", "--no-edit", "origin/master")
    if merged.returncode != 0:
        _git(work, "merge", "--abort")
        return "rebase conflict"
    new_head = _git(work, "rev-parse", "HEAD").stdout.strip()
    pushed = _git(work, "push", "origin", f"HEAD:refs/heads/{branch}")
    if pushed.returncode != 0:
        return "push refused"
    return new_head


def _real_deps(
    merge_id: str, entry: dict, cfg: FenceConfig, cwd: Path, state_dir: Path, work: Path
) -> LandingDeps | str:
    """`LandingDeps` from real `gh`; a string is a return reason to record
    instead of attempting the landing. A stale READY branch is refreshed by
    merging `origin/master` forward (CM-3 step 3) and CI is awaited on the
    new head. The P11 re-runs are not wired here."""
    branch = entry.get("branch", merge_id)
    expected = entry.get("landing_sha") or entry.get("head_sha")
    try:
        pr_head = pr_head_via_gh(branch, cwd)
        _git(work, "fetch", "origin", branch)
        target, rebased = expected, None
        if pr_head == expected:
            refreshed = _refresh_stale(work, branch, expected)
            if refreshed in ("rebase conflict", "push refused"):
                return refreshed
            if refreshed != expected:
                target = rebased = pr_head = refreshed
                _git(work, "fetch", "origin", branch)
        required = _required_at(work, f"origin/{branch}")
        if pr_head == target:
            wait = ci_status(
                work,
                branch=f"origin/{branch}",
                wait=True,
                conclusions_reader=lambda _sha, where: job_conclusions_via_gh(target, where),
                required=required,
                state_dir=state_dir,
            )
            if wait == 5:
                return "remote unavailable"
            conclusions: dict[str, str | None] | Exception = job_conclusions_via_gh(target, cwd)
        else:
            wait, conclusions = 0, {}
    except FenceRemoteOutage:
        return "remote unavailable"
    return LandingDeps(
        cfg=cfg,
        cwd=work,
        state_dir=state_dir,
        pr_head_sha=pr_head,
        job_conclusions=conclusions,
        ci_wait_result=wait,
        required=required,
        rebased_head_sha=rebased,
    )


def cmd_land(cwd: Path | None = None, once: bool = False, landing_dir: Path | None = None) -> int:
    """`fence land`: the CM-3 single-writer loop. Holds the lifetime lock
    (exit 10 if another loop has it) and lands READY entries FIFO. Forever
    by default; `--once` drains the READY queue (each entry attempted at
    most once) and exits 0."""
    cwd = cwd or ROOT
    landing_dir = landing_dir or cwd.parent / "landing"
    state_dir = default_state_dir(cwd)
    try:
        lock = take_land_lock(state_dir)
    except SecondLandRefused as exc:
        print(f"fence land: {exc}")
        return 10
    try:
        cfg = load_fence()
        tried: set[str] = set()
        while True:
            merge_id = next_fifo_entry(state_dir, exclude=frozenset(tried))
            if merge_id is None:
                write_progress(state_dir, None, "idle")
                if once:
                    return 0
                _time.sleep(CI_POLL_S)
                tried.clear()
                continue
            tried.add(merge_id)
            started = _time.time()
            while True:
                write_progress(state_dir, merge_id, "prepare")
                entry = json.loads(_ready_path(state_dir, merge_id).read_text())
                try:
                    work = _landing_worktree(cwd, landing_dir)
                    deps = _real_deps(merge_id, entry, cfg, cwd, state_dir, work)
                    if isinstance(deps, str):
                        record_return(state_dir, merge_id, deps)
                        outcome = deps
                    else:
                        outcome = attempt_landing(merge_id, deps)
                except FenceRemoteOutage:
                    record_return(state_dir, merge_id, "remote unavailable")
                    outcome = "remote unavailable"
                finally:
                    _drop_worktree(cwd, landing_dir)
                if outcome != "recheck-ci":
                    print(f"fence land: {merge_id}: {outcome}")
                    break
                if _time.time() - started >= CI_WAIT_MAX:
                    record_return(state_dir, merge_id, "CI wait exceeded")
                    print(f"fence land: {merge_id}: CI wait exceeded")
                    break
                _time.sleep(CI_POLL_S)
    finally:
        lock.close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m tests.proof.fence")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check_group = check.add_mutually_exclusive_group(required=True)
    check_group.add_argument("--pr", action="store_true")
    check_group.add_argument("--history", default=None)

    merge_parser = sub.add_parser("merge")
    merge_parser.add_argument("branch")
    merge_parser.add_argument("--expect-sha", required=True)

    ready_parser = sub.add_parser("ready")
    ready_parser.add_argument("action", choices=["mark", "unmark", "status"])
    ready_parser.add_argument("--clear", action="store_true")

    ci_status_parser = sub.add_parser("ci-status")
    ci_status_parser.add_argument("branch", nargs="?")
    ci_status_parser.add_argument("--ckpt", default=None)
    ci_status_parser.add_argument("--wait", action="store_true")

    land_parser = sub.add_parser("land")
    land_parser.add_argument("--once", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "check":
        if args.pr:
            return cmd_check_pr()
        return cmd_check_history(args.history)
    if args.command == "ci-status":
        return ci_status(ROOT, branch=args.branch, ckpt=args.ckpt, wait=args.wait)
    if args.command == "merge":
        return cmd_merge(args.branch, args.expect_sha)
    if args.command == "ready":
        if args.clear and args.action != "mark":
            parser.error("--clear applies to `ready mark` only")
        return cmd_ready(args.action, args.clear)
    return cmd_land(once=args.once)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
