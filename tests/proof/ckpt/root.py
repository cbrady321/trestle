"""`ckpt root`: the J-ROOT condition module (CM-5, MC-28; L.CZ.9).

`TRIGGER_MERGE = "J-ROOT"`, `TAG = None`: J-ROOT is a check-run checkpoint (O-3; artifact
`ckpt-root`). The MC-28 framework evaluates it only on the newest commit carrying
`WR-Merge: J-ROOT` (DM-11); `--preview` evaluates the PR head and `--dry` lists every unmet reason
on any commit. The module is authored here, in a CZ product merge, because the J-ROOT lane's globs
hold fossils and reviews only (CM-5): a later edit after `L.J-ROOT.2`'s HOST re-run would make the
records inadmissible (CM-6). It is named neither `test_*.py` nor `*_test.py`, so its live
conditions run only under `meta ckpt root` (DM-80); its default-collected self-test
(`tests/proof/selftest/test_ckpt.py`) plants synthetic worlds and never runs a live condition.

It never imports `tests.proof.fence`: step 1 runs `python -m tests.proof.fence check --history` as a
subprocess, and only when this commit is the `WR-Merge: J-ROOT` carrier (an unlanded PR head has no
landed history to check). It resolves the candidate's HOST records only through
`record.select(gate, HEAD)` and `record.paired_docker`, and judges role 2 only through
`record.pass_set_violations` (CM-6; R5-4).

The conditions are the seven steps of `## Root integration (J-ROOT)`, one or more condition per
step so that `--dry` lists exactly what is still pending. A part that needs `L.J-ROOT.1`'s reviews
ends in `:review`, and a part that needs `L.J-ROOT.2`'s HOST records ends in `:host-record`;
every other part is evaluable on the CZ PR head:

  J-ROOT-1                  invariants: `fence check --history 5fbdd2f..HEAD` (carrier only)
  J-ROOT-2                  drift cross-check: `pytest tests/proof/drift`, `differ d1 --closure`,
                            d4, d5, d6, d7, d8
  J-ROOT-3                  performance: the four named budget nodes
  J-ROOT-3:spine-budget     the `spine` job's junit within 15 minutes (`spine_budget_check`, run
                            with `tests/proof/results/spine-junit.xml`; a CI artifact, so outside CI
                            an absent junit is not evaluable and passes, in CI it fails)
  J-ROOT-4                  security: compat security/loopback, `-k guard`, WR-AUTH profile and keep
                            refusal, the import-boundary ceiling
  J-ROOT-4:review           RV-3 ("not a sandbox") recorded, passing, an ancestor
  J-ROOT-5                  end-to-end: `pytest -m spine` (slice A)
  J-ROOT-5:host-record      slice B: the paired host-docker record's healthy-machine run PASSED,
                            inventory diff empty, both role-2 records admissible and pass-set clean
  J-ROOT-6:baseline         SC-2: ruff check, ruff format --check, mypy (zero errors), the packs
                            session (the root session's shards are the required `test` jobs)
  J-ROOT-6:host-record      SC-1: `meta enforce --scope checkpoint`
  J-ROOT-6:d1-closure       SC-3: `differ d1 --closure`
  J-ROOT-6:audit-rows       SC-4: `meta audit-rows --enforce`
  J-ROOT-6:register         SC-5: `meta register --final`
  J-ROOT-6:open-questions   SC-6: `meta open-questions --final`
  J-ROOT-7                  design quality: import boundaries and `test_mc_locality`
  J-ROOT-7:review           RV-1..RV-5 recorded (`review:RV-n`), passing, ancestors

Every condition is a pure function of a `World` (plain data). `LiveWorld` fills the same attributes
lazily from the checkout; the self-test builds synthetic `World`s.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.proof import ckpt as ckpt_mod

TRIGGER_MERGE = "J-ROOT"
TAG = None

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable
PROVEN = "PROVEN"
S0_BASE = "5fbdd2f"
STEP_MAX_S = 60 * 60  # one external process, never unbounded
SPINE_JUNIT = "tests/proof/results/spine-junit.xml"
REVIEWS = ("RV-1", "RV-2", "RV-3", "RV-4", "RV-5")
HEALTHY_MACHINE_NODE = (
    "packages/trestle-env/tests/host/test_b_spine.py::test_one_call_passed_healthy_machine"
)
PERF_NODES = [
    "tests/core/spine/test_cs1_framing.py::test_e6_rerun_quartile_ratio",
    "tests/tree/host/test_tr6_hundred.py::test_hundred_node_answer_within_budget_handles_fetch",
    "packages/trestle-env/tests/unit/test_scale_catalog.py::test_answer_within_summary_budget",
    "tests/core/spine/test_cs4_call.py::test_cancel_and_query_answer_while_terminal_call_held",
]
LINT_PATHS = [
    "trestle", "tests", "packages/trestle-packs", "packages/trestle-env", "conftest.py",
    "scripts/smoke_packs.py", "scripts/demo_pack_workflows.py",
]  # fmt: skip
CONDITION_IDS = [
    "J-ROOT-1",
    "J-ROOT-2",
    "J-ROOT-3",
    "J-ROOT-3:spine-budget",
    "J-ROOT-4",
    "J-ROOT-4:review",
    "J-ROOT-5",
    "J-ROOT-5:host-record",
    "J-ROOT-6:baseline",
    "J-ROOT-6:host-record",
    "J-ROOT-6:d1-closure",
    "J-ROOT-6:audit-rows",
    "J-ROOT-6:register",
    "J-ROOT-6:open-questions",
    "J-ROOT-7",
    "J-ROOT-7:review",
]

CommandResult = tuple[int, str]


def _pytest(*args: str) -> list[str]:
    return [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args]


def _meta(*args: str) -> list[str]:
    return [PY, "-m", "tests.proof.meta", *args]


def _differ(*args: str) -> list[str]:
    return [PY, "-m", "tests.proof.differ", *args]


# command key -> argv (the checkout's own commands; `enforce` is completed with the commit)
COMMANDS: dict[str, list[str]] = {
    "fence_history": [PY, "-m", "tests.proof.fence", "check", "--history", f"{S0_BASE}..HEAD"],
    "drift": _pytest("tests/proof/drift"),
    "d1_closure": _differ("d1", "--closure"),
    "d4": _differ("d4"),
    "d5": _differ("d5"),
    "d6": _differ("d6"),
    "d7": _differ("d7"),
    "d8": _differ("d8"),
    "perf": _pytest(*PERF_NODES),
    "spine_budget": [PY, "-m", "tests.proof.selftest.spine_budget_check", "--junit", SPINE_JUNIT],
    "security_compat": _pytest("-m", "compat", "-k", "security or loopback"),
    "security_guards": _pytest("tests/proof/selftest", "-k", "guard"),
    "security_admission": _pytest(
        "tests/core/admission/test_cl_a2_profile.py", "tests/core/answer/test_cl_b3_keep.py"
    ),
    "boundaries": _pytest("tests/proof/test_import_boundaries.py"),
    "locality": _pytest("tests/proof/selftest/test_mc_locality.py"),
    "spine": _pytest("-m", "spine"),
    "ruff_check": [PY, "-m", "ruff", "check", *LINT_PATHS],
    "ruff_format": [PY, "-m", "ruff", "format", "--check", *LINT_PATHS],
    "mypy": [PY, "-m", "mypy"],
    "packs": _pytest("-c", "pyproject.toml", "--rootdir", ".", "packages/trestle-packs/tests"),
    "audit_rows": _meta("audit-rows", "--enforce"),
    "register_final": _meta("register", "--final"),
    "open_questions_final": _meta("open-questions", "--final"),
}


# ---------------------------------------------------------------------------
# the world: every input a condition reads
# ---------------------------------------------------------------------------


@dataclass
class World:
    """Plain data: the self-test builds these; `LiveWorld` fills the same names lazily. A command
    result is `(exit code, output)`; an unlisted command is a clean exit."""

    results: dict[str, CommandResult] = field(default_factory=dict)
    is_carrier: bool = False  # this commit is the newest `WR-Merge: J-ROOT` carrier
    in_ci: bool = False  # GITHUB_ACTIONS: the spine job's junit is a CI artifact
    spine_junit: bool = False  # the junit file is present
    report: dict[str, dict[str, Any]] = field(default_factory=dict)  # the MC-02 ledger
    labels: list[dict[str, Any]] = field(default_factory=list)  # labels.d/*.toml
    host_proc: dict[str, Any] | None = None  # record.select("host-proc", HEAD)
    host_docker: dict[str, Any] | None = None  # record.paired_docker of it
    admissible: Callable[[dict[str, Any]], tuple[bool, str | None]] = (
        lambda record: (True, None)  # noqa: E731
    )
    root_reviews: dict[str, dict[str, Any]] = field(default_factory=dict)  # RV-n-root.toml
    is_ancestor: Callable[[str], bool] = lambda sha: True  # noqa: E731

    def result(self, key: str) -> CommandResult:
        return self.results.get(key, (0, ""))


def _tail(result: CommandResult, size: int = 160) -> str:
    rc, output = result
    lines = " | ".join(line for line in output.splitlines() if line.strip())
    return f"exit {rc}: {lines[-size:]}"


def _fail(problems: list[str], limit: int = 6) -> tuple[bool, str]:
    if not problems:
        return True, ""
    shown = problems[:limit]
    more = f" (+{len(problems) - limit} more)" if len(problems) > limit else ""
    return False, "; ".join(shown) + more


def _commands_ok(w: World, keys: list[str]) -> tuple[bool, str]:
    return _fail([f"{key}: {_tail(w.result(key))}" for key in keys if w.result(key)[0] != 0])


# ---------------------------------------------------------------------------
# the verdicts, by step
# ---------------------------------------------------------------------------


def verdict_1(w: World) -> tuple[bool, str]:
    if not w.is_carrier:  # an unlanded PR head has no landed history to check
        return True, ""
    return _commands_ok(w, ["fence_history"])


def verdict_2(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["drift", "d1_closure", "d4", "d5", "d6", "d7", "d8"])


def verdict_3(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["perf"])


def verdict_3_spine(w: World) -> tuple[bool, str]:
    if not w.spine_junit:
        if w.in_ci:
            return False, f"no spine job junit at {SPINE_JUNIT} (download the proof-results-*)"
        return True, ""  # a CI artifact: not evaluable outside CI
    return _commands_ok(w, ["spine_budget"])


def verdict_4(w: World) -> tuple[bool, str]:
    return _commands_ok(
        w, ["security_compat", "security_guards", "security_admission", "boundaries"]
    )


def review_problems(w: World, ids: tuple[str, ...]) -> list[str]:
    """Each review recorded for the root (`RV-n-root.toml`, valid, `outcome = "pass"`), reviewing
    a commit that is an ancestor of the candidate, and rendered `review:RV-n` in the ledger."""
    problems = []
    for rid in ids:
        record = w.root_reviews.get(rid)
        if record is None:
            problems.append(f"{rid}: no valid RV-{rid[3:]}-root.toml record")
        elif record.get("outcome") != "pass":
            problems.append(f"{rid}: outcome {record.get('outcome')!r}")
        elif not w.is_ancestor(str(record.get("sha"))):
            problems.append(f"{rid}: reviewed sha {str(record.get('sha'))[:12]} is no ancestor")
        elif w.report.get(f"review:{rid}", {}).get("status") != PROVEN:
            problems.append(f"{rid}: review:{rid} is not PROVEN in the ledger")
    return problems


def verdict_4_review(w: World) -> tuple[bool, str]:
    return _fail(review_problems(w, ("RV-3",)))


def verdict_5(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["spine"])


def verdict_5_host(w: World) -> tuple[bool, str]:
    from tests.proof.host import record as record_mod

    problems: list[str] = []
    labels = {str(lb["id"]): lb for lb in w.labels}
    for gate, record in (("host-proc", w.host_proc), ("host-docker", w.host_docker)):
        if record is None:
            problems.append(f"no admissible {gate} record at the candidate (CM-6)")
            continue
        ok, why = w.admissible(record)
        if not ok:
            problems.append(f"{gate} record {str(record.get('sha'))[:12]} is not admissible: {why}")
        problems += [
            f"{gate} pass set: {v}" for v in record_mod.pass_set_violations(record, labels)
        ]
    docker = w.host_docker
    if docker is not None:
        hits = [r for r in docker.get("results", []) if r.get("nodeid") == HEALTHY_MACHINE_NODE]
        if not hits or any(r.get("outcome") != "PASSED" for r in hits):
            problems.append(f"host-docker record: {HEALTHY_MACHINE_NODE} is not PASSED")
        if docker.get("status") != "PASSED":
            problems.append(f"host-docker record status is {docker.get('status')!r}")
        diff = docker.get("diff") or {}
        if diff.get("unattributed") != []:
            problems.append(f"host-docker diff.unattributed is {diff.get('unattributed')!r}")
        if diff.get("engine_state_changed") is not False:
            problems.append("host-docker diff.engine_state_changed is not false (WR-CON-3)")
    return _fail(problems)


def verdict_6_baseline(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["ruff_check", "ruff_format", "mypy", "packs"])


def verdict_6_host(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["enforce"])


def verdict_6_d1(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["d1_closure"])


def verdict_6_audit(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["audit_rows"])


def verdict_6_register(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["register_final"])


def verdict_6_oq(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["open_questions_final"])


def verdict_7(w: World) -> tuple[bool, str]:
    return _commands_ok(w, ["boundaries", "locality"])


def verdict_7_review(w: World) -> tuple[bool, str]:
    return _fail(review_problems(w, REVIEWS))


# ---------------------------------------------------------------------------
# the live world
# ---------------------------------------------------------------------------


def _run(argv: list[str], env: dict[str, str] | None = None) -> CommandResult:
    """One bounded subprocess in the repository root; a timeout is a failed run (exit 124)."""
    merged = dict(os.environ, **(env or {}))
    merged["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), merged.get("PYTHONPATH")]))
    try:
        proc = subprocess.run(
            argv, cwd=ROOT, env=merged, capture_output=True, text=True, timeout=STEP_MAX_S
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return proc.returncode, proc.stdout + proc.stderr


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=STEP_MAX_S
    )


class LiveWorld:
    """The same attributes as `World`, each read from the checkout the first time it is used."""

    def __init__(self, commit: str) -> None:
        self.commit = commit
        self._results: dict[str, CommandResult] = {}

    def result(self, key: str) -> CommandResult:
        if key not in self._results:
            argv = list(COMMANDS.get(key, []))
            if key == "enforce":
                argv = _meta("enforce", "--scope", "checkpoint", "--commit", self.commit)
            self._results[key] = _run(argv)
        return self._results[key]

    def __getattr__(self, name: str) -> Any:
        loader = getattr(type(self), f"_load_{name}", None)
        if loader is None:
            raise AttributeError(name)
        value = loader(self)
        setattr(self, name, value)
        return value

    def _sha(self) -> str:
        return _git("rev-parse", self.commit).stdout.strip()

    def _load_is_carrier(self) -> bool:
        from tests.proof import trailers as trailers_mod

        return trailers_mod.newest(TRIGGER_MERGE, ref=self.commit, cwd=ROOT) == self._sha()

    def _load_in_ci(self) -> bool:
        return os.environ.get("GITHUB_ACTIONS") == "true"

    def _load_spine_junit(self) -> bool:
        return (ROOT / SPINE_JUNIT).exists()

    def _load_report(self) -> dict[str, dict[str, Any]]:
        from tests.proof import ledger as ledger_mod

        try:
            return ledger_mod.render()
        except ledger_mod.VacuousLedgerError:
            return {}

    def _load_labels(self) -> list[dict[str, Any]]:
        from tests.proof import meta as meta_mod

        return list(meta_mod._load_all_labels())  # noqa: SLF001

    def _load_host_proc(self) -> dict[str, Any] | None:
        from tests.proof.host import record as record_mod

        return record_mod.select("host-proc", self.commit, cwd=ROOT)

    def _load_host_docker(self) -> dict[str, Any] | None:
        from tests.proof.host import record as record_mod

        proc = self.host_proc
        return None if proc is None else record_mod.paired_docker(proc, cwd=ROOT)

    def _load_admissible(self) -> Callable[[dict[str, Any]], tuple[bool, str | None]]:
        from tests.proof.host import record as record_mod

        return lambda record: record_mod.is_admissible(record, self.commit, ROOT)

    def _load_root_reviews(self) -> dict[str, dict[str, Any]]:
        from tests.proof import reviews as reviews_mod

        found: dict[str, dict[str, Any]] = {}
        for rid in REVIEWS:
            path = reviews_mod.REVIEWS_DIR / f"{rid}-root.toml"
            if path.exists():
                try:
                    found[rid] = reviews_mod.load(path)
                except reviews_mod.ReviewSchemaError:
                    continue
        return found

    def _load_is_ancestor(self) -> Callable[[str], bool]:
        return lambda sha: _git("merge-base", "--is-ancestor", sha, self.commit).returncode == 0


_WORLDS: dict[str, LiveWorld] = {}


def make_world(commit: str) -> World | LiveWorld:
    """The world every condition reads; the self-test replaces this seam with a synthetic one."""
    if commit not in _WORLDS:
        _WORLDS[commit] = LiveWorld(commit)
    return _WORLDS[commit]


def _condition(cond_id: str, verdict: Callable[[Any], tuple[bool, str]]) -> ckpt_mod.Condition:
    def check(commit: str) -> tuple[bool, str]:
        try:
            return verdict(make_world(commit))
        except Exception as exc:  # a condition that cannot be read is not passing
            return False, f"cannot evaluate: {type(exc).__name__}: {exc}"

    return ckpt_mod.Condition(cond_id, check)


VERDICTS: dict[str, Callable[[Any], tuple[bool, str]]] = {
    "J-ROOT-1": verdict_1,
    "J-ROOT-2": verdict_2,
    "J-ROOT-3": verdict_3,
    "J-ROOT-3:spine-budget": verdict_3_spine,
    "J-ROOT-4": verdict_4,
    "J-ROOT-4:review": verdict_4_review,
    "J-ROOT-5": verdict_5,
    "J-ROOT-5:host-record": verdict_5_host,
    "J-ROOT-6:baseline": verdict_6_baseline,
    "J-ROOT-6:host-record": verdict_6_host,
    "J-ROOT-6:d1-closure": verdict_6_d1,
    "J-ROOT-6:audit-rows": verdict_6_audit,
    "J-ROOT-6:register": verdict_6_register,
    "J-ROOT-6:open-questions": verdict_6_oq,
    "J-ROOT-7": verdict_7,
    "J-ROOT-7:review": verdict_7_review,
}
assert list(VERDICTS) == CONDITION_IDS

CONDITIONS = [_condition(cond_id, VERDICTS[cond_id]) for cond_id in CONDITION_IDS]

__all__ = ["TRIGGER_MERGE", "TAG", "CONDITIONS", "World", "make_world", "LiveWorld"]
