"""Self-tests for the per-result records and derived ledger renderer
(L.P0-0a.3). Each test builds an isolated results/meta_config/gates.d set
under `tmp_path` and calls `ledger.render()` directly (never spawning a
subprocess pytest run), so this file stays fast."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.proof import ledger as ledger_mod
from tests.proof import results as results_mod

ROOT = Path(__file__).resolve().parents[3]


def _meta_config(tmp_path: Path, gates: list[str]) -> Path:
    path = tmp_path / "meta_config.toml"
    names = ", ".join(f'"{g}"' for g in gates)
    path.write_text(f"[gates]\nnames = [{names}]\n")
    return path


def _gates_dir(tmp_path: Path, fragments: dict[str, list[str]] | None = None) -> Path:
    gdir = tmp_path / "gates.d"
    gdir.mkdir(exist_ok=True)
    for name, gate_names in (fragments or {}).items():
        names = ", ".join(f'"{g}"' for g in gate_names)
        (gdir / f"{name}.toml").write_text(f"[gates]\nnames = [{names}]\n")
    return gdir


def _record(
    results_dir: Path,
    label: str,
    outcome: str,
    gate: str | None,
    venue: str,
    interpreter: str = "3.12.9",
) -> None:
    results_mod.write_record(
        results_mod.Record(
            nodeid=f"tests/planted.py::test_{label}_{outcome}_{gate}_{venue}",
            outcome=outcome,
            gate=gate,
            venue=venue,
            interpreter=interpreter,
            labels=[label],
        ),
        results_dir=results_dir,
    )


@pytest.mark.proves("WR-PROOF-7", "WR-PROOF-7:3.14-only-not-proven", "core", "core", "LOGIC", "CI")
def test_named_gate_312_pass_renders_proven_ci(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-A", "passed", "ci-test", "CI")
    report = ledger_mod.render(
        results_dir=results_dir,
        meta_config_path=_meta_config(tmp_path, ["ci-test"]),
        gates_dir=_gates_dir(tmp_path),
    )
    assert report["CLAUSE-A"]["status"] == ledger_mod.PROVEN


@pytest.mark.parametrize("outcome", ["skipped", "xfailed", "error", "unrun"])
def test_planted_skip_xfail_error_unrun_render_unproven(tmp_path: Path, outcome: str) -> None:
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-B", outcome, "ci-test", "CI")
    report = ledger_mod.render(
        results_dir=results_dir,
        meta_config_path=_meta_config(tmp_path, ["ci-test"]),
        gates_dir=_gates_dir(tmp_path),
    )
    assert report["CLAUSE-B"]["status"] == ledger_mod.UNPROVEN


def test_clause_unproven_while_any_registered_node_xfails(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-C", "passed", "ci-test", "CI")
    _record(results_dir, "CLAUSE-C", "xfailed", "ci-test", "CI")
    report = ledger_mod.render(
        results_dir=results_dir,
        meta_config_path=_meta_config(tmp_path, ["ci-test"]),
        gates_dir=_gates_dir(tmp_path),
    )
    assert report["CLAUSE-C"]["status"] == ledger_mod.UNPROVEN


@pytest.mark.proves("WR-PROOF-7", "WR-PROOF-7:3.14-only-not-proven", "core", "core", "LOGIC", "CI")
def test_planted_314_only_pass_is_corroborating_not_proven(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-D", "passed", "ci-test", "CI", interpreter="3.14.7")
    report = ledger_mod.render(
        results_dir=results_dir,
        meta_config_path=_meta_config(tmp_path, ["ci-test"]),
        gates_dir=_gates_dir(tmp_path),
    )
    assert report["CLAUSE-D"]["status"] == ledger_mod.UNPROVEN
    assert report["CLAUSE-D"]["corroborating_314"] is True


@pytest.mark.proves("WR-PROOF-7", "WR-PROOF-7:3.14-only-not-proven", "core", "core", "LOGIC", "CI")
def test_local_venue_never_counts(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-E", "passed", None, results_mod.LOCAL_VENUE)
    with pytest.raises(ledger_mod.VacuousLedgerError):
        ledger_mod.render(
            results_dir=results_dir,
            meta_config_path=_meta_config(tmp_path, []),
            gates_dir=_gates_dir(tmp_path),
        )


def test_vacuity_zero_results_is_error(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    with pytest.raises(ledger_mod.VacuousLedgerError):
        ledger_mod.render(
            results_dir=results_dir,
            meta_config_path=_meta_config(tmp_path, ["ci-test"]),
            gates_dir=_gates_dir(tmp_path),
        )


def test_gate_declared_in_gates_d_fragment_counts(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-F", "passed", "ci-fragment-only", "CI")
    report = ledger_mod.render(
        results_dir=results_dir,
        meta_config_path=_meta_config(tmp_path, []),  # not declared here
        gates_dir=_gates_dir(tmp_path, {"p0": ["ci-fragment-only"]}),
    )
    assert report["CLAUSE-F"]["status"] == ledger_mod.PROVEN


def test_undeclared_gate_results_ignored(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-G", "passed", "not-a-declared-gate", "CI")
    with pytest.raises(ledger_mod.VacuousLedgerError):
        ledger_mod.render(
            results_dir=results_dir,
            meta_config_path=_meta_config(tmp_path, ["ci-test"]),
            gates_dir=_gates_dir(tmp_path),
        )


def test_report_json_matches_schema(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")

    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-H", "passed", "ci-test", "CI")
    report = ledger_mod.render(
        results_dir=results_dir,
        meta_config_path=_meta_config(tmp_path, ["ci-test"]),
        gates_dir=_gates_dir(tmp_path),
    )
    schema = json.loads((ROOT / "tests" / "proof" / "ledger.schema.json").read_text())
    jsonschema.validate(report, schema)


def test_report_json_matches_schema_shape_without_jsonschema_dep(tmp_path: Path) -> None:
    """Belt-and-suspenders: assert the schema's required shape by hand, in
    case the optional `jsonschema` package is not installed on a given
    host."""
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-I", "passed", "ci-test", "CI")
    report = ledger_mod.render(
        results_dir=results_dir,
        meta_config_path=_meta_config(tmp_path, ["ci-test"]),
        gates_dir=_gates_dir(tmp_path),
    )
    for entry in report.values():
        assert set(entry) == {"status", "corroborating_314", "n_results"}
        assert entry["status"] in ("PROVEN", "UNPROVEN")
        assert isinstance(entry["corroborating_314"], bool)
        assert isinstance(entry["n_results"], int) and entry["n_results"] >= 1


def test_enforce_scope_ci_fails_on_an_unproven_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L.CZ.1: `meta enforce` is no longer a report-mode command; an unproven clause fails it
    (the scope-by-scope cases are `test_enforce.py`'s)."""
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-J", "xfailed", "ci-test", "CI")
    monkeypatch.setattr(results_mod, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(results_mod, "META_CONFIG_PATH", _meta_config(tmp_path, ["ci-test"]))
    monkeypatch.setattr(results_mod, "GATES_DIR", _gates_dir(tmp_path))
    # `--scope ci` resolves its anchor through `fence.ckpt_succeeded`; once the newest carrier on
    # HEAD is a check-run checkpoint (J0, J-ROOT) that is a live `gh` read, which a test job has
    # no token for. The read is not under test here: the newest check-run carrier is unmarked.
    from tests.proof import fence as fence_mod

    monkeypatch.setattr(fence_mod, "_default_check_run_reader", lambda *_a: "missing")

    import tests.proof.meta as meta_mod

    assert meta_mod.main(["enforce", "--scope", "ci"]) == 1


def test_enforce_print_mode_prints_enforce() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "tests.proof.meta", "enforce", "--print-mode"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "enforce"


def _review(reviews_dir: Path, outcome: str = "pass") -> None:
    reviews_dir.mkdir(exist_ok=True)
    verdict = "pass" if outcome == "pass" else "fail"
    (reviews_dir / "RV-5-j0.toml").write_text(
        f'id = "RV-5"\nmode = "stage-critic review"\noutcome = "{outcome}"\n'
        f'sha = "3e82d1f"\ntranscribe_log = "ok"\n\n[criteria]\n'
        f'verbatim_fragments = "pass"\nrow_set = "{verdict}"\nowner = "pass"\nk_docs = "pass"\n'
    )


def _render_with_reviews(tmp_path: Path, reviews_dir: Path) -> dict:
    results_dir = tmp_path / "results"
    _record(results_dir, "CLAUSE-R", "passed", "ci-test", "CI")
    return ledger_mod.render(
        results_dir=results_dir,
        meta_config_path=_meta_config(tmp_path, ["ci-test"]),
        gates_dir=_gates_dir(tmp_path),
        reviews_dir=reviews_dir,
    )


def test_valid_passing_review_renders_review_key(tmp_path: Path) -> None:
    reviews_dir = tmp_path / "reviews"
    _review(reviews_dir)
    report = _render_with_reviews(tmp_path, reviews_dir)
    assert report["review:RV-5"]["status"] == ledger_mod.PROVEN


def test_failing_or_absent_or_malformed_review_renders_nothing(tmp_path: Path) -> None:
    reviews_dir = tmp_path / "reviews"
    assert "review:RV-5" not in _render_with_reviews(tmp_path, reviews_dir)
    _review(reviews_dir, outcome="fail")
    assert "review:RV-5" not in _render_with_reviews(tmp_path, reviews_dir)
    (reviews_dir / "RV-5-j0.toml").write_text('id = "RV-5"\noutcome = "pass"\n')
    assert "review:RV-5" not in _render_with_reviews(tmp_path, reviews_dir)
