"""BFD-46 (L.NW-1.5): `run_pytest` counts setup, teardown and collection errors and writes
`errors` to the report (WR-VERIFY-4:legacy-run_pytest-errors).

Each fixture suite under `tests/fixtures/legacy-errors/` runs in a fresh interpreter (a nested
pytest session never shares state with this one), by explicit path: its modules are named
`suite_*.py`, outside every collection pattern."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.proof import tolerances

PACKS = Path(__file__).resolve().parents[2]
ROOT = PACKS.parents[1]
FIXTURES = PACKS / "tests" / "fixtures" / "legacy-errors"

DRIVER = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from trestle_packs.pytest.runner import PytestSpec, run_pytest\n"
    "result = run_pytest(PytestSpec(path=sys.argv[1]), report_dir=Path(sys.argv[2]))\n"
    "print('RESULT ' + json.dumps(result.to_dict()))\n"
)


def _run(target: Path, report_dir: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(PACKS)])}
    env.pop("TRESTLE_PROOF_GATE", None)
    proc = subprocess.run(
        [sys.executable, "-c", DRIVER, str(target), str(report_dir)],
        cwd=report_dir.parent,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, proc.stdout + proc.stderr
    return json.loads(lines[-1][len("RESULT ") :])


def test_setup_teardown_collection_errors_counted(tmp_path: Path) -> None:
    runtime = _run(FIXTURES / "suite_runtime_errors.py", tmp_path / "r1")
    # one setup error and one teardown error (its call still passed): 2 errors, 1 pass
    assert (runtime["errors"], runtime["passed"], runtime["failed"]) == (2, 1, 0)
    assert runtime["exit_code"] != 0

    collection = _run(FIXTURES / "suite_collection_error.py", tmp_path / "r2")
    assert collection["errors"] == 1
    assert collection["exit_code"] != 0

    # together: 3 errors (setup, teardown, collection), and the clean suite still counts 1 pass
    clean = _run(FIXTURES / "suite_pass.py", tmp_path / "r3")
    assert clean["errors"] == 0 and clean["passed"] == 1 and clean["exit_code"] == 0
    assert runtime["errors"] + collection["errors"] == 3


def test_directory_with_a_collection_error_counts_it_and_exits_nonzero(tmp_path: Path) -> None:
    # pytest aborts a session that has a collection error (exit code unchanged, 2): the error is
    # counted, no test runs
    suite = tmp_path / "suite"
    suite.mkdir()
    for name in ("suite_collection_error.py", "suite_pass.py"):
        (suite / name.replace("suite_", "test_")).write_text((FIXTURES / name).read_text())
    result = _run(suite, tmp_path / "r")
    assert result["errors"] == 1 and result["exit_code"] == 2 and result["passed"] == 0


def test_report_json_contains_errors(tmp_path: Path) -> None:
    result = _run(FIXTURES / "suite_runtime_errors.py", tmp_path / "report")
    payload = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert payload["errors"] == result["errors"] == 2
    assert payload["exit_code"] == result["exit_code"]


def test_fixture_suites_not_collected_by_packs_session() -> None:
    fixtures = "packages/trestle-packs/tests/fixtures/"
    for args in (
        ["-c", "pyproject.toml", "--rootdir", ".", "packages/trestle-packs/tests"],
        [],
    ):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--collect-only",
                "-p",
                "no:cacheprovider",
                *args,
            ],
            cwd=ROOT,
            env={
                **{k: v for k, v in os.environ.items() if k != "TRESTLE_PROOF_GATE"},
                "PYTHONPATH": os.pathsep.join(
                    [str(ROOT), str(PACKS), str(ROOT / "packages" / "trestle-env")]
                ),
            },
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=tolerances.JOIN_WAIT_S * 6,
        )
        assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-500:]
        nodes = [ln for ln in proc.stdout.splitlines() if "::" in ln]
        assert nodes
        assert [n for n in nodes if fixtures in n] == []
