"""The HTTP readiness read facet answers only the declared response (L.RB-2.1; KDD 2)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from trestle.workflow.declarations import Vantage
from trestle.workflow.ports import Endpoint, RouteRefused
from trestle.workflow.values import CheckResult, Lineage, NodePath, SelectorRef

from trestle_env import tree
from trestle_env.plugins._http import HttpReadinessReads

CONTRACT = tree.HTTP_SUPPORT_READINESS
TARGET = SelectorRef(Lineage("r_1", NodePath(("a",))), "up", "sel", datetime.now(UTC))


class Answer:
    """What the listener says to the next request."""

    status = 200
    body = b"ok"
    location: str | None = None
    hits = 0


@pytest.fixture
def listener() -> Iterator[tuple[int, type[Answer]]]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            Answer.hits += 1
            self.send_response(Answer.status)
            if Answer.location is not None:
                self.send_header("Location", Answer.location)
            self.send_header("Content-Length", str(len(Answer.body)))
            self.end_headers()
            self.wfile.write(Answer.body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return None

    Answer.status, Answer.body, Answer.location, Answer.hits = 200, b"ok", None, 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], Answer
    server.shutdown()
    server.server_close()


class Inner:
    """The wrapped port: alive or not, one endpoint, and it answers its own checks."""

    def __init__(self, endpoint: Endpoint | RouteRefused, alive: bool = True) -> None:
        self.endpoint_of = endpoint
        self.alive = alive
        self.asked: list[str] = []

    def observe(self, spec: Any, lineage: Any, effect: Any) -> str:
        return "observed"

    def check(self, check: str, target: Any) -> CheckResult:
        self.asked.append(check)
        return CheckResult(self.alive, None, f"inner {check}")

    def endpoint(self, target: Any, vantage: Vantage) -> Endpoint | RouteRefused:
        assert vantage is Vantage.HOST
        return self.endpoint_of


def reads(inner: Inner) -> HttpReadinessReads:
    return HttpReadinessReads(inner, tree.HTTP_READINESS)  # type: ignore[arg-type]


def test_only_the_declared_response_is_ready(listener: tuple[int, type[Answer]]) -> None:
    port, answer = listener
    facet = reads(Inner(Endpoint("tcp", "127.0.0.1", port)))
    assert facet.check(tree.HTTP_SUPPORT_READY, TARGET).satisfied
    for status, body in ((503, b"not ready"), (200, b"okay"), (200, b""), (404, b"ok")):
        answer.status, answer.body = status, body
        assert not facet.check(tree.HTTP_SUPPORT_READY, TARGET).satisfied, (status, body)


def test_a_redirect_is_another_answer_never_followed(listener: tuple[int, type[Answer]]) -> None:
    port, answer = listener
    answer.status, answer.location = 302, f"http://127.0.0.1:{port}/health"
    result = reads(Inner(Endpoint("tcp", "127.0.0.1", port))).check(tree.HTTP_SUPPORT_READY, TARGET)
    assert not result.satisfied
    assert answer.hits == 1


def test_a_process_that_is_up_with_its_port_open_is_not_ready_without_the_response() -> None:
    import socket

    with socket.socket() as bound:  # a port that is open (listening) but never answers HTTP
        bound.bind(("127.0.0.1", 0))
        bound.listen()
        endpoint = Endpoint("tcp", "127.0.0.1", bound.getsockname()[1])
        inner = Inner(endpoint)
        result = HttpReadinessReads(inner, tree.HTTP_READINESS, timeout_s=0.2).check(  # type: ignore[arg-type]
            tree.HTTP_SUPPORT_READY, TARGET
        )
    assert inner.asked == ["running"]  # up, as far as the wrapped port can tell
    assert not result.satisfied and result.code is None


def test_a_refused_connection_is_not_ready_yet_not_a_code() -> None:
    result = reads(Inner(Endpoint("tcp", "127.0.0.1", 1))).check(tree.HTTP_SUPPORT_READY, TARGET)
    assert not result.satisfied and result.code is None


def test_a_resource_that_is_not_alive_is_never_asked_over_http(
    listener: tuple[int, type[Answer]],
) -> None:
    port, answer = listener
    inner = Inner(Endpoint("tcp", "127.0.0.1", port), alive=False)
    result = reads(inner).check(tree.HTTP_SUPPORT_READY, TARGET)
    assert not result.satisfied and result.detail == "inner running"
    assert answer.hits == 0


def test_only_loopback_is_ever_requested(listener: tuple[int, type[Answer]]) -> None:
    port, answer = listener
    result = reads(Inner(Endpoint("tcp", "192.0.2.1", port))).check(tree.HTTP_SUPPORT_READY, TARGET)
    assert not result.satisfied and "not loopback" in result.detail
    routed = reads(Inner(RouteRefused("admission.route_unsupported", "x"))).check(
        tree.HTTP_SUPPORT_READY, TARGET
    )
    assert not routed.satisfied and answer.hits == 0


def test_every_other_check_and_read_is_the_wrapped_ports() -> None:
    inner = Inner(Endpoint("tcp", "127.0.0.1", 1))
    facet = reads(inner)
    assert facet.check("running", TARGET).detail == "inner running"
    assert facet.check(tree.POSTGRES_READY, TARGET).detail == f"inner {tree.POSTGRES_READY}"
    assert facet.observe(None, None, None) == "observed"  # type: ignore[comparison-overlap]
