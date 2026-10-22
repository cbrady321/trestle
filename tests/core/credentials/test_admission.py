"""Change 3 acceptance: identities by name, grants by digest, and the scrub over both shapes.

- `admission.credential_unknown` and `admission.credential_not_granted` are refused before a run
  id, run directory or process exists, naming the identity (and, for not_granted, the operator
  command that fixes it).
- A republished plugin whose entry file or a declared package changed loses the grant; `trestle
  credentials grant` re-pins it.
- A value delivered through `ctx.credential_env` or `ctx.credential_file` that the plugin logs,
  prints, returns, raises with and attaches is `<redacted>` on disk and in every answer.
- The `credential_file` file is 0600 in a 0700 channel directory, and gone after the run.

Every value here is a fake stand-in (D-9); the key lives with the stub keychain (conftest).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.core.credentials.conftest import CliResult
from tests.proof import harness, tolerances
from trestle.common import codes, redact
from trestle.common.types import RequestOutcome, RunView
from trestle.server.credentials import load_credentials
from trestle.server.ledger import run_dir_for
from trestle.server.main import Kernel

SENTINEL = "FAKE-cred-sentinel-3c5e7a91"
ROTATED = "FAKE-cred-rotated-b2d4f608"

PROBE = """\
import hashlib
import os

from trestle.plugin import Context, trestle


@trestle(credentials=["demo-token"])
def cred_probe(ctx: Context, mode: str = "ok") -> dict[str, str]:
    env = ctx.credential_env("demo-token")
    value = env["DEMO_TOKEN"]
    path = ctx.credential_file("demo-token")
    from_file = path.read_text()
    try:
        ctx.credential_file("not-declared")
        undeclared = "no error"
    except LookupError as exc:
        undeclared = str(exc)
    ctx.log(f"env shape {value}")
    ctx.log(f"file shape {from_file}")
    ctx.event("probe", value=value)
    print(f"printed {value} and {from_file}")
    staged = ctx.tmp / "leak.txt"
    staged.write_text(f"attached {value}")
    ctx.attach(staged, name="leak.txt")
    (ctx.outputs / "promoted.txt").write_text(f"promoted {from_file}")
    if mode == "raise":
        raise RuntimeError(f"failing with {value}")
    return {
        "value": value,
        "digest": hashlib.sha256(value.encode()).hexdigest(),
        "file_value": from_file,
        "mode": oct(path.stat().st_mode & 0o777),
        "dir_mode": oct(path.parent.stat().st_mode & 0o777),
        "undeclared": undeclared,
        "in_os_environ": os.environ.get("DEMO_TOKEN", "absent"),
    }
"""

UNKNOWN = """\
from trestle.plugin import Context, trestle


@trestle(credentials=["nobody-configured-this"])
def cred_unknown(ctx: Context) -> dict[str, str]:
    return {"ran": "yes"}
"""

WITH_PACKAGE = """\
import cred_pkg

from trestle.plugin import Context, trestle


@trestle(credentials=["demo-token"], packages=["cred_pkg"])
def cred_pkg_probe(ctx: Context) -> dict[str, int]:
    return {"value": cred_pkg.VALUE, "seen": len(ctx.credential_env("demo-token"))}
