"""The `test` job's shards (L.P0-0d.17): the one list ci.yml's `test-shard` matrix reads.

CI's unsharded `pytest -q` is split into explicit path groups, one matrix job each; the
`test` job only aggregates them, so CM-4's required name `test` is kept. `misc` ignores
every path another shard names, so the shards' union is the unsharded collection by
construction. Each package testpath (`packages/<name>/tests`, pyproject `testpaths`) is its
own shard, run once in the CSC-12 form (`-c pyproject.toml --rootdir .`, the root conftest
and proof plugin kept) whose node ids equal the unsharded run's; the root session runs none
of them a second time. `check` proves it against `pytest --collect-only -q`: every shard
collects something, no node is in two shards, the union equals the full collection, and
ci.yml's matrix names exactly these shards. Stdlib only.

    python -m tests.proof.ci_shards args <shard>   # the pytest arguments, one per line
    python -m tests.proof.ci_shards check          # the partition proof (exit 1 on a gap)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI_YML_PATH = ROOT / ".github" / "workflows" / "ci.yml"

_BASELINE = "tests/proof/selftest/test_baseline.py"
# Each baseline test re-measures the S0 corpus in a subprocess; split, they are two poles.
_PLANTED = f"{_BASELINE}::test_meta_baseline_exits_1_on_unexplained_field"
_FENCE = [
    "tests/proof/selftest/test_fence.py",
    "tests/proof/selftest/test_fence_cli.py",
    "tests/proof/selftest/test_fence_merge.py",
    "tests/proof/selftest/test_landing_loop.py",
]

# Path splits of the former `root` shard, by measured CI time (TEST-SPEEDUP-ANALYSIS: tree/host
# with facts is one ~440 s block, the largest left).
_TREE_HOST = ["tests/tree/host", "tests/tree/facts"]
_PATHS: dict[str, list[str]] = {
    "core": ["tests/core"],
    "single": ["tests/single"],
    "tree-host": list(_TREE_HOST),
    "tree": ["tests/tree", *(f"--ignore={p}" for p in _TREE_HOST)],
}


def package_testpaths(pyproject: Path = ROOT / "pyproject.toml") -> dict[str, str]:
    """`packages/<name>/tests` entries of pyproject `testpaths`, keyed by shard name
    (`trestle-packs` -> `packs`)."""
    paths = tomllib.loads(pyproject.read_text())["tool"]["pytest"]["ini_options"]["testpaths"]
    return {p.split("/")[1].removeprefix("trestle-"): p for p in paths if p.startswith("packages/")}


_PACKAGES = package_testpaths()

SHARDS: dict[str, list[str]] = {
    "baseline": [_BASELINE, f"--deselect={_PLANTED}"],
    "planted": [_PLANTED],
    "fence": list(_FENCE),
    "proof": ["tests/proof", f"--ignore={_BASELINE}", *(f"--ignore={f}" for f in _FENCE)],
    "pins": ["tests/pins"],
    **_PATHS,
    # tests/*.py, tests/spine and whatever else under tests/ no other shard names (an explicit
    # path: an `--ignore` does not drop a testpaths entry itself, the package testpaths)
    "misc": [
        "tests",
        *(f"--ignore=tests/{d}" for d in ("proof", "pins", "core", "single", "tree")),
    ],
    **{name: ["-c", "pyproject.toml", "--rootdir", ".", p] for name, p in _PACKAGES.items()},
}


def collect(args: list[str]) -> list[str]:
    """The node ids `pytest --collect-only -q <args>` selects, in order."""
    env = {k: v for k, v in os.environ.items() if k != "TRESTLE_PROOF_GATE"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"collect {args} exited {proc.returncode}:\n{proc.stdout[-2000:]}")
    ids: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            break  # node ids end at the first blank line; a warnings summary may follow
        if "::" in line:
            ids.append(line.strip())
    return ids


def matrix_shards(ci_text: str) -> list[str]:
    """The `shard:` list of ci.yml's `test-shard` matrix (flow form, one line)."""
    match = re.search(r"^\s*shard:\s*\[([^\]]*)\]", ci_text, re.M)
    return [s.strip() for s in match.group(1).split(",")] if match else []


def partition_problems(full: list[str], by_shard: dict[str, list[str]]) -> list[str]:
    problems = []
    seen: dict[str, str] = {}
    for name, ids in by_shard.items():
        if not ids:
            problems.append(f"shard {name!r} collects nothing")
        for nodeid in ids:
            if nodeid in seen:
                problems.append(f"{nodeid} is in shards {seen[nodeid]!r} and {name!r}")
            seen[nodeid] = name
    missing = sorted(set(full) - set(seen))
    extra = sorted(set(seen) - set(full))
    problems += [f"{n} is in no shard" for n in missing]
    problems += [f"{n} is in a shard but not the unsharded collection" for n in extra]
    return problems


def check() -> int:
    runs = {"": [], **SHARDS}
    with ThreadPoolExecutor(max_workers=len(runs)) as pool:
        got = dict(zip(runs, pool.map(collect, runs.values()), strict=True))
    full = got.pop("")
    problems = partition_problems(full, got)
    matrix = matrix_shards(CI_YML_PATH.read_text())
    if matrix != list(SHARDS):
        problems.append(f"ci.yml test-shard matrix {matrix} != ci_shards.SHARDS {list(SHARDS)}")
    for problem in problems:
        print(problem)
    sizes = ", ".join(f"{n}={len(ids)}" for n, ids in got.items())
    print(f"ci_shards: {len(full)} nodes; {sizes}; {'FAIL' if problems else 'ok'}")
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "args" and argv[1] in SHARDS:
        print("\n".join(SHARDS[argv[1]]))
        return 0
    if argv == ["check"]:
        return check()
    print(f"usage: python -m tests.proof.ci_shards args {{{','.join(SHARDS)}}} | check")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
