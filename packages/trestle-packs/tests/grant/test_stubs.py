"""L.RB-9.1: the demo stubs `stub_issuer` and `stub_cloud` behave as MC-B-06 describes (an AWS-CLI
shaped client and an HTTP issuer with generation ordering), and are demo-only (D-9, WR-CON-1)."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from tests.proof import tolerances

from grant.conftest import STUBS, load_stub


@pytest.fixture
def issuer() -> Any:
    with load_stub("stub_issuer").StubIssuer() as running:
        yield running


def call(url: str, method: str = "GET", token: str | None = None) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(  # noqa: S310 - the stub issuer on loopback
        url, method=method, headers=headers, data=b"" if method == "POST" else None
    )
    try:
        with urllib.request.urlopen(request, timeout=tolerances.JOIN_WAIT_S) as reply:  # noqa: S310
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_host_route_names_no_secret(issuer: Any) -> None:
    status, host = call(f"{issuer.url}/host")
    assert status == 200
    assert set(host) == {"identity", "expires_at", "generation", "interactive_required"}
    assert all(secret not in json.dumps(host) for secret in issuer.state.secret_values())


def test_credential_route_is_the_only_one_that_returns_the_token(issuer: Any) -> None:
    for path in ("/host", "/order?a=x&b=y", "/whoami"):
        assert issuer.state.nonce not in json.dumps(call(f"{issuer.url}{path}")[1])
    assert call(f"{issuer.url}/credential")[1]["token"] == issuer.state.token()


def test_generation_order_is_the_issuers_own_not_the_string_order(issuer: Any) -> None:
    first = issuer.state.current()
    later = issuer.state.advance()
    while not later < first:  # a later generation that sorts below the first
        later = issuer.state.advance()
    order = lambda a, b: call(f"{issuer.url}/order?a={a}&b={b}")[1]["relation"]  # noqa: E731
    assert order(first, later) == "older" and order(later, first) == "newer"
    assert order(first, first) == "same" and order(first, "gen-never-issued") == "unknown"


def test_refresh_moves_the_generation_unless_the_identity_is_interactive(issuer: Any) -> None:
    before = issuer.state.current()
    assert call(f"{issuer.url}/refresh", "POST")[0] == 200 and issuer.state.current() != before
    call(f"{issuer.url}/admin/interactive?value=1", "POST")
    kept = issuer.state.current()
    assert call(f"{issuer.url}/refresh", "POST") == (409, {"error": "interactive"})
    assert issuer.state.current() == kept


def test_whoami_authenticates_only_an_issued_live_token(issuer: Any) -> None:
    good = issuer.state.token()
    assert call(f"{issuer.url}/whoami", token=good)[1]["authenticated"] is True
    alien = issuer.state.token("gen-never-issued")
    assert call(f"{issuer.url}/whoami", token=alien) == (
        401,
        {"authenticated": False, "generation": "gen-never-issued"},
    )
    assert call(f"{issuer.url}/whoami", token="not-a-demo-token")[1]["generation"] is None
    call(f"{issuer.url}/admin/lifetime?seconds=-1", "POST")  # expired
    assert call(f"{issuer.url}/whoami", token=good)[0] == 401


def run_cloud(
    *args: str, credentials: Path | None, endpoint: str
) -> subprocess.CompletedProcess[str]:
    env = {"STUB_CLOUD_ENDPOINT_URL": endpoint}
    if credentials is not None:
        env["STUB_CLOUD_CREDENTIALS_FILE"] = str(credentials)
    return subprocess.run(  # noqa: S603 - the stub by absolute path, this interpreter
        [sys.executable, str(STUBS / "stub_cloud.py"), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=env,
        timeout=tolerances.JOIN_WAIT_S,
        check=False,
    )


def test_stub_cloud_is_aws_cli_shaped_and_prints_the_generation_never_the_token(
    issuer: Any, tmp_path: Path
) -> None:
    creds = tmp_path / "credentials"
    creds.write_text(issuer.state.token(), encoding="utf-8")
    done = run_cloud("sts", "get-caller-identity", credentials=creds, endpoint=issuer.url)
    assert done.returncode == 0, done.stderr
    who = json.loads(done.stdout)
    assert set(who) == {"UserId", "Account", "Arn", "Generation"}
    assert who["Generation"] == issuer.state.current()
    assert all(secret not in done.stdout + done.stderr for secret in issuer.state.secret_values())


def test_stub_cloud_exits_254_for_a_token_the_issuer_does_not_accept(
    issuer: Any, tmp_path: Path
) -> None:
    creds = tmp_path / "credentials"
    creds.write_text(issuer.state.token("gen-never-issued"), encoding="utf-8")
    done = run_cloud("sts", "get-caller-identity", credentials=creds, endpoint=issuer.url)
    assert done.returncode == 254
    assert json.loads(done.stdout) == {
        "Error": "InvalidClientTokenId",
        "Generation": "gen-never-issued",
    }


def test_stub_cloud_without_credentials_or_an_issuer_or_a_known_command(
    issuer: Any, tmp_path: Path
) -> None:
    assert (
        run_cloud("sts", "get-caller-identity", credentials=None, endpoint=issuer.url).returncode
        == 255
    )
    creds = tmp_path / "credentials"
    creds.write_text(issuer.state.token(), encoding="utf-8")
    gone = issuer.url
    issuer.stop()
    down = run_cloud("sts", "get-caller-identity", credentials=creds, endpoint=gone)
    assert down.returncode == 255 and json.loads(down.stdout)["Generation"] is None
    assert run_cloud("s3", "ls", credentials=creds, endpoint=gone).returncode == 2


def test_the_stubs_import_no_credential_source() -> None:
    for name in ("stub_issuer.py", "stub_cloud.py"):
        text = (STUBS / name).read_text(encoding="utf-8")
        for forbidden in (".aws", "boto", "expanduser", "AWS_ACCESS_KEY", "AWS_SECRET"):
            assert forbidden not in text, (name, forbidden)
