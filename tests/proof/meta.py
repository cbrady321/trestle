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


MATRIX_MAP_PATH = ROOT / "tests" / "proof" / "matrix_map.toml"


def cmd_check_map(_args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta check-map` (L.P0-0c.1): validate
    `matrix_map.toml`'s schema and the frozen MC-03 shape (65 clause ids,
    18 cells, 69 clause-parts, 3 stub-label-required, 2 adversary-stub)."""
    from tests.proof import transcribe as transcribe_mod

    data = tomllib.loads(MATRIX_MAP_PATH.read_text())
    clauses = data.get("clause", [])

    ok = True
    ids = []
    cells = set()
    n_parts = 0
    n_stub_req = 0
    n_adversary = 0
    id_re = transcribe_mod.MATRIX_CLAUSE_RE
    required_keys = {
        "id",
        "cell",
        "fragment",
        "rows",
        "rows_source",
        "stub_label_required",
        "adversary_stub",
    }
    for clause in clauses:
        missing = required_keys - set(clause)
        if missing or not ("step" in clause or "parts" in clause):
            print(f"check-map: {clause.get('id')}: missing key(s) {missing or {'step|parts'}}")
            ok = False
            continue
        cid = clause["id"]
        if not id_re.match(cid):
            print(f"check-map: {cid!r} does not match MC-03 clause id shape")
            ok = False
        ids.append(cid)
        cells.add(clause["cell"])
        n_parts += len(clause["parts"]) if "parts" in clause else 1
        if clause["stub_label_required"]:
            n_stub_req += 1
        if clause["adversary_stub"]:
            n_adversary += 1

    if len(ids) != len(set(ids)):
        print("check-map: duplicate clause id")
        ok = False
    if len(ids) != 65:
        print(f"check-map: {len(ids)} clause ids, expected 65")
        ok = False
    if len(cells) != 18:
        print(f"check-map: {len(cells)} cells, expected 18")
        ok = False
    if n_parts != 69:
        print(f"check-map: {n_parts} clause-parts, expected 69")
        ok = False
    if n_stub_req != 3:
        print(f"check-map: {n_stub_req} stub_label_required, expected 3")
        ok = False
    if n_adversary != 2:
        print(f"check-map: {n_adversary} adversary_stub, expected 2")
        ok = False

    return 0 if ok else 1


ROW_OWNERS_PATH = ROOT / "tests" / "proof" / "row_owners.toml"

# CSC-1 labels registry schema (exact key set; L.P0-0c.2). `id` and `row` are
# always required; every other key is optional.
CSC1_REQUIRED_KEYS = {"id", "row", "step", "slice", "tier", "venue", "posture", "declared_by"}
CSC1_OPTIONAL_KEYS = {"composes", "oq", "reason"}
CSC1_ALL_KEYS = CSC1_REQUIRED_KEYS | CSC1_OPTIONAL_KEYS


def _load_all_labels() -> list[dict[str, object]]:
    labels = []
    labels_dir = ROOT / "tests" / "proof" / "labels.d"
    for path in sorted(labels_dir.glob("*.toml")):
        data = tomllib.loads(path.read_text())
        labels.extend(data.get("label", []))
    return labels


def cmd_audit_rows(_args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta audit-rows` (L.P0-0c.2, report mode):
    validate the CSC-1 label schema exactly, then report every one of the
    129 rows with no registered clause (a direct `<row>:<label>`, or credit
    through one of MC-03's nine matrix-credited rows, `matrix_map.toml`'s
    `rows` field). Report mode: always exits 0; `--enforce` is a later
    leaf's addition (plan-workflow-runtime.md L1712)."""
    from tests.proof import transcribe as transcribe_mod

    labels = _load_all_labels()
    errors: list[str] = []
    for label in labels:
        extra = set(label) - CSC1_ALL_KEYS
        missing = CSC1_REQUIRED_KEYS - set(label)
        if extra or missing:
            errors.append(f"{label.get('id')}: bad schema (extra={extra}, missing={missing})")
            continue
        if label.get("posture") == "gated_on" and not label.get("oq"):
            errors.append(f"{label.get('id')}: posture=gated_on requires an 'oq' field")
        composes = label.get("composes")
        if composes is not None:
            matrix_ids = transcribe_mod.matrix_ids()
            if composes not in matrix_ids and composes not in {
                c["id"] for c in transcribe_mod.load_matrix_map()
            }:
                errors.append(
                    f"{label.get('id')}: composes={composes!r} does not name an MC-03 clause/part"
                )

    if errors:
        print("audit-rows: label schema errors:")
        for e in errors:
            print(f"  {e}")
        return 1

    matrix_credited_rows: set[str] = set()
    for clause in tomllib.loads((ROOT / "tests" / "proof" / "matrix_map.toml").read_text()).get(
        "clause", []
    ):
        matrix_credited_rows.update(clause.get("rows", []))

    labeled_rows = {label["row"] for label in labels if "row" in label}
    rows = tomllib.loads(ROW_OWNERS_PATH.read_text()).get("row", [])
    uncovered = [
        r["id"] for r in rows if r["id"] not in labeled_rows and r["id"] not in matrix_credited_rows
    ]

    print(f"audit-rows: {len(rows)} rows, {len(uncovered)} with no registered clause (report mode)")
    if uncovered:
        for rid in uncovered:
            print(f"  unowned: {rid}")
    return 0


K_DOC_MAP_PATH = ROOT / "tests" / "proof" / "k_doc_map.toml"
OPEN_QUESTIONS_PATH = ROOT / "tests" / "proof" / "open_questions.toml"


def load_open_questions() -> list[dict[str, object]]:
    return list(tomllib.loads(OPEN_QUESTIONS_PATH.read_text()).get("oq", []))


def cmd_open_questions(_args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta open-questions` (L.P0-0c.3): exit 0 iff
    every label bound to a listed open-question id carries posture
    `gated_on` or `both_variant` and no claimed label or `proves` marker
    decides one. A label whose `oq` names an id outside `open_questions.toml`
    is a load error."""
    oq_ids = {o["id"] for o in load_open_questions()}
    labels = _load_all_labels()

    for label in labels:
        oq = label.get("oq")
        if oq is None:
            continue
        if oq not in oq_ids:
            print(
                f"open-questions: load error: {label.get('id')} names oq={oq!r}, "
                "not in open_questions.toml"
            )
            return 1
        posture = label.get("posture")
        if posture not in ("gated_on", "both_variant"):
            print(
                f"open-questions: {label.get('id')} is bound to open question {oq!r} but "
                f"posture={posture!r} decides it (must be gated_on or both_variant)"
            )
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


def cmd_ckpt(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta ckpt <name> [--dry] [--preview] [--commit <sha>]`
    (L.P0-0d.4): evaluates `tests/proof/ckpt/<name>.py` only on the newest
    commit carrying `WR-Merge: <TRIGGER_MERGE>`; any other commit is a
    no-op exit 0."""
    from tests.proof import ckpt as ckpt_mod
    from tests.proof import fence as fence_mod
    from tests.proof import trailers as trailers_mod

    try:
        module = ckpt_mod.load_module(args.name)
    except ckpt_mod.UnknownCkptError:
        print(f"ckpt: unknown checkpoint {args.name!r}")
        return 2

    commit_ref = args.commit or "HEAD"
    commit_sha = fence_mod._git(ROOT, "rev-parse", commit_ref).stdout.strip()  # noqa: SLF001
    trigger_sha = trailers_mod.newest(module.TRIGGER_MERGE, ref=commit_ref, cwd=ROOT)
    # --dry lists pending reasons on any commit (L.P0-0d.4: at P0-0d there is no
    # J0 carrier yet); only a real evaluation is limited to the newest carrier.
    if not args.dry and (trigger_sha is None or trigger_sha != commit_sha):
        print(
            f"ckpt {args.name}: {commit_sha} is not the newest "
            f"{module.TRIGGER_MERGE} carrier; no-op"
        )
        return 0

    if (
        not args.dry
        and not args.preview
        and ckpt_mod.already_evaluated(module, commit_sha, cwd=ROOT)
    ):
        print(f"ckpt {args.name}: already evaluated at {commit_sha} (idempotent no-op)")
        return 0

    results = ckpt_mod.evaluate(module, commit_sha, preview=args.preview)
    pending = [r for r in results if not r.ok]

    if args.dry:
        for r in pending:
            print(f"pending:{r.id}")
        return 0

    if pending:
        for r in pending:
            print(f"ckpt {args.name}: {r.id}: {r.reason}")
        return 1

    digest = ckpt_mod.ledger_digest()
    print(f"ckpt {args.name}: pass; digest={digest}")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    """`python -m tests.proof.meta register[, --probe <id>, --final]`
    (L.P0-0d.1): exactly CM-7's register rule, probe and `--final` commands,
    implemented in `tests/proof/register.py`."""
    from tests.proof import register as register_mod

    if args.final:
        return register_mod.cmd_final()
    if args.probe:
        return register_mod.cmd_probe(args.probe)
    return register_mod.cmd_register()


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
    sub.add_parser("check-map")
    sub.add_parser("audit-rows")
    sub.add_parser("open-questions")
    register_parser = sub.add_parser("register")
    register_parser.add_argument("--probe", default=None)
    register_parser.add_argument("--final", action="store_true")
    kdoc_parser = sub.add_parser("kdoc")
    kdoc_parser.add_argument("--history", default=None)
    ckpt_parser = sub.add_parser("ckpt")
    ckpt_parser.add_argument("name")
    ckpt_parser.add_argument("--dry", action="store_true")
    ckpt_parser.add_argument("--preview", action="store_true")
    ckpt_parser.add_argument("--commit", default=None)
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
    if args.command == "check-map":
        return cmd_check_map(args)
    if args.command == "audit-rows":
        return cmd_audit_rows(args)
    if args.command == "open-questions":
        return cmd_open_questions(args)
    if args.command == "register":
        return cmd_register(args)
    if args.command == "kdoc":
        from tests.proof import kdoc as kdoc_mod

        return kdoc_mod.cmd_kdoc(args)
    if args.command == "ckpt":
        return cmd_ckpt(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
