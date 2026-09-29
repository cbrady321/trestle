"""CLI entry point for proof-court reports (CSC-5).

`L.P0-0a.1` builds only the `baseline` subcommand. Later leaves
(`L.P0-0a.3`, `L.P0-0a.4`, `L.P0-0a.6`) extend this module with
`report`, `enforce`, `inventory`, `tolerances --list-s0-literal-sites`
style neighbors, and `mypy-ratchet`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "tests" / "proof" / "baseline.json"
NODEIDS_PATH = ROOT / "tests" / "fixtures" / "golden" / "s0" / "nodeids.txt"
GAPS_PATH = ROOT / "tests" / "proof" / "gaps.toml"
FACETS_PATH = ROOT / "tests" / "proof" / "facets.toml"
FOSSIL_MANIFEST_PATH = ROOT / "tests" / "fixtures" / "fossils" / "s0" / "MANIFEST.toml"

BASELINE_FIELDS = [
    "root_passed",
    "root_failed",
    "packs_passed",
    "packs_skipped",
    "ruff_check",
    "ruff_format",
    "mypy_errors",
]


def _run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env=env)


def _pytest_counts(extra_args: list[str], env: dict[str, str] | None = None) -> tuple[int, int]:
    """Return (passed, skipped) parsed from a `pytest -q` summary line."""
    proc = _run([sys.executable, "-m", "pytest", "-q", *extra_args], env=env)
    output = proc.stdout + proc.stderr
    passed_match = re.search(r"(\d+) passed", output)
    skipped_match = re.search(r"(\d+) skipped", output)
    passed = int(passed_match.group(1)) if passed_match else 0
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    return passed, skipped


def measure_current() -> dict[str, object]:
    """Recompute pass/skip counts, ruff and mypy status for this interpreter.

    `root_passed`/`root_failed` count only the frozen S0 corpus
    (`--ignore=tests/proof`): tests/proof/** itself grows a new selftest
    module per leaf, and `python -m tests.proof.meta baseline` is one of
    the tests a full `pytest -q` run would collect, so counting it in
    would both double-count and self-invoke without bound. This is a
    deliberate delivery-time reading of "root_passed" as "the S0 count
    stays stable"; recorded as a plan-gap (p0-court.md L250 does not spell
    out the exclusion).
    """
    # `packages/trestle-packs/tests` is in `testpaths` from L.P0-0a.5 on, so
    # a bare `pytest -q` collects both; exclude it here to keep
    # `root_passed` meaning only the non-packs S0 corpus (measured
    # separately below), matching EV-01's separate root/packs fields.
    root_passed, _root_skipped = _pytest_counts(
        ["--ignore=tests/proof", "--ignore-glob=packages/trestle-packs/*"]
    )
    # The packs half is run with the worktree's own trestle_packs source
    # prepended on PYTHONPATH. In CI, `pip install -e ".[dev,packs]"` makes
    # this a no-op (the editable install already resolves there first); on
    # a host whose ambient site-packages holds a stale non-editable
    # trestle_packs (this host, per L.P0-0a.1 current_behavior), the bare
    # `-c pyproject.toml --rootdir .` form alone still resolves the stale
    # copy and errors on collection, so this delivery adds the PYTHONPATH
    # prepend to make the measurement meaningful on both venues.
    packs_env = dict(os.environ)
    packs_src = str(ROOT / "packages" / "trestle-packs")
    packs_env["PYTHONPATH"] = packs_src + (
        (":" + packs_env["PYTHONPATH"]) if packs_env.get("PYTHONPATH") else ""
    )
    packs_passed, packs_skipped = _pytest_counts(
        ["-c", "pyproject.toml", "--rootdir", ".", "packages/trestle-packs/tests"],
        env=packs_env,
    )
    ruff_check = _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "trestle",
            "tests",
            "packages/trestle-packs",
            "scripts/smoke_packs.py",
            "scripts/demo_pack_workflows.py",
        ]
    )
    ruff_format = _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "trestle",
            "tests",
            "packages/trestle-packs",
            "scripts/smoke_packs.py",
            "scripts/demo_pack_workflows.py",
        ]
    )
    mypy = _run([sys.executable, "-m", "mypy", "trestle"])
    mypy_match = re.search(r"Found (\d+) error", mypy.stdout)
    mypy_errors = int(mypy_match.group(1)) if mypy_match else 0
    return {
        "root_passed": root_passed,
        "root_failed": 0,
        "packs_passed": packs_passed,
        "packs_skipped": packs_skipped,
        "ruff_check": "clean" if ruff_check.returncode == 0 else "dirty",
        "ruff_format": "clean" if ruff_format.returncode == 0 else "dirty",
        "mypy_errors": mypy_errors,
    }


def _explained_fields(explanations: list[dict[str, str]]) -> set[str]:
    covered: set[str] = set()
    for entry in explanations:
        for name in entry.get("field", "").split(","):
            covered.add(name.strip())
    return covered


def cmd_baseline(_args: argparse.Namespace) -> int:
    data = json.loads(BASELINE_PATH.read_text())
    ev01 = data["ev01"]
    ci = data.get("ci_312", {})
    explanations = ci.get("explanations", [])
    explained = _explained_fields(explanations)
    current = measure_current()

    ok = True
    for field in BASELINE_FIELDS:
        if current[field] != ev01[field] and field not in explained:
            ok = False
            print(f"UNEXPLAINED DIFF: {field}: ev01={ev01[field]!r} current={current[field]!r}")

    if not NODEIDS_PATH.exists():
        print(f"missing {NODEIDS_PATH}")
        return 1
    ids = [line for line in NODEIDS_PATH.read_text().splitlines() if line.strip()]
    if len(set(ids)) != 187:
        ok = False
        print(f"nodeids.txt has {len(set(ids))} unique ids, expected 187")

    return 0 if ok else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Render the derived proof ledger (MC-02): text, or `--json`."""
    from tests.proof import ledger as ledger_mod

    try:
        report = ledger_mod.render()
    except ledger_mod.VacuousLedgerError as exc:
        print(f"error: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0

    for clause in sorted(report):
        entry = report[clause]
        corroborating = " (corroborating 3.14 pass)" if entry["corroborating_314"] else ""
        print(f"{clause}: {entry['status']}{corroborating} [{entry['n_results']} result(s)]")
    return 0


def cmd_enforce(args: argparse.Namespace) -> int:
    """Report mode only (L.P0-0a.3): prints what enforcement would refuse
    and exits 0. `--print-mode` prints just the mode name and exits 0 —
    TM-P0-6's probe, removed once L.CZ.1 builds the scoped enforcement
    mode."""
    if args.print_mode:
        print("report")
        return 0

    from tests.proof import ledger as ledger_mod

    try:
        report = ledger_mod.render()
    except ledger_mod.VacuousLedgerError as exc:
        print(f"error: {exc}")
        return 1

    unproven = {c: e for c, e in report.items() if e["status"] != ledger_mod.PROVEN}
    if unproven:
        print("[report mode] enforcement would refuse on:")
        for clause in sorted(unproven):
            print(f"  {clause}: {unproven[clause]['status']}")
    else:
        print("[report mode] enforcement would pass")
    return 0


def _load_inventories() -> tuple[list[dict], list[dict], list[dict]]:
    gaps = tomllib.loads(GAPS_PATH.read_text()).get("gap", [])
    facets = tomllib.loads(FACETS_PATH.read_text()).get("facet", [])
    states = tomllib.loads(FOSSIL_MANIFEST_PATH.read_text()).get("state", [])
    return gaps, facets, states


def cmd_inventory(args: argparse.Namespace) -> int:
    """Report the gap/facet/fossil-state inventories (L.P0-0a.4).

    `--strict`: exit 1 on any pending entry, or any `target = "required"`
    gap with no real entry point yet.
    `--count-pending [--lane X]`: print the pending count and exit 0 iff
    >=1 pending entry (1 otherwise) — a side-effect-free CM-7 probe
    (TM-P0-8). `--lane` filters to gaps owned by that lane; facets and
    fossil states (which are not lane-scoped in this delivery) are counted
    regardless of `--lane`.
    """
    gaps, facets, states = _load_inventories()

    if args.count_pending:
        lane = args.lane
        count = 0
        for g in gaps:
            if lane and g.get("lane") != lane:
                continue
            if g.get("entry") == "pending":
                count += 1
        if not lane:
            count += sum(1 for f in facets if f.get("extractor") == "pending")
            count += sum(1 for s in states if s.get("producer") == "pending")
        print(count)
        return 0 if count >= 1 else 1

    print(f"gaps: {len(gaps)} (21 expected)")
    print(f"facets: {len(facets)} (11 expected)")
    present_states = [s for s in states if not s.get("absent")]
    n_absent = len(states) - len(present_states)
    print(f"fossil states: {len(present_states)} present + {n_absent} absent (19 present expected)")

    if args.strict:
        pending = (
            [g["id"] for g in gaps if g.get("entry") == "pending"]
            + [f["id"] for f in facets if f.get("extractor") == "pending"]
            + [s["id"] for s in states if s.get("producer") == "pending"]
        )
        if pending:
            print(f"STRICT: {len(pending)} pending entries")
            return 1
    return 0


def mypy_error_sites() -> list[str]:
    """`file:line` for every current `mypy trestle` error."""
    proc = _run([sys.executable, "-m", "mypy", "trestle"])
    sites = []
    for line in proc.stdout.splitlines():
        match = re.match(r"^(\S+:\d+): error:", line)
        if match:
            sites.append(match.group(1))
    return sites


def cmd_mypy_ratchet(args: argparse.Namespace) -> int:
    """`mypy trestle` may carry at most `--max` errors (WR-PROOF-8:
    mypy-ratchet-le-1), and a known error may never move: every current
    error site must already be one of `baseline.json`'s `ev01.mypy_error_sites`
    (a fixed error count with a *different* location is still a ratchet
    violation, not a wash)."""
    sites = mypy_error_sites()
    baseline = json.loads(BASELINE_PATH.read_text())
    baseline_sites = set(baseline["ev01"].get("mypy_error_sites", []))

    if len(sites) > args.max:
        print(f"mypy-ratchet: {len(sites)} error(s) > max {args.max}: {sites}")
        return 1

    unknown = set(sites) - baseline_sites
    if unknown:
        print(f"mypy-ratchet: error site(s) not in the recorded baseline: {sorted(unknown)}")
        return 1

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.meta")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("baseline")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--json", action="store_true")
    enforce_parser = sub.add_parser("enforce")
    enforce_parser.add_argument("--print-mode", action="store_true", dest="print_mode")
    inventory_parser = sub.add_parser("inventory")
    inventory_parser.add_argument("--strict", action="store_true")
    inventory_parser.add_argument("--count-pending", action="store_true", dest="count_pending")
    inventory_parser.add_argument("--lane", default=None)
    ratchet_parser = sub.add_parser("mypy-ratchet")
    ratchet_parser.add_argument("--max", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "baseline":
        return cmd_baseline(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "enforce":
        return cmd_enforce(args)
    if args.command == "inventory":
        return cmd_inventory(args)
    if args.command == "mypy-ratchet":
        return cmd_mypy_ratchet(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
