"""Find the project's own Python interpreter, never the service's.

The migrate pack's own copy of the pytest pack's `interpreter`: a bundled library stands alone
(it is installed with `--no-deps`).

`pytest_run`, `migrate_apply` and `stack_pipeline` run their tool as `<python> -m <tool>` in a
child process, so the project's dependencies come from the project's environment and the Trestle
service venv needs none of them.
"""

from __future__ import annotations

import os
from pathlib import Path

from trestle.plugin import Blocked

# Looked for in the project root, then in each parent up to the repository root (`.git`).
VENV_PYTHONS = (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe")


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_project_python(root: Path, python: str | None = None) -> str:
    """The interpreter to run the project's tools with, as an absolute path.

    `python`, when given, is used as is (relative to `root`). Otherwise the first virtualenv in
    `VENV_PYTHONS` found in `root` or a parent up to the repository root. None found raises
    `Blocked`: falling back to the service's own interpreter would run the project's tests
    without its dependencies."""
    if python:
        named = Path(python) if Path(python).is_absolute() else root / python
        if _runnable(named):
            return str(named)
        raise Blocked(
            "project_python_missing",
            f"python={python!r} is not an executable at {named}. Pass the absolute path of the "
            "project's interpreter (for example <project>/.venv/bin/python), then resend.",
            resend="will_not_succeed",
        )
    here = root.resolve()
    for folder in (here, *here.parents):
        for relative in VENV_PYTHONS:
            candidate = folder / relative
            if _runnable(candidate):
                return str(candidate)
        if (folder / ".git").exists():
            break
    raise Blocked(
        "project_python_not_found",
        f"No virtualenv found in {here} or its parents up to the repository root "
        f"(looked for {', '.join(VENV_PYTHONS)}). Create one with the project's dependencies "
        "(`python -m venv .venv && .venv/bin/pip install -e .` plus its test tools), or pass "
        "python=<absolute path to the project's interpreter>; then resend.",
    )
