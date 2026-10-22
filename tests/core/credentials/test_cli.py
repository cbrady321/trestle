"""Change 3: `trestle credentials ...`, the operator's way to add, grant and remove an identity.

Run in process against a temporary home with the stub keychain (conftest): the value arrives on
piped stdin and is never an argument, `list` never prints it, `grant` pins the plugin's current
digests and refuses an agent-published plugin, `add --replace` rotates in place keeping the grants.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.core.credentials.conftest import CliResult, StubKeychain
from trestle.common.types import PublishView
from trestle.server.credential_store import default_key_provider
from trestle.server.credentials import load_credentials
from trestle.server.credentials_cli import RESTART_NOTE, ROTATED_NOTE
from trestle.server.main import create_kernel

VALUE = "FAKE-cli-value-61b2c3d4"
ROTATED = "FAKE-cli-rotated-9a8b7c6d"

PLUGIN = """\
from trestle.plugin import Context, trestle


@trestle(credentials=["demo-token"])
def cred_cli(ctx: Context) -> dict[str, bool]:
    return {"ok": bool(ctx.credential_env("demo-token"))}
"""

AGENT_PLUGIN = PLUGIN.replace("cred_cli", "cred_agent")

Cli = Callable[..., CliResult]


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "cred_cli.py").write_text(PLUGIN, encoding="utf-8")
    return home


def test_round_trip(home: Path, credentials_cli: Cli, stub_keychain: StubKeychain) -> None:
    code, out, err = credentials_cli(home, "add", "demo-token", "--env", "DEMO_TOKEN", stdin=VALUE)
    assert code == 0, err
    assert RESTART_NOTE in out and VALUE not in out
    # the first add fixes the key provider: the platform's default (the stub keychain on macOS)
    assert load_credentials(home).key_provider == default_key_provider()
    if default_key_provider() == "keychain":
        assert stub_keychain.calls().count("add-generic-password") == 1  # the key, made once

    code, out, _ = credentials_cli(home, "list")
    assert code == 0
    assert "demo-token" in out and "env=DEMO_TOKEN" in out and "grants: none" in out
    assert VALUE not in out

    code, out, err = credentials_cli(home, "grant", "demo-token", "cred_cli")
    assert code == 0, err
    assert "granted demo-token to cred_cli" in out and RESTART_NOTE in out
    (grant,) = load_credentials(home).identities["demo-token"].grants
    assert grant.plugin == "cred_cli" and grant.source_sha256 and grant.package_digests == {}
    code, out, _ = credentials_cli(home, "list")
    assert "grant cred_cli: current" in out

    # an existing name is refused without --replace; --replace rotates in place
    code, _, err = credentials_cli(home, "add", "demo-token", "--env", "DEMO_TOKEN", stdin=ROTATED)
    assert code == 1 and "--replace" in err
    code, out, err = credentials_cli(home, "add", "demo-token", "--replace", stdin=ROTATED)
    assert code == 0, err
    assert ROTATED_NOTE in out and RESTART_NOTE not in out  # value only: the next run sees it
    assert load_credentials(home).identities["demo-token"].grants == (grant,)
    assert load_credentials(home).store(home).get("demo-token") == ROTATED

    # the plugin is republished with a change: the pin is stale until `grant` re-pins it
    path = home / "plugins" / "cred_cli.py"
    path.write_text(PLUGIN + "\n# edited\n", encoding="utf-8")
    code, out, _ = credentials_cli(home, "list")
    assert "grant cred_cli: stale" in out
    code, out, _ = credentials_cli(home, "grant", "demo-token", "cred_cli")
    assert code == 0 and "re-pinned" in out
    code, out, _ = credentials_cli(home, "list")
    assert "grant cred_cli: current" in out

    code, out, err = credentials_cli(home, "revoke", "demo-token", "cred_cli")
    assert code == 0, err
    assert load_credentials(home).identities["demo-token"].grants == ()

    code, out, err = credentials_cli(home, "remove", "demo-token")
    assert code == 0, err
    assert "demo-token" not in load_credentials(home).identities
    assert load_credentials(home).store(home).get("demo-token") is None
    code, out, _ = credentials_cli(home, "list")
    assert "no credential identities configured" in out
    # nothing the commands wrote holds the value in the clear
    for path in home.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert VALUE.encode() not in data and ROTATED.encode() not in data, path


def test_value_is_never_an_argument(home: Path, credentials_cli: Cli) -> None:
    code, _, _ = credentials_cli(home, "add", "demo-token", VALUE, "--env", "DEMO_TOKEN")
    assert code == 2  # argparse: an extra positional is not accepted
    code, _, _ = credentials_cli(home, "add", "demo-token", "--env", "X", "--value", VALUE)
    assert code == 2
    assert not (home / "credentials.toml").exists()
    code, _, err = credentials_cli(home, "add", "demo-token", "--env", "DEMO_TOKEN", stdin="")
    assert code == 1 and "no value" in err
    assert "demo-token" not in load_credentials(home).identities


def test_add_with_grant_and_file_key_provider(
    home: Path, credentials_cli: Cli, stub_keychain: StubKeychain
) -> None:
    code, out, err = credentials_cli(
        home,
        "add",
        "demo-token",
        "--env",
        "DEMO_TOKEN",
        "--grant",
        "cred_cli",
        "--key-provider",
        "file",
        stdin=VALUE + "\n",
    )
    assert code == 0, err
    assert "granted demo-token to cred_cli" in out
    config = load_credentials(home)
    assert config.key_provider == "file"
    assert config.store(home).get("demo-token") == VALUE  # the trailing newline is dropped
    assert stub_keychain.calls() == []
    code, _, err = credentials_cli(home, "grant", "demo-token", "no_such_plugin")
    assert code == 1 and "no plugin" in err


def test_grant_refuses_an_agent_published_plugin(home: Path, credentials_cli: Cli) -> None:
    kernel = create_kernel(home=home, plugin_dirs=[home / "plugins"], skip_recovery=True)
    published = kernel.control.publish_plugin(AGENT_PLUGIN)
    assert isinstance(published, PublishView), published
    code, _, err = credentials_cli(
        home, "add", "demo-token", "--env", "DEMO_TOKEN", "--grant", "cred_agent", stdin=VALUE
    )
    assert code == 1 and "published by an agent" in err
    assert not (home / "credentials.toml").exists()  # nothing stored when a grant is refused
    code, _, _ = credentials_cli(home, "add", "demo-token", "--env", "DEMO_TOKEN", stdin=VALUE)
    assert code == 0
    code, _, err = credentials_cli(home, "grant", "demo-token", "cred_agent")
    assert code == 1 and "published by an agent" in err
    # an operator's own edit of the file makes it the operator's publication
    path = home / "plugins" / "cred_agent.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# reviewed\n", encoding="utf-8")
    code, out, err = credentials_cli(home, "grant", "demo-token", "cred_agent")
    assert code == 0, err


def test_doctor_reports_identities_and_stale_grants(home: Path, credentials_cli: Cli) -> None:
    from trestle.server.doctor import build_doctor_report

    credentials_cli(
        home, "add", "demo-token", "--env", "DEMO_TOKEN", "--grant", "cred_cli", stdin=VALUE
    )
    lines = build_doctor_report(home=home, plugin_dirs=[home / "plugins"]).lines()
    text = "\n".join(lines)
    assert "credentials: 1 identities" in text
    assert "grant cred_cli: current" in text and "store: readable" in text
    assert VALUE not in text
    (home / "plugins" / "cred_cli.py").write_text(PLUGIN + "\n# edited\n", encoding="utf-8")
    text = "\n".join(build_doctor_report(home=home, plugin_dirs=[home / "plugins"]).lines())
    assert "grant cred_cli: stale" in text
    assert "warning: credential demo-token grant to cred_cli is stale" in text


def test_exec_is_never_granted(home: Path, credentials_cli: Cli) -> None:
    """Change 2c: `grant x exec` and `add x --grant exec` are refused before anything is written,
    in the form the command uses for its other refusals."""
    from trestle.server.init_cmd import seed_exec_source

    (home / "plugins" / "exec.py").write_bytes(seed_exec_source())
    code, _, err = credentials_cli(
        home, "add", "demo-token", "--env", "DEMO_TOKEN", "--grant", "exec", stdin=VALUE
    )
    assert code == 1 and "never granted a credential" in err, err
    assert not (home / "credentials.toml").exists()
    code, _, _ = credentials_cli(home, "add", "demo-token", "--env", "DEMO_TOKEN", stdin=VALUE)
    assert code == 0
    before = (home / "credentials.toml").read_bytes()
    code, _, err = credentials_cli(home, "grant", "demo-token", "exec")
    assert code == 1 and "never granted a credential" in err, err
    assert (home / "credentials.toml").read_bytes() == before
