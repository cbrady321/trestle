"""L.CK-14.1: the zero-error mypy gate (K-14). The gate is the `typecheck` CI job's `mypy`
command; it must pass on HEAD, fail on a planted error, install the packs before it runs, and
`_packs_hint` keeps both of its messages."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from trestle.server import init_cmd

ROOT = Path(__file__).resolve().parents[3]
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
PACKS_SRC = ROOT / "packages" / "trestle-packs"
INSTALL_DOC = ROOT / "docs" / "install.md"


def _typecheck_job(workflow: dict[str, Any]) -> dict[str, Any]:
    job: dict[str, Any] = workflow["jobs"]["typecheck"]
    return job


def gate_command(workflow: dict[str, Any]) -> list[str]:
    """The `typecheck` job's `mypy` step, as an argv for this interpreter."""
    runs = [str(step["run"]).split() for step in _typecheck_job(workflow)["steps"] if "run" in step]
    (cmd,) = [r for r in runs if r[0] == "mypy"]
    return [sys.executable, "-m", *cmd]


def installs_packs_before_mypy(job: dict[str, Any]) -> bool:
    """The job installs `.[dev,packs]` in a step before its `mypy` step (PC2-8)."""
    runs = [str(step.get("run", "")) for step in job["steps"]]
    mypy_at = [i for i, r in enumerate(runs) if r.split()[:1] == ["mypy"]]
    installs = [
        i for i, r in enumerate(runs) if "pip install" in r and ".[dev,packs]" in r.replace(" ", "")
    ]
    return bool(mypy_at) and bool(installs) and min(installs) < min(mypy_at)


def _gate(cwd: Path, cache: Path) -> subprocess.CompletedProcess[str]:
    """The gate command in `cwd`, `trestle_packs` resolving to this checkout's package (what
    `pip install -e ".[dev,packs]"` gives CI) and a private cache."""
    env = dict(
        os.environ,
        MYPY_CACHE_DIR=str(cache),
        PYTHONPATH=os.pathsep.join(
            [str(PACKS_SRC)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
        ),
    )
    workflow = yaml.safe_load(CI_YML.read_text())
    return subprocess.run(gate_command(workflow), cwd=cwd, env=env, capture_output=True, text=True)


@pytest.mark.proves("WR-PROOF-8", "WR-PROOF-8:mypy-zero-3.12", "core", "core", "PROC", "CI")
def test_gate_rejects_planted_error_and_passes_head(tmp_path: Path) -> None:
    assert (PACKS_SRC / "trestle_packs" / "py.typed").is_file()
    head = _gate(ROOT, tmp_path / "cache-head")
    assert head.returncode == 0, head.stdout + head.stderr

    copy = tmp_path / "copy"
    shutil.copytree(
        ROOT / "trestle", copy / "trestle", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy(ROOT / "pyproject.toml", copy / "pyproject.toml")
    with (copy / "trestle" / "server" / "init_cmd.py").open("a") as fh:
        fh.write('\n_PLANTED: int = "not an int"\n')
    planted = _gate(copy, tmp_path / "cache-copy")
    assert planted.returncode != 0
    assert "_PLANTED" in planted.stdout or "init_cmd.py" in planted.stdout


def test_typecheck_job_installs_packs() -> None:
    workflow = yaml.safe_load(CI_YML.read_text())
    assert installs_packs_before_mypy(_typecheck_job(workflow))

    only_dev = yaml.safe_load(CI_YML.read_text())
    for step in _typecheck_job(only_dev)["steps"]:
        if "pip install" in str(step.get("run", "")):
            step["run"] = 'pip install -e ".[dev]"'
    assert not installs_packs_before_mypy(_typecheck_job(only_dev))

    installs_after = yaml.safe_load(CI_YML.read_text())
    steps = _typecheck_job(installs_after)["steps"]
    steps.sort(key=lambda s: str(s.get("run", "")) == 'pip install -e ".[dev,packs]"')
    assert not installs_packs_before_mypy(_typecheck_job(installs_after))


def test_init_packs_hint_both_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    import trestle_packs  # noqa: F401  (present in the tests' environment)

    assert init_cmd._packs_hint() == (
        "workflow packs: trestle serve --plugin-dir examples/packs (see docs/packs.md)"
    )
    monkeypatch.setitem(sys.modules, "trestle_packs", None)  # `import trestle_packs` -> ImportError
    assert init_cmd._packs_hint() == (
        'workflow packs: pip install -e ".[packs]" (see docs/packs.md)'
    )


@pytest.mark.proves("WR-PROOF-10", "WR-PROOF-10:K-14", "core", "core", "INSPECT", "CI")
def test_k14_documented() -> None:
    text = INSTALL_DOC.read_text(encoding="utf-8")
    opening, closing = "<!-- K-" + "14 -->", "<!-- /K-" + "14 -->"
    assert text.count(opening) == 1 and text.count(closing) == 1
    block = text.split(opening)[1].split(closing)[0]
    assert "typecheck" in block and ".[dev,packs]" in block and "py.typed" in block
