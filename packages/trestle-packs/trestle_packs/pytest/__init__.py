"""The pytest pack's library: the mechanism behind `pytest_run` (P-8).

The wrapper builds the `<python> -m pytest` command line and runs it with `ctx.exec`; this
library finds the project's interpreter and reads the JUnit XML that pytest writes into a bounded
report. Counts come from the XML, never from console text. Everything an adapted wrapper may rely
on is in `__all__`.
"""

from trestle_packs.core.interpreter import VENV_PYTHONS, find_project_python
from trestle_packs.pytest.junit import (
    EXIT_MEANINGS,
    Failure,
    PytestReport,
    read_junit_report,
)

__all__ = [
    "EXIT_MEANINGS",
    "VENV_PYTHONS",
    "Failure",
    "PytestReport",
    "find_project_python",
    "read_junit_report",
]