"""

Cli = Callable[..., CliResult]


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    for name, source in (("cred_probe", PROBE), ("cred_unknown", UNKNOWN)):
        (home / "plugins" / f"{name}.py").write_text(source, encoding="utf-8")
    return home


def kernel_on(home: Path) -> Kernel:
    """A fresh server start: credentials.toml is read here, once."""
    return harness.fresh_kernel([home / "plugins"], home=home)


def run(
    kernel: Kernel, plugin: str, args: dict[str, Any] | None = None
) -> RunView | RequestOutcome:
    return kernel.control.run(plugin=plugin, args=args or {}, wait_ms=tolerances.HARNESS_WAIT_MS)


def finished(kernel: Kernel, plugin: str, args: dict[str, Any] | None = None) -> RunView:
    view = run(kernel, plugin, args)
    assert isinstance(view, RunView), view
    ended = kernel.control.project.await_terminal(view.run_id)
    assert isinstance(ended, RunView), ended
    return ended


def no_run_exists(home: Path) -> None:
    """No run id was minted: no run directory (built or committed) and no live marker."""
    assert list((home / "runs").glob("*/*")) == []
    live = home / "live"
    assert not live.exists() or not any(live.iterdir())


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def result_of(kernel: Kernel, view: RunView) -> dict[str, Any]:
    path = run_dir_for(kernel.home, view.run_id) / "evidence" / "result.json"
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def occurrences(root: Path, needle: str) -> list[str]:
    raw = needle.encode("utf-8")
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and raw in p.read_bytes()
    )


def add(cli: Cli, home: Path, *extra: str, value: str = SENTINEL) -> None:
    code, _, err = cli(home, "add", "demo-token", "--env", "DEMO_TOKEN", *extra, stdin=value)
    assert code == 0, err


def test_unknown_identity_is_refused_before_a_run_id(home: Path) -> None:
    kernel = kernel_on(home)
    refused = run(kernel, "cred_unknown")
    assert isinstance(refused, RequestOutcome), refused
    assert refused.code == codes.ADMISSION_CREDENTIAL_UNKNOWN
    assert "nobody-configured-this" in refused.message
    assert "trestle credentials add nobody-configured-this" in refused.message
    no_run_exists(home)


def test_not_granted_is_refused_before_a_run_id(home: Path, credentials_cli: Cli) -> None:
    add(credentials_cli, home)  # configured, granted to nothing
    kernel = kernel_on(home)
    refused = run(kernel, "cred_probe")
    assert isinstance(refused, RequestOutcome), refused
    assert refused.code == codes.ADMISSION_CREDENTIAL_NOT_GRANTED
    assert "trestle credentials grant demo-token cred_probe" in refused.message
    assert SENTINEL not in refused.message
    no_run_exists(home)


def test_scrub_covers_both_shapes_and_the_channel_goes(home: Path, credentials_cli: Cli) -> None:
    add(credentials_cli, home, "--grant", "cred_probe")
    kernel = kernel_on(home)
    ended = finished(kernel, "cred_probe")
    assert ended.state == "succeeded", ended
    run_dir = run_dir_for(kernel.home, ended.run_id)
    result = json.loads((run_dir / "evidence" / "result.json").read_text(encoding="utf-8"))
    # the plugin had the value in both shapes (the record shows only that it was there) ...
    assert result["value"] == redact.REDACTED and result["file_value"] == redact.REDACTED
    assert result["digest"] == digest(SENTINEL)
    # ... the file was 0600 in a 0700 directory, and nothing was put into os.environ
    assert result["mode"] == "0o600" and result["dir_mode"] == "0o700"
    assert result["in_os_environ"] == "absent"
    assert "not-declared" in result["undeclared"] and "not declared" in result["undeclared"]
    # the channel directory is gone with the run
    assert not (run_dir / "channel").exists()
    # the value is nowhere on disk in the clear: run directory, home, store (encrypted)
    assert occurrences(run_dir, SENTINEL) == []
    assert occurrences(kernel.home, SENTINEL) == []
    assert (run_dir / "evidence" / "events.ndjson").read_text().count(redact.REDACTED) >= 3
    # nor in an answer: the run view, the tail, the events, the artifact and the result fetch
    answers = [json.dumps(ended.to_dict())]
    for view in ("run_tail", "run_events", "run_artifacts"):
        answers.append(json.dumps(kernel.control.query(view, {"run_id": ended.run_id})))
    answers.append(json.dumps(kernel.control.fetch(f"{ended.run_id}/result", {"kind": "head"})))
    for answer in answers:
        assert SENTINEL not in answer
    assert any(redact.REDACTED in answer for answer in answers[1:])


def test_scrub_covers_a_raise(home: Path, credentials_cli: Cli) -> None:
    add(credentials_cli, home, "--grant", "cred_probe")
    kernel = kernel_on(home)
    ended = finished(kernel, "cred_probe", {"mode": "raise"})
    assert ended.state == "failed", ended
    last = kernel.control.query("last_error", {"run_id": ended.run_id})
    text = json.dumps(last)
    assert SENTINEL not in text and redact.REDACTED in text
    run_dir = run_dir_for(kernel.home, ended.run_id)
    assert occurrences(run_dir, SENTINEL) == []
    assert not (run_dir / "channel").exists()


def test_rotation_applies_to_the_next_run_without_a_restart(
    home: Path, credentials_cli: Cli
) -> None:
    add(credentials_cli, home, "--grant", "cred_probe")
    kernel = kernel_on(home)
    first = finished(kernel, "cred_probe")
    code, _, err = credentials_cli(home, "add", "demo-token", "--replace", stdin=ROTATED)
    assert code == 0, err
    second = finished(kernel, "cred_probe")  # the same server: no restart
    assert second.state == "succeeded"
    # the second run was given the rotated value, and its scrub covered it
    assert result_of(kernel, first)["digest"] == digest(SENTINEL)
    assert result_of(kernel, second)["digest"] == digest(ROTATED)
    run_dir = run_dir_for(kernel.home, second.run_id)
    assert occurrences(run_dir, ROTATED) == [] and first.run_id != second.run_id
    tail = json.dumps(kernel.control.query("run_tail", {"run_id": second.run_id}))
    assert ROTATED not in tail and SENTINEL not in tail


def test_republished_entry_file_loses_the_grant(home: Path, credentials_cli: Cli) -> None:
    add(credentials_cli, home, "--grant", "cred_probe")
    path = home / "plugins" / "cred_probe.py"
    path.write_text(PROBE + "\n# republished\n", encoding="utf-8")
    refused = run(kernel_on(home), "cred_probe")
    assert isinstance(refused, RequestOutcome) and (
        refused.code == codes.ADMISSION_CREDENTIAL_NOT_GRANTED
    ), refused
    no_run_exists(home)
    code, out, _ = credentials_cli(home, "grant", "demo-token", "cred_probe")
    assert code == 0 and "re-pinned" in out
    assert finished(kernel_on(home), "cred_probe").state == "succeeded"


def test_changed_package_loses_the_grant(
    home: Path, tmp_path: Path, credentials_cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    module = lib / "cred_pkg.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(lib) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    (home / "plugins" / "cred_pkg_probe.py").write_text(WITH_PACKAGE, encoding="utf-8")
    add(credentials_cli, home, "--grant", "cred_pkg_probe")
    (grant,) = load_credentials(home).identities["demo-token"].grants
    assert set(grant.package_digests) == {"cred_pkg"}
    assert finished(kernel_on(home), "cred_pkg_probe").state == "succeeded"

    module.write_text("VALUE = 2\n", encoding="utf-8")  # the entry file is unchanged
    refused = run(kernel_on(home), "cred_pkg_probe")
    assert isinstance(refused, RequestOutcome), refused
    assert refused.code == codes.ADMISSION_CREDENTIAL_NOT_GRANTED
    code, out, _ = credentials_cli(home, "list")
    assert "grant cred_pkg_probe: stale" in out
    code, _, _ = credentials_cli(home, "grant", "demo-token", "cred_pkg_probe")
    assert code == 0
    assert finished(kernel_on(home), "cred_pkg_probe").state == "succeeded"


def test_describe_lists_names_only_and_marks_secrets_deprecated(
    home: Path, credentials_cli: Cli
) -> None:
    (home / "plugins" / "old_secret.py").write_text(
        "from trestle.plugin import Context, trestle\n\n\n"
        '@trestle(secrets=["token"])\n'
        "def old_secret(ctx: Context, token: str) -> dict[str, int]:\n"
        '    return {"n": len(token)}\n',
        encoding="utf-8",
    )
    add(credentials_cli, home, "--grant", "cred_probe")
    kernel = kernel_on(home)
    described = kernel.control.describe_plugin("cred_probe")
    assert isinstance(described, dict)
    assert described["credentials"] == ["demo-token"]
    text = json.dumps(described)
    assert SENTINEL not in text and "DEMO_TOKEN" not in text and "grants" not in text
    old = kernel.control.describe_plugin("old_secret")
    assert isinstance(old, dict)
    assert old["secrets"] == ["token"] and "deprecated" in old["deprecated"][0]
    assert "credentials" not in old
