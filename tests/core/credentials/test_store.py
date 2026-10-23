"""Change 3: the `encrypted_file` store, its key providers and `credentials.toml`.

The store is one 0600 file in a 0700 directory, encrypted so its bytes never hold a value; the key
comes from the (stub) keychain or a 0600 key file; the tables round-trip through the CLI's writer
and reject what change 3 does not implement.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from tests.core.credentials.conftest import StubKeychain
from trestle.server import credential_store as store_mod
from trestle.server.credential_store import (
    EncryptedStore,
    FileKeyProvider,
    KeychainKeyProvider,
    StoreError,
)
from trestle.server.credentials import (
    GRANT_CURRENT,
    GRANT_MISSING,
    GRANT_STALE,
    GRANT_UNPINNED,
    CredentialsConfig,
    Grant,
    Identity,
    grant_status,
    load_credentials,
    parse_credentials,
    save_credentials,
)

VALUE = "FAKE-demo-value-0f9e8d7c"  # a stand-in, never a real credential (D-9)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_keychain_round_trip_permissions_and_ciphertext(
    tmp_path: Path, stub_keychain: StubKeychain
) -> None:
    home = tmp_path / "home"
    store = EncryptedStore(home, KeychainKeyProvider(home))
    assert store.read_all() == {}  # nothing stored, no key asked for
    assert stub_keychain.calls() == []

    store.put("demo", VALUE)
    store.put("other", "FAKE-second")
    assert _mode(store.path) == 0o600
    assert _mode(store.path.parent) == 0o700
    assert VALUE.encode() not in store.path.read_bytes()
    # the key went to the keychain under the service and the home's account, not into the home
    (item,) = stub_keychain.entries()
    assert item == f"{store_mod.KEYCHAIN_SERVICE}|{home.resolve()}"
    assert not (home / "credentials" / store_mod.KEY_FILE).exists()
    assert stub_keychain.calls().count("add-generic-password") == 1

    fresh = EncryptedStore(home, KeychainKeyProvider(home))
    assert fresh.read_all() == {"demo": VALUE, "other": "FAKE-second"}
    assert fresh.delete("other") is True
    assert fresh.delete("other") is False
    assert fresh.get("demo") == VALUE
    assert stub_keychain.calls().count("add-generic-password") == 1  # the key is made once


def test_file_key_provider_round_trip(tmp_path: Path, stub_keychain: StubKeychain) -> None:
    home = tmp_path / "home"
    store = EncryptedStore(home, FileKeyProvider(home))
    store.put("demo", VALUE)
    key = home / "credentials" / store_mod.KEY_FILE
    assert _mode(key) == 0o600 and _mode(store.path) == 0o600
    assert EncryptedStore(home, FileKeyProvider(home)).get("demo") == VALUE
    assert stub_keychain.calls() == []  # the file provider never runs `security`


def test_wrong_or_missing_key_is_a_store_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    EncryptedStore(home, FileKeyProvider(home)).put("demo", VALUE)
    key = home / "credentials" / store_mod.KEY_FILE
    key.unlink()
    with pytest.raises(StoreError, match="key is missing"):
        EncryptedStore(home, FileKeyProvider(home)).read_all()
    FileKeyProvider(home).create()  # a different key
    with pytest.raises(StoreError, match="does not decrypt") as excinfo:
        EncryptedStore(home, FileKeyProvider(home)).read_all()
    assert VALUE not in str(excinfo.value)


def test_keychain_failure_is_a_store_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_mod, "SECURITY_BIN", str(tmp_path / "no-such-security"))
    home = tmp_path / "home"
    with pytest.raises(StoreError, match="keychain could not be reached"):
        EncryptedStore(home, KeychainKeyProvider(home)).put("demo", VALUE)


def test_tables_round_trip_through_the_writer(tmp_path: Path) -> None:
    config = CredentialsConfig(
        identities={
            "ci-reader": Identity(
                name="ci-reader",
                kind="encrypted_file",
                env="CI_TOKEN",
                grants=(
                    Grant("repo_tests", "a" * 64, {"trestle_packs.grant": "b" * 64}),
                    Grant("release"),
                ),
            )
        },
        key_provider="file",
    )
    home = tmp_path / "home"
    home.mkdir()
    save_credentials(home, config)
    assert _mode(home / "credentials.toml") == 0o600
    loaded = load_credentials(home)
    assert loaded.key_provider == "file"
    ident = loaded.identities["ci-reader"]
    assert ident.env == "CI_TOKEN"
    assert {g.plugin: g for g in ident.grants} == {
        g.plugin: g for g in config.identities["ci-reader"].grants
    }


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ('kind = "aws_assume_role"\nenv = "X"', "not implemented yet"),
        ('kind = "github_app"\nenv = "X"', "not implemented yet"),
        ('kind = "magic"\nenv = "X"', "unknown kind"),
        ('kind = "encrypted_file"\nenv = "lower"', "env must be a variable name"),
        ('kind = "encrypted_file"\nenv = "X"\nvalue = "nope"', "unknown keys"),
    ],
)
def test_bad_tables_are_refused_at_load(table: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_credentials(f"[credentials.demo]\n{table}\n")


def test_grant_status_pins_source_and_packages() -> None:
    pin = Grant("probe", "s1", {"pkg": "p1"})
    assert grant_status(pin, ("s1", {"pkg": "p1"})) == GRANT_CURRENT
    assert grant_status(pin, ("s2", {"pkg": "p1"})) == GRANT_STALE  # entry file changed
    assert grant_status(pin, ("s1", {"pkg": "p2"})) == GRANT_STALE  # a declared package changed
    assert grant_status(pin, ("s1", {})) == GRANT_STALE
    assert grant_status(pin, None) == GRANT_MISSING
    assert grant_status(Grant("probe"), ("s1", {})) == GRANT_UNPINNED
