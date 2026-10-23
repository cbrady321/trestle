"""Change 2d (owner decision 3): a message built at run time that names an operator command quotes
it in the one fixed form, `the operator runs: <command>`, with the command whole after the
200-character cut. No real credentials: the store is a file-keyed one under tmp_path, or a stub
that fails the way an unreadable store does."""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.types import RequestOutcome
from trestle.server import credentials as creds
from trestle.server.credential_store import StoreError
from trestle.server.plugin_paths import CATALOG_HINT_EMPTY, CATALOG_HINT_PACKS_MISSING

NAME = "n" * 64
PLUGIN = "p" * 64
OPERATOR = "the operator runs: "


def _quotes(message: str, command: str) -> None:
    assert len(message) <= 200, len(message)
    clause = f"{OPERATOR}{command}"
    assert clause in message, message
    # the command is whole: what follows it is the clause's end or the next sentence part
    rest = message[message.index(clause) + len(clause) :]
    assert rest == "" or rest.startswith(";"), message


def test_credential_unknown() -> None:
    out = creds.refusal(creds.CredentialsConfig(), PLUGIN, [NAME], "sha", {})
    assert isinstance(out, RequestOutcome) and out.code == codes.ADMISSION_CREDENTIAL_UNKNOWN
    _quotes(out.message, f"trestle credentials add {NAME} --env VAR")


def test_credential_not_granted() -> None:
    config = creds.CredentialsConfig(identities={NAME: creds.Identity(NAME, "encrypted_file", "V")})
    out = creds.refusal(config, PLUGIN, [NAME], "sha", {})
    assert isinstance(out, RequestOutcome) and out.code == codes.ADMISSION_CREDENTIAL_NOT_GRANTED
    _quotes(out.message, f"trestle credentials grant {NAME} {PLUGIN}")


def test_credential_unavailable_no_value(tmp_path: Path) -> None:
    config = creds.CredentialsConfig(
        identities={NAME: creds.Identity(NAME, "encrypted_file", "V")}, key_provider="file"
    )
    out = creds.resolve(config, tmp_path, [NAME])
    assert isinstance(out, RequestOutcome) and out.code == codes.ADMISSION_CREDENTIAL_UNAVAILABLE
    _quotes(out.message, f"trestle credentials add {NAME} --replace")


def test_credential_unavailable_store_unreadable_shows_no_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = f"key file {tmp_path / ('deep' * 40)} unreadable (keychain item trestle/store)"

    class Broken:
        def read_all(self) -> dict[str, str]:
            raise StoreError(detail)

    config = creds.CredentialsConfig(
        identities={NAME: creds.Identity(NAME, "encrypted_file", "V")}, key_provider="file"
    )
    monkeypatch.setattr(creds.CredentialsConfig, "store", lambda self, home: Broken())
    out = creds.resolve(config, tmp_path, [NAME])
    assert isinstance(out, RequestOutcome) and out.code == codes.ADMISSION_CREDENTIAL_UNAVAILABLE
    _quotes(out.message, "trestle doctor")
    assert str(tmp_path) not in out.message and "keychain" not in out.message.lower()


def test_packs_hint_quotes_the_install() -> None:
    assert len(CATALOG_HINT_PACKS_MISSING) <= 200
    _quotes(CATALOG_HINT_PACKS_MISSING, '~/.trestle-venv/bin/pip install -e ".[packs]"')


def test_empty_catalog_hint_leaves_the_folder_to_the_operator() -> None:
    assert "the operator adds plugin files" in CATALOG_HINT_EMPTY
    assert "publish_plugin" in CATALOG_HINT_EMPTY
    assert "Write a" not in CATALOG_HINT_EMPTY
