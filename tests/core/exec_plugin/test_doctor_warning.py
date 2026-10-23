"""Change 2c (owner decision 1): `exec` allowlisted under the restricted profile on a home with a
credential identity is warned about, never refused, by `trestle doctor` and by `trestle
credentials add` and `grant`. No real credentials: a fake value in a file-keyed store."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.core.credentials.conftest import CliResult
from trestle import cli
from trestle.server.doctor import build_doctor_report, exec_exposure_warning

FAKE = "FAKE-exec-warning-0000"
RESTRICTED_EXEC = '[profile]\nmode = "restricted"\nallowlist = ["exec", "echo"]\n'
RESTRICTED = '[profile]\nmode = "restricted"\nallowlist = ["echo"]\n'


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    return home


Cli = Callable[..., CliResult]


def _add(credentials_cli: Cli, home: Path) -> CliResult:
    return credentials_cli(
        home, "add", "demo-token", "--env", "DEMO_TOKEN", "--key-provider", "file", stdin=FAKE
    )


def _doctor_warns(home: Path) -> bool:
    report = build_doctor_report(home=home, plugin_dirs=[home / "plugins"])
    return any(w.startswith("exec is allowlisted") for w in report.warnings)


def test_doctor_and_add_warn(home: Path, credentials_cli: Cli) -> None:
    (home / "config.toml").write_text(RESTRICTED_EXEC, encoding="utf-8")
    code, out, err = _add(credentials_cli, home)
    assert code == 0, err
    warning = exec_exposure_warning(home)
    assert warning is not None
    assert warning.startswith(
        "exec is allowlisted under the restricted profile and this home holds 1 credential "
        "identity: any agent on this service can run commands as you"
    )
    assert f"warning: {warning}" in out.splitlines()
    assert FAKE not in out
    assert _doctor_warns(home)
    code, out, _ = _add_again(credentials_cli, home)
    assert f"warning: {warning}" in out.splitlines()


def _add_again(credentials_cli: Cli, home: Path) -> CliResult:
    return credentials_cli(home, "add", "demo-token", "--replace", stdin=FAKE + "-2")


def test_no_warning_without_identities(home: Path) -> None:
    (home / "config.toml").write_text(RESTRICTED_EXEC, encoding="utf-8")
    assert exec_exposure_warning(home) is None
    assert not _doctor_warns(home)


def test_no_warning_under_the_full_profile(home: Path, credentials_cli: Cli) -> None:
    code, out, err = _add(credentials_cli, home)
    assert code == 0, err
    assert exec_exposure_warning(home) is None
    assert "exec is allowlisted" not in out
    assert not _doctor_warns(home)


def test_no_warning_when_exec_is_not_listed(home: Path, credentials_cli: Cli) -> None:
    (home / "config.toml").write_text(RESTRICTED, encoding="utf-8")
    code, out, err = _add(credentials_cli, home)
    assert code == 0, err
    assert exec_exposure_warning(home) is None
    assert "exec is allowlisted" not in out
    assert not _doctor_warns(home)


def test_doctor_prints_it(
    home: Path, credentials_cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "config.toml").write_text(RESTRICTED_EXEC, encoding="utf-8")
    _add(credentials_cli, home)
    capsys.readouterr()
    assert cli.main(["doctor", "--home", str(home)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("warning: exec is allowlisted") for line in lines)
