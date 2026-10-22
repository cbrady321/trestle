"""Credential tests: a stub keychain in place of `/usr/bin/security`, for every test here.

No test reaches the real macOS Keychain (decision record D-9 and change 3): `SECURITY_BIN` is
pointed at a small script that keeps its items in a JSON file under the test's own directory and
answers the two commands the keychain provider runs, with `security`'s exit codes.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from trestle import cli
from trestle.server import credential_store

STUB = """\
#!{python}
import json, sys
from pathlib import Path

items = Path({items!r})
data = json.loads(items.read_text()) if items.exists() else {{}}
args = sys.argv[1:]
command, rest = args[0], args[1:]
opts = {{}}
i = 0
while i < len(rest):
    if rest[i] in ("-s", "-a", "-l", "-w") and i + 1 < len(rest):
        opts[rest[i]] = rest[i + 1]
        i += 2
    else:
        opts[rest[i]] = True
        i += 1
key = opts.get("-s", "") + "|" + opts.get("-a", "")
with open({log!r}, "a") as fh:
    fh.write(command + "\\n")
if command == "find-generic-password":
    if key not in data:
        print("security: SecKeychainSearchCopyNext: The specified item could not be found.",
              file=sys.stderr)
        sys.exit(44)
    print(data[key])
    sys.exit(0)
if command == "add-generic-password":
    if key in data and "-U" not in opts:
        print("security: The specified item already exists in the keychain.", file=sys.stderr)
        sys.exit(45)
    data[key] = opts["-w"]
    items.write_text(json.dumps(data))
    sys.exit(0)
sys.exit(1)
"""


class StubKeychain:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.items = root / "items.json"
        self.log = root / "calls.log"
        self.bin = root / "security"
        self.bin.write_text(
            STUB.format(python=sys.executable, items=str(self.item_path), log=str(self.log))
        )
        self.bin.chmod(0o755)

    @property
    def item_path(self) -> Path:
        return self.items

    def entries(self) -> dict[str, str]:
        return json.loads(self.items.read_text()) if self.items.exists() else {}

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture(autouse=True)
def stub_keychain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StubKeychain:
    stub = StubKeychain(tmp_path / "stub-keychain")
    monkeypatch.setattr(credential_store, "SECURITY_BIN", str(stub.bin))
    return stub


class _Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


CliResult = tuple[int, str, str]


@pytest.fixture
def credentials_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Callable[..., CliResult]:
    """`trestle credentials ...` in process: (exit code, stdout, stderr), `stdin` piped."""

    def invoke(home: Path, *argv: str, stdin: str = "") -> CliResult:
        monkeypatch.setattr(sys, "stdin", _Pipe(stdin))
        capsys.readouterr()
        try:
            code = cli.main(["credentials", *argv, "--home", str(home)])
        except SystemExit as exc:
            code = int(exc.code or 0)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke
