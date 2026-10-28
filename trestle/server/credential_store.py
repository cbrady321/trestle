"""The `encrypted_file` credential store and the key providers behind it (decision record change 3).

Every stored value lives in one file, `$TRESTLE_HOME/credentials/store.enc` (mode 0600, in a 0700
directory), encrypted with Fernet (AES-128-CBC with an HMAC-SHA256 tag, from `cryptography`) and
replaced whole on every write. The key is not in that file. It comes from a key provider:

- `keychain` (the macOS default): a generic password in the user's login Keychain, service
  `trestle-credentials`, account the home's absolute path, read and created with
  `/usr/bin/security`. It is created by the first `trestle credentials add`.
- `file` (the default elsewhere): `$TRESTLE_HOME/credentials/store.key`, mode 0600. This is
  weaker: anything that can read the home as the user can read both the key and the store, so
  the encryption only keeps the values out of a copy of `store.enc` taken alone (a backup, a
  `grep` over the home). It is no protection against a process running as the user.

Under either provider a process running as the user can, in the end, ask for the key the same way
the service does; see the decision record's "honest limit". Values are read from the store at each
admission of a plugin that declares an identity, so a rotated value applies to the next run without
a restart.

Tests never reach the real Keychain: they point `SECURITY_BIN` at a stub.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from trestle.common.fsutil import atomic_write

STORE_DIR = "credentials"
STORE_FILE = "store.enc"
KEY_FILE = "store.key"
KEYCHAIN_SERVICE = "trestle-credentials"
KEY_PROVIDER_KEYCHAIN = "keychain"
KEY_PROVIDER_FILE = "file"
KEY_PROVIDERS = (KEY_PROVIDER_KEYCHAIN, KEY_PROVIDER_FILE)
# the one program the keychain provider runs; a test points this at a stub keychain
SECURITY_BIN = "/usr/bin/security"
_SECURITY_TIMEOUT_S = 15.0
# `security find-generic-password`: "The specified item could not be found in the keychain."
_SECURITY_NOT_FOUND = 44
_FORMAT = 1


class StoreError(Exception):
    """The store or its key could not be read or written. The message names no value."""


def default_key_provider() -> str:
    return KEY_PROVIDER_KEYCHAIN if sys.platform == "darwin" else KEY_PROVIDER_FILE


def store_dir(home: Path) -> Path:
    return home / STORE_DIR


def ensure_store_dir(home: Path) -> Path:
    directory = store_dir(home)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


class KeyProvider(Protocol):
    """Where the store's key lives. `read` is None when there is no key yet; `create` makes one
    (once, on the first `add`) and returns it."""

    name: str

    def read(self) -> bytes | None: ...
    def create(self) -> bytes: ...
    def describe(self) -> str: ...


def _new_key() -> bytes:
    return Fernet.generate_key()


@dataclass
class FileKeyProvider:
    """The key in `$TRESTLE_HOME/credentials/store.key`, 0600. Weaker than the Keychain: see the
    module docstring."""

    home: Path
    name: str = KEY_PROVIDER_FILE

    @property
    def path(self) -> Path:
        return store_dir(self.home) / KEY_FILE

    def read(self) -> bytes | None:
        try:
            return self.path.read_bytes().strip() or None
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreError(f"the key file could not be read: {exc.strerror}") from None

    def create(self) -> bytes:
        ensure_store_dir(self.home)
        key = _new_key()
        atomic_write(self.path, key + b"\n", mode=0o600)
        return key

    def describe(self) -> str:
        return f"key file {self.path} (0600)"


@dataclass
class KeychainKeyProvider:
    """The key as a generic password in the login Keychain, through `/usr/bin/security`."""

    home: Path
    name: str = KEY_PROVIDER_KEYCHAIN

    @property
    def account(self) -> str:
        return str(self.home.resolve())

    def _security(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [SECURITY_BIN, *args],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_SECURITY_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StoreError(f"the keychain could not be reached ({SECURITY_BIN}): {exc}") from None

    def read(self) -> bytes | None:
        done = self._security(
            "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", self.account, "-w"
        )
        if done.returncode == _SECURITY_NOT_FOUND:
            return None
        if done.returncode != 0:
            raise StoreError(
                f"the keychain refused the key (security exit {done.returncode}): "
                f"{done.stderr.strip()[:200]}"
            )
        return done.stdout.strip().encode("ascii") or None

    def create(self) -> bytes:
        key = _new_key()
        # `security` takes the password as an argument: the key (not a credential) is in this
        # process's argv for the moment the command runs. There is no non-interactive stdin form.
        done = self._security(
            "add-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            self.account,
            "-l",
            "Trestle credentials store key",
            "-w",
            key.decode("ascii"),
        )
        if done.returncode != 0:
            raise StoreError(
                f"the keychain did not store the key (security exit {done.returncode}): "
                f"{done.stderr.strip()[:200]}"
            )
        return key

    def describe(self) -> str:
        return f"login Keychain, service {KEYCHAIN_SERVICE}, account {self.account}"


def make_key_provider(home: Path, name: str | None) -> KeyProvider:
    chosen = name or default_key_provider()
    if chosen == KEY_PROVIDER_KEYCHAIN:
        return KeychainKeyProvider(home)
    if chosen == KEY_PROVIDER_FILE:
        return FileKeyProvider(home)
    raise StoreError(f"unknown key provider {chosen!r}; expected one of {KEY_PROVIDERS}")


@dataclass
class EncryptedStore:
    """The identities' values, by name, in one encrypted file."""

    home: Path
    provider: KeyProvider

    @property
    def path(self) -> Path:
        return store_dir(self.home) / STORE_FILE

    def _fernet(self, *, create: bool) -> Fernet | None:
        key = self.provider.read()
        if key is None:
            if not create:
                return None
            key = self.provider.create()
        try:
            return Fernet(key)
        except (ValueError, TypeError):
            raise StoreError(f"the store key from {self.provider.name} is malformed") from None

    def exists(self) -> bool:
        return self.path.exists()

    def read_all(self) -> dict[str, str]:
        """Every stored value by name; empty when nothing was ever stored."""
        try:
            token = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise StoreError(f"the store could not be read: {exc.strerror}") from None
        fernet = self._fernet(create=False)
        if fernet is None:
            raise StoreError(
                f"the store exists but its key is missing ({self.provider.describe()})"
            )
        try:
            plain = fernet.decrypt(token)
        except InvalidToken:
            raise StoreError(
                "the store does not decrypt with its key (wrong key or damaged file)"
            ) from None
        loaded = json.loads(plain)
        entries = loaded.get("entries") if isinstance(loaded, dict) else None
        if not isinstance(entries, dict):
            raise StoreError("the store's contents are malformed")
        return {str(k): str(v) for k, v in entries.items()}

    def get(self, name: str) -> str | None:
        return self.read_all().get(name)

    def _write(self, entries: dict[str, str]) -> None:
        ensure_store_dir(self.home)
        fernet = self._fernet(create=True)
        assert fernet is not None
        plain = json.dumps({"format": _FORMAT, "entries": entries}, sort_keys=True).encode("utf-8")
        atomic_write(self.path, fernet.encrypt(plain), mode=0o600)

    def put(self, name: str, value: str) -> None:
        entries = self.read_all()
        entries[name] = value
        self._write(entries)

    def delete(self, name: str) -> bool:
        entries = self.read_all()
        if name not in entries:
            return False
        del entries[name]
        self._write(entries)
        return True
