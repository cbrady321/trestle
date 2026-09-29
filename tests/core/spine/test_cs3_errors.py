"""CS-3 errors explain (L.CS-3.1..3.3; MC-15, MC-CORE-04, MC-CORE-14, SA-13): a failed run's
explanation is written once by the child (`evidence/child_error.json`), folded into the ledger's
`error_record` row (the sole authority), and read back by `RunView.error` and `last_error`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from tests.proof import tolerances
from trestle.common import codes, errtext

REPO = Path(__file__).resolve().parents[3]
SENTINEL = "SENTINEL-cs3-child-failure-5e2a"

_HEADER = "from trestle.plugin.surface import Context, trestle\n\n"
PLUGINS = {
    "import_error": "import a_module_that_does_not_exist_cs3\n\n"
    + _HEADER
    + "@trestle\ndef import_error(ctx: Context) -> dict[str, int]:\n    return {}\n",
    "missing_arg": _HEADER
    + "@trestle\ndef missing_arg(ctx: Context, required: str) -> dict[str, str]:\n"
    "    return {'got': required}\n",
    "raises": "import os\n\n" + _HEADER + "@trestle\ndef raises(ctx: Context) -> dict[str, int]:\n"
    "    home = os.environ['TRESTLE_HOME']\n"
    "    raise RuntimeError(f'boom at {home}/snapshots in {os.getcwd()}')\n",
    "unencodable": _HEADER
    + "@trestle\ndef unencodable(ctx: Context) -> object:\n    return object()\n",
    "fine": _HEADER
    + "@trestle\ndef fine(ctx: Context) -> dict[str, bool]:\n    return {'ok': True}\n",
}


def run_child(tmp_path: Path, name: str, args: dict[str, Any] | None = None) -> tuple[int, Path]:
    """Run the child entry point alone over a hand-built run directory (no admission, so a plugin
    that admission would refuse still reaches the child)."""
    home = tmp_path / "home"
    snapshot = home / "snapshots" / f"snap_{name}"
    snapshot.mkdir(parents=True)
    (snapshot / "plugin.py").write_text(textwrap.dedent(PLUGINS[name]), encoding="utf-8")
    run_dir = home / "runs" / "2099-01" / f"run_{name}"
    evidence = run_dir / "evidence"
    evidence.mkdir(parents=True)
    (run_dir / "work").mkdir()
    spec = {
        "plugin": name,
        "version": "0.1.0",
        "snapshot_id": f"snap_{name}",
        "args": args or {},
        "args_hash": "h",
        "source_sha256": "s",
        "schema_sha256": "s",
        "manifest_sha256": "m",
        "python_version": "3",
        "platform": "p",
        "summary_budget": 1000,
        "timeout_s": 60,
        "resolved_artifacts": {},
    }
    (evidence / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO}{os.pathsep}{REPO / 'packages' / 'trestle-packs'}"
    env["TRESTLE_HOME"] = str(home)
    done = subprocess.run(
        [sys.executable, "-m", "trestle.child.main", "--run-dir", str(run_dir)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        timeout=tolerances.HARNESS_WAIT_MS / 1000,
    )
    return done.returncode, run_dir


def child_error(run_dir: Path) -> dict[str, Any]:
    loaded = json.loads((run_dir / "evidence" / "child_error.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_child_failure_phases_get_distinct_codes(tmp_path: Path) -> None:
    expected = {
        "import_error": ("load", codes.EXECUTION_IMPORT_FAILED),
        "missing_arg": ("bind", codes.EXECUTION_BIND_FAILED),
        "raises": ("call", codes.EXECUTION_PLUGIN_RAISED),
        "unencodable": ("encode", codes.EXECUTION_RESULT_UNENCODABLE),
    }
    seen: dict[str, str] = {}
    for name, (phase, code) in expected.items():
        status, run_dir = run_child(tmp_path / name, name)
        assert status == 1, name  # the exit-code contract is unchanged
        record = child_error(run_dir)
        assert set(record) == {"code", "phase", "message", "exc_type"}
        assert (record["phase"], record["code"]) == (phase, code), name
        assert record["code"] in codes.EXECUTION_CODES
        assert record["message"] and len(record["message"].encode("utf-8")) <= errtext.MESSAGE_MAX
        assert not list((run_dir / "evidence").glob("*.tmp"))  # atomic: no partial left behind
        seen[name] = record["code"]
    assert len(set(seen.values())) == len(expected)


def test_child_failure_message_holds_no_host_path(tmp_path: Path) -> None:
    _status, run_dir = run_child(tmp_path, "raises")
    message = child_error(run_dir)["message"]
    home = tmp_path / "home"
    assert str(home) not in message and str(home.resolve()) not in message
    assert str(run_dir) not in message
    assert "<home>/snapshots" in message and "boom at" in message
    assert "<cwd>" in message  # the work directory the child stands in


def test_child_success_and_no_result_write_no_error(tmp_path: Path) -> None:
    status, run_dir = run_child(tmp_path, "fine")
    assert status == 0
    assert not (run_dir / "evidence" / "child_error.json").exists()
    assert (run_dir / "evidence" / "result.json").exists()


def test_execution_code_vocabulary_is_the_nine_named() -> None:
    names = {
        "import_failed",
        "bind_failed",
        "plugin_raised",
        "result_unencodable",
        "provenance_mismatch",
        "cancelled",
        "deadline_exceeded",
        "worker_exit",
        "interrupted",
    }
    assert codes.EXECUTION_CODES == {f"execution.{n}" for n in names}
    for n in names:
        assert getattr(codes, f"EXECUTION_{n.upper()}") == f"execution.{n}"


def test_sanitize_bounds_and_replaces_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    run = home / "runs" / "2099-01" / "r1"
    cwd = run / "work"
    user = tmp_path / "user"
    roots = {"home": home, "run": run, "cwd": cwd, "user-home": user}
    text = f"a {cwd}/tmp/x b {run}/evidence c {home}/snapshots d {user}/.cfg e {tmp_path}/other"
    out = errtext.sanitize(text, roots)
    # the longest root wins where roots nest: the run directory under the home reads <run>
    want = "a <cwd>/tmp/x b <run>/evidence c <home>/snapshots d <user-home>/.cfg e "
    assert out == want + f"{tmp_path}/other"
    # a prefix only matches at a boundary
    assert errtext.sanitize(f"{home}x/y {home}", roots) == f"{home}x/y <home>"
    # the bound is bytes of UTF-8, cut at a character boundary
    long = "é" * 400  # 800 bytes
    bounded = errtext.sanitize(long, roots)
    assert len(bounded.encode("utf-8")) <= errtext.MESSAGE_MAX
    assert bounded == "é" * (errtext.MESSAGE_MAX // 2)
    odd = errtext.sanitize("a" + "é" * 400, roots)
    assert len(odd.encode("utf-8")) <= errtext.MESSAGE_MAX and odd.startswith("aé")
    odd.encode("utf-8").decode("utf-8")  # whole characters only
    assert errtext.sanitize("short", roots) == "short"
    # a bare-slash root never rewrites every slash
    assert errtext.sanitize("/a/b", {"home": Path("/")}) == "/a/b"
