"""Pytest pack — programmatic test runs with structured results."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PytestSpec:
    path: str = "."
    markers: str | None = None
    keyword: str | None = None
    junit_xml: str | None = None


@dataclass
class PytestResult:
    exit_code: int
    collected: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    report_path: str | None = None
    failures: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "collected": self.collected,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
            "report_path": self.report_path,
            "failures": self.failures,
        }


class _ResultsCollector:
    def __init__(self) -> None:
        self.exitcode = 0
        self.collected = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors = 0
        self.failures: list[dict[str, str]] = []

    def pytest_collection_modifyitems(self, items: list[Any]) -> None:
        self.collected = len(items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when != "call":
            return
        if report.passed:
            self.passed += 1
        elif report.failed:
            self.failed += 1
            self.failures.append(
                {
                    "nodeid": str(report.nodeid),
                    "longrepr": str(report.longrepr)[:2000],
                }
            )
        elif report.skipped:
            self.skipped += 1

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        self.exitcode = int(exitstatus)


def run_pytest(spec: PytestSpec, *, report_dir: Path) -> PytestResult:
    import pytest

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "pytest-report.json"

    args = [spec.path, "-q", "--tb=short", "-p", "no:cacheprovider"]
    if spec.markers:
        args.extend(["-m", spec.markers])
    if spec.keyword:
        args.extend(["-k", spec.keyword])
    if spec.junit_xml:
        args.extend(["--junitxml", spec.junit_xml])

    collector = _ResultsCollector()
    exit_code = int(pytest.main(args, plugins=[collector]))

    payload = {
        "exit_code": exit_code,
        "collected": collector.collected,
        "passed": collector.passed,
        "failed": collector.failed,
        "skipped": collector.skipped,
        "failures": collector.failures,
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return PytestResult(
        exit_code=exit_code,
        collected=collector.collected,
        passed=collector.passed,
        failed=collector.failed,
        skipped=collector.skipped,
        errors=collector.errors,
        report_path=str(report_path),
        failures=collector.failures,
    )
