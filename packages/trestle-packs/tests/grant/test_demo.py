"""L.RB-9.1: the demo grant adapter's edges the family suite does not reach: it is loopback-only and
reads no credential source (D-9, WR-CON-1, MC-13), a refresh that may have landed is UNKNOWN, and a
reply it cannot read is could-not-observe, never a partial observation (B3-E1, B3-C8)."""

from __future__ import annotations

import ast
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from conftest import load_stub
from tests.proof.suites.ports import families
from trestle.workflow.declarations import EffectFacetClass
from trestle.workflow.values import ConfirmationStatus, FoundRef

from trestle_packs.grant import DEMO_CREDENTIAL_KIND, GRANT_ISSUER_UNREACHABLE, DemoGrant
from trestle_packs.grant import demo as demo_module

GRANT_DIR = Path(demo_module.__file__).parent
HOST_OK = {
    "identity": "demo-user",
    "expires_at": "2999-01-01T00:00:00+00:00",
    "generation": "gen-00001",
    "interactive_required": False,
}


class Scripted:
    """A one-route-at-a-time HTTP server whose replies the test scripts (loopback, this process)."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        scripted = routes

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _reply(self, method: str) -> None:
                answer = scripted.get((method, self.path.split("?")[0]), (404, {}))
                if answer is None:  # accept the request, then drop the connection unanswered
                    self.connection.close()
                    return
                status, body = answer
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._reply("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._reply("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def scripted() -> Any:
    servers: list[Scripted] = []

    def make(routes: dict[tuple[str, str], Any]) -> str:
        servers.append(Scripted(routes))
        return servers[-1].url

    yield make
    for server in servers:
        server.close()


def safe_start_ticket() -> Any:
    return families.ticket("refresh", EffectFacetClass.SAFE_START)


def grant() -> FoundRef:
    return FoundRef(DEMO_CREDENTIAL_KIND, "demo-user", datetime.now(UTC))


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://10.0.0.5:9",
        "https://127.0.0.1",
        "http://169.254.169.254",
        "ftp://127.0.0.1",
        "127.0.0.1:9",
    ],
)
def test_the_issuer_must_be_on_loopback_over_http(url: str) -> None:
    with pytest.raises(ValueError, match="loopback|http"):
        DemoGrant(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:1", "http://localhost:1", "http://[::1]:1"])
def test_loopback_urls_are_accepted(url: str) -> None:
    assert DemoGrant(url).issuer_url == url


def test_the_adapter_reads_no_environment_or_credential_store() -> None:
    """MC-13's premise for the grant package: nothing in it can reach a real credential source."""
    forbidden_attrs = {"environ", "getenv", "expanduser", "home", "putenv"}
    forbidden_text = (".aws", "credentials", "boto", "AWS_", "keyring")
    for path in sorted(GRANT_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_attrs, (path.name, node.lineno, node.attr)
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & {"subprocess", "boto3"}
        if path.name in {"demo.py", "__init__.py"}:
            for word in forbidden_text:
                strings = [
                    n.value
                    for n in ast.walk(ast.parse(source))
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                ]
                assert not any(word in s for s in strings if len(s) < 80), (path.name, word)


def test_an_unreadable_reply_is_could_not_observe_not_a_partial_observation(scripted: Any) -> None:
    for reply in (
        (200, b"not json"),
        (200, []),
        (500, {}),
        (200, {**HOST_OK, "expires_at": "yesterday"}),
        (200, {**HOST_OK, "expires_at": "2999-01-01T00:00:00"}),
        (200, {**HOST_OK, "interactive_required": "no"}),
        (200, {**HOST_OK, "identity": "x" * 257}),
    ):
        seen = DemoGrant(scripted({("GET", "/host"): reply})).observe_host()
        assert seen.code == GRANT_ISSUER_UNREACHABLE and seen.identity == "", reply


def test_a_refresh_that_may_have_landed_is_unknown(scripted: Any) -> None:
    for reply in (None, (500, {}), (200, b"garbage")):
        url = scripted({("GET", "/host"): (200, HOST_OK), ("POST", "/refresh"): reply})
        done = DemoGrant(url).refresh(grant(), safe_start_ticket())
        assert done.status is ConfirmationStatus.UNKNOWN, reply


def test_a_refresh_the_issuer_refuses_as_interactive_is_not_applied_with_the_identity(
    scripted: Any,
) -> None:
    url = scripted(
        {("GET", "/host"): (200, HOST_OK), ("POST", "/refresh"): (409, {"error": "interactive"})}
    )
    done = DemoGrant(url).refresh(grant(), safe_start_ticket())
    assert (done.status, done.code, done.identity) == (
        ConfirmationStatus.NOT_APPLIED,
        "execution.credential_interactive",
        "demo-user",
    )


def test_no_probe_means_no_authenticated_call() -> None:
    seen = DemoGrant("http://127.0.0.1:1").observe_in_consumer(
        FoundRef("consumer", "c-one", datetime.now(UTC))
    )
    assert (seen.authenticated, seen.generation_seen, seen.code) == (False, None, None)


def test_the_real_issuer_and_the_adapter_agree_on_expiry_to_the_microsecond() -> None:
    with load_stub("stub_issuer").StubIssuer() as issuer:
        seen = DemoGrant(issuer.url).observe_host()
    assert seen.expires_at == issuer.state.expires_at() and seen.code is None
