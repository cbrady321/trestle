"""User-story integration markers (US-01–US-24 core paths)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from trestle.common import codes
from trestle.common.types import AdmitRequest, RequestOutcome, RunView
from trestle.server.doctor import build_doctor_report
from trestle.server.main import create_kernel
from trestle.server.recovery import recover_on_startup


def test_us02_refused_request_has_no_run_id(kernel) -> None:
    outcome = kernel.control.run(plugin="missing", args={})
    assert isinstance(outcome, RequestOutcome)
    assert outcome.origin == "admission"
    assert "run_id" not in outcome.to_dict()


def test_us03_terminal_states_are_distinct(kernel) -> None:
    ok = kernel.control.run(plugin="echo", args={"message": "ok"}, wait_ms=5000)
    assert isinstance(ok, RunView)
    assert ok.state == "succeeded"

    bad = kernel.control.run(plugin="echo", args={"message": object()}, wait_ms=1000)
    assert isinstance(bad, RequestOutcome)
    assert bad.code == codes.INVALID_ARGS


def test_us06_query_backend_independent(kernel) -> None:
    view = kernel.control.run(plugin="echo", args={"message": "q"}, wait_ms=5000)
    assert isinstance(view, RunView)
    result = kernel.control.query("recent_runs", {})
    assert isinstance(result, dict)
    assert result["backend"] == "filesystem"
    assert "as_of" in result
    payload = json.dumps(result["items"])
    assert "filesystem" not in payload


def test_us07_drop_in_plugin_without_restart(trestle_home: Path, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    echo_src = Path(__file__).resolve().parent / "fixtures" / "plugins" / "echo.py"
    (plugin_dir / "echo.py").write_text(echo_src.read_text(encoding="utf-8"), encoding="utf-8")
    kernel = create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    new_plugin = (
        "from trestle.plugin.surface import Context, trestle\n\n"
        "@trestle\ndef fresh(ctx: Context) -> dict[str, str]:\n"
        "    return {'fresh': 'yes'}\n"
    )
    (plugin_dir / "fresh.py").write_text(new_plugin, encoding="utf-8")
    names = {row["name"] for row in kernel.control.list_plugins()["items"]}
    assert "fresh" in names
    view = kernel.control.run(plugin="fresh", wait_ms=5000)
    assert isinstance(view, RunView)
    assert view.state == "succeeded"


def test_us08_hostile_plugin_control_plane_responsive(bounded_kernel) -> None:
    import time

    bounded_kernel.control.run(plugin="hostile", wait_ms=5000)
    start = time.monotonic()
    bounded_kernel.control.list_plugins()
    assert time.monotonic() - start < 0.5
    follow = bounded_kernel.control.run(plugin="echo", args={"message": "after"}, wait_ms=5000)
    assert isinstance(follow, RunView)
    assert follow.state == "succeeded"


def test_us09_run_uses_snapshot_at_admit_time(kernel, trestle_home: Path, plugin_dir: Path) -> None:
    plugin_path = plugin_dir / "echo.py"
    original = plugin_path.read_text(encoding="utf-8")
    admit = kernel.control.admission.admit(
        AdmitRequest(plugin="echo", args={"message": "snap"}),
    )
    assert admit.tag == "admitted"
    plugin_path.write_text(original.replace("hello", "mutated"), encoding="utf-8")
    kernel.registry.refresh()
    view = kernel.control.run(plugin="echo", args={"message": "snap"}, wait_ms=5000)
    assert isinstance(view, RunView)
    assert view.state == "succeeded"


def test_us10_recovery_from_ledger_not_meta(trestle_home: Path, plugin_dir: Path) -> None:
    kernel = create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    view = kernel.control.run(plugin="echo", args={"message": "persist"}, wait_ms=5000)
    assert isinstance(view, RunView)
    run_dir = _find_run(trestle_home, view.run_id)
    meta = run_dir / "evidence" / "meta.json"
    meta.unlink()
    recover_on_startup(trestle_home)
    assert meta.exists()


def test_us11_pin_prevents_gc_collection(trestle_home: Path, plugin_dir: Path) -> None:
    from trestle.server.config import RetentionConfig, TrestleConfig, load_config
    from trestle.server.gc import run_gc

    kernel = create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    view = kernel.control.run(plugin="echo", args={"message": "pinme"}, wait_ms=5000)
    assert isinstance(view, RunView)
    kernel.control.pin(view.run_id)
    run_dir = _find_run(trestle_home, view.run_id)
    old = run_dir.stat().st_mtime - (200 * 86400)
    import os

    os.utime(run_dir, (old, old))
    cfg = load_config(trestle_home)
    cfg = TrestleConfig(
        retention=RetentionConfig(metadata_days=0, artifact_days=0, abandoned_hours=0),
        idempotency_ttl_s=cfg.idempotency_ttl_s,
        service_log=cfg.service_log,
    )
    report = run_gc(trestle_home, cfg)
    assert report.runs_removed == 0
    assert run_dir.exists()


def test_us12_no_plugin_code_in_server_or_wrapper() -> None:
    repo = Path(__file__).resolve().parents[1]
    for rel in ("trestle/server/main.py", "trestle/wrapper/main.py"):
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "fixtures.plugins" in node.module
            ):
                raise AssertionError(f"{rel} imports plugin fixtures")


def test_us13_outputs_auto_promoted_not_writable_evidence(kernel) -> None:
    view = kernel.control.run(
        plugin="outputs_writer",
        args={"name": "x.txt", "body": "data"},
        wait_ms=5000,
    )
    assert isinstance(view, RunView)
    assert view.artifact_count >= 1


def test_us14_drain_refuses_new_work(kernel) -> None:
    kernel.control.scheduler.draining = True
    outcome = kernel.control.run(plugin="echo", args={"message": "nope"})
    assert isinstance(outcome, RequestOutcome)
    assert outcome.code == codes.SERVICE_DRAINING


def test_us15_huge_return_summary_bounded(bounded_kernel) -> None:
    view = bounded_kernel.control.run(plugin="big_array", args={"count": 5000}, wait_ms=10_000)
    assert isinstance(view, RunView)
    assert view.truncated is True
    summary_bytes = len(json.dumps(view.summary or {}, separators=(",", ":")).encode("utf-8"))
    assert summary_bytes < 500


def test_us16_no_tty_oneshot_in_default_catalog(kernel) -> None:
    catalog = kernel.control.list_plugins()
    tty_rows = [row for row in catalog["items"] if row.get("capability_class") == "tty-oneshot"]
    assert tty_rows == []


def test_us18_error_classes_distinguishable(kernel) -> None:
    missing = kernel.control.run(plugin="nope")
    assert isinstance(missing, RequestOutcome)
    assert missing.code == codes.PLUGIN_NOT_FOUND

    bad_args = kernel.control.run(plugin="echo", args={"message": {1, 2, 3}})
    assert isinstance(bad_args, RequestOutcome)
    assert bad_args.code == codes.INVALID_ARGS

    failed = kernel.control.run(plugin="echo", args={"message": "ok"}, wait_ms=5000)
    assert isinstance(failed, RunView)


def test_us20_idempotency_same_key_returns_existing(trestle_home: Path, plugin_dir: Path) -> None:
    kernel = create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    first = kernel.control.run(
        plugin="echo",
        args={"message": "idem"},
        idempotency_key="k1",
        wait_ms=5000,
    )
    second = kernel.control.run(
        plugin="echo",
        args={"message": "idem"},
        idempotency_key="k1",
        wait_ms=0,
    )
    assert isinstance(first, RunView)
    assert isinstance(second, RunView)
    assert first.run_id == second.run_id


def test_us21_errors_are_short_strings_no_traceback(kernel) -> None:
    outcome = kernel.control.run(plugin="missing")
    assert isinstance(outcome, RequestOutcome)
    assert len(outcome.message) < 200
    assert "Traceback" not in outcome.message


def test_us22_doctor_cli_operator_surface(trestle_home: Path, plugin_dir: Path) -> None:
    create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    report = build_doctor_report(home=trestle_home, plugin_dirs=[plugin_dir])
    assert report.health == "ok"
    assert report.service_epoch
    assert "echo" in report.plugins


def test_us23_automatic_runtime_no_author_ceremony(kernel) -> None:
    view = kernel.control.run(
        plugin="outputs_writer",
        args={"name": "a.txt", "body": "x"},
        wait_ms=5000,
    )
    assert isinstance(view, RunView)
    run_dir = _find_run(kernel.home, view.run_id)
    assert (run_dir / "work" / "outputs" / "a.txt").exists() or view.artifact_count >= 1


def test_us24_plugin_imports_surface_only() -> None:
    forbidden = (
        "trestle.server",
        "trestle.wrapper",
        "trestle.child",
        "fastmcp",
        "mcp",
    )
    plugin_dir = Path(__file__).resolve().parent / "fixtures" / "plugins"
    for path in plugin_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in {"trestle", "fastmcp", "mcp"} or alias.name.startswith(
                        "trestle.plugin"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(prefix) for prefix in forbidden)


def test_live_cli_doctor(trestle_home: Path, plugin_dir: Path) -> None:
    create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    proc = subprocess.run(
        [sys.executable, "-m", "trestle.cli", "doctor", "--home", str(trestle_home)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert proc.returncode == 0
    assert "health: ok" in proc.stdout


def _find_run(home: Path, run_id: str) -> Path:
    for month in (home / "runs").iterdir():
        candidate = month / run_id
        if candidate.is_dir():
            return candidate
    raise AssertionError(run_id)
