"""Run callbacks (docs/proposals/2026-10-23-run-callbacks-design.md).

A run sent with `callback="<url>"` gets one HTTP POST to that URL once it has ended, however it
ended: after its evidence is finalized and its terminal row is written. Trestle does not provide
the listener; it promises only that the request is sent, and what it looks like (version 1).

- Admission (`check_url`): an absolute http(s) URL of at most 2,048 bytes with no user/password
  part and no fragment (`admission.invalid_args`); a loopback host (`127.0.0.1`, `[::1]`,
  `localhost`) or a match in `[callbacks] allow` (`admission.callback_not_allowed`); never one of
  Trestle's own MCP or ops ports.
- Durability: admission writes `<run_dir>/callback.json` (status `pending`) and the empty marker
  `home/callbacks/pending/<run_id>`. The sender holds the marker's flock while it sends, so one
  server sends a given callback, and removes the marker once the status is `delivered`, `failed`
  or `abandoned`. Each serving process goes through the folder at start and on each reaper pass,
  so a run recovery finalized `interrupted` still gets its callback.
- Delivery: attempts at 0 s, 2 s, 10 s, 30 s and 2 min, a 5 s timeout each; a `2xx` delivers;
  a refused connection, a timeout, `408`, `429` or `5xx` retries; any other answer (a redirect
  included: none is followed) fails at once. A marker whose run ended more than 24 hours ago is
  closed `abandoned` without sending.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import http.client
import json
import os
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit

from trestle.common.fsutil import atomic_write_json

# -- the contract -----------------------------------------------------------------------------

BODY_TYPE = "trestle.run.ended"
BODY_VERSION = 1
EVENT_HEADER = "run.ended"
EVENT = "ended"
MAX_URL_BYTES = 2048
MAX_BODY_BYTES = 2048
# delivery: the offset of each attempt from the first (seconds), each with its own timeout; fixed
# by the contract (Q-4), module values only so a test can shorten them
SCHEDULE_S: tuple[float, ...] = (0.0, 2.0, 10.0, 30.0, 120.0)
TIMEOUT_S = 5.0
ABANDON_AFTER_S = 24 * 3600.0
# a marker with no run behind it is admission debris once it is this old (an admission writes its
# marker a few milliseconds before the run's directory becomes visible)
ORPHAN_AFTER_S = 3600.0

STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"
STATUS_ABANDONED = "abandoned"
CLOSED_STATUSES = frozenset({STATUS_DELIVERED, STATUS_FAILED, STATUS_ABANDONED})

CALLBACK_FILE = "callback.json"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# `localhost` is never looked up: these addresses are tried in order, with `Host: localhost`
LOCALHOST_ADDRESSES: tuple[str, ...] = ("127.0.0.1", "::1")
_VIEW_FIELDS = ("origin", "status", "attempts", "last_http_status", "last_error", "delivered_at")
_ERROR_BYTES = 200


# -- the URL and the operator's list ----------------------------------------------------------


@dataclass(frozen=True)
class AllowEntry:
    """One `[callbacks] allow` prefix: scheme, host and port equal, and the path starting with
    `path` at a `/` boundary."""

    scheme: str
    host: str
    port: int
    path: str

    def matches(self, scheme: str, host: str, port: int, path: str) -> bool:
        if (scheme, host, port) != (self.scheme, self.host, self.port):
            return False
        if path == self.path:
            return True
        prefix = self.path if self.path.endswith("/") else self.path + "/"
        return path.startswith(prefix)


@dataclass(frozen=True)
class Target:
    """A checked callback URL, split for the sender."""

    url: str
    scheme: str
    host: str
    port: int
    netloc: str
    target: str  # the request target: path and query

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.netloc}"


class UrlError(ValueError):
    """A callback that is not a well-formed URL (`admission.invalid_args`)."""


class NotAllowed(ValueError):
    """A well-formed callback this server will not call (`admission.callback_not_allowed`)."""


def _split(url: object, what: str) -> tuple[SplitResult, str, int]:
    if not isinstance(url, str):
        raise UrlError(f"{what} must be a string URL")
    if len(url.encode("utf-8")) > MAX_URL_BYTES:
        raise UrlError(f"{what} is over {MAX_URL_BYTES} bytes")
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        raise UrlError(f"{what} must not contain spaces or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UrlError(f"{what} is not a valid URL: {exc}") from None
    if parts.scheme not in ("http", "https"):
        raise UrlError(f"{what} must be an absolute http or https URL")
    if not parts.hostname:
        raise UrlError(f"{what} has no host")
    if "@" in parts.netloc:
        raise UrlError(f"{what} must not carry a user or password")
    if "#" in url:
        raise UrlError(f"{what} must not carry a #fragment")
    effective = port if port is not None else (443 if parts.scheme == "https" else 80)
    return parts, parts.hostname.lower(), effective


def parse_allow(raw: object) -> tuple[AllowEntry, ...]:
    """`[callbacks] allow = ["<url prefix>", ...]` (default: none, loopback only). A malformed
    table or entry, or a host other than loopback reached over plain `http`, stops the load."""
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise ValueError("config.toml [callbacks] must be a table")
    listed = raw.get("allow", [])
    if not isinstance(listed, list):
        raise ValueError("config.toml [callbacks] allow must be a list of URL prefixes")
    entries: list[AllowEntry] = []
    for item in listed:
        try:
            parts, host, port = _split(item, "config.toml [callbacks] allow entry")
        except UrlError as exc:
            raise ValueError(f"{exc}: {item!r}") from None
        if parts.query:
            raise ValueError(f"config.toml [callbacks] allow entry has a query: {item!r}")
        if host not in LOOPBACK_HOSTS and parts.scheme != "https":
            raise ValueError(
                f"config.toml [callbacks] allow entry for a host other than loopback must be "
                f"https: {item!r}"
            )
        entries.append(AllowEntry(parts.scheme, host, port, parts.path or "/"))
    return tuple(entries)


def check_url(url: object, allow: Iterable[AllowEntry], own_ports: Iterable[int]) -> Target:
    """Admission's check of `run(callback=...)`: the split target, or `UrlError` (invalid
    arguments) or `NotAllowed` (this server will not call it). Nothing is looked up."""
    parts, host, port = _split(url, "callback")
    assert isinstance(url, str)
    path = parts.path or "/"
    loopback = host in LOOPBACK_HOSTS
    if not loopback and not any(e.matches(parts.scheme, host, port, path) for e in allow):
        raise NotAllowed(
            f"callback host {host} is not loopback and matches no [callbacks] allow entry "
            "in config.toml"
        )
    if loopback and port in set(own_ports):
        raise NotAllowed(f"callback port {port} is one of Trestle's own ports")
    return target_of(url)


def target_of(url: str) -> Target:
    """A URL admission already checked, split for the sender."""
    parts = urlsplit(url)
    path = parts.path or "/"
    return Target(
        url=url,
        scheme=parts.scheme,
        host=(parts.hostname or "").lower(),
        port=parts.port or (443 if parts.scheme == "https" else 80),
        netloc=parts.netloc.lower(),
        target=path + (f"?{parts.query}" if parts.query else ""),
    )


def origin_of(url: str) -> str:
    """The URL's origin (`scheme://host[:port]`): what every view shows, never the path."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.lower()}"


# -- the files ---------------------------------------------------------------------------------


def pending_dir(home: Path) -> Path:
    return home / "callbacks" / "pending"


def marker_path(home: Path, run_id: str) -> Path:
    return pending_dir(home) / run_id


def callback_path(run_dir: Path) -> Path:
    return run_dir / CALLBACK_FILE


def initial_record(url: str) -> dict[str, Any]:
    return {
        "origin": origin_of(url),
        "status": STATUS_PENDING,
        "attempts": 0,
        "last_http_status": None,
        "last_error": None,
        "delivered_at": None,
        "last_attempt_at": None,
    }


def write_admitted(home: Path, building: Path, run_id: str, url: str) -> None:
    """Admission step (c): the run's `callback.json` (pending) and its pending marker, both before
    the run becomes visible."""
    atomic_write_json(callback_path(building), initial_record(url))
    marker = marker_path(home, run_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def read_record(run_dir: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(callback_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def view_of(run_dir: Path | None) -> dict[str, Any] | None:
    """The `callback` field of RunView and `query(view="run")`: origin and delivery status, None
    for a run sent without a callback. The path and query are never on it."""
    if run_dir is None:
        return None
    record = read_record(run_dir)
    if record is None:
        return None
    return {name: record.get(name) for name in _VIEW_FIELDS}


# -- the body -----------------------------------------------------------------------------------


def _iso(value: object) -> str | None:
    """A ledger timestamp as `YYYY-MM-DDTHH:MM:SSZ`, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def delivery_id(run_id: str) -> str:
    return f"{run_id}:{EVENT}"


def run_fields(run_dir: Path) -> dict[str, Any] | None:
    """The body's `run` object, from the run's finalized evidence; None while it has not ended."""
    from trestle.server import project
    from trestle.server.ledger import RunLedger, evidence_dir, ledger_path

    ledger = RunLedger.open(ledger_path(run_dir))
    state = ledger.terminal_state()
    if state is None or not ledger.has_kind("evidence_finalized"):
        return None
    created = ledger.last_kind("created") or {}
    started = ledger.last_kind("started")
    terminal = ledger.last_kind(state) or {}
    outcome = project._outcome_view(ledger, state, evidence_dir(run_dir))
    answer = project._answer_view(ledger, state, run_dir)
    error = project._error_view(ledger, state)
    key = created.get("idempotency_key")
    return {
        "run_id": created.get("run_id", run_dir.name),
        "plugin": created.get("plugin"),
        "idempotency_key": key if isinstance(key, str) else None,
        "state": state,
        "outcome_class": outcome.get("class") if isinstance(outcome, dict) else None,
        "answer_outcome": answer.get("outcome") if isinstance(answer, dict) else None,
        "error": (
            {"code": error.get("code"), "message": error.get("message")}
            if isinstance(error, dict)
            else None
        ),
        "started_at": _iso(started.get("at")) if started is not None else None,
        "ended_at": _iso(terminal.get("at")),
    }


def body(run: dict[str, Any], attempt: int) -> bytes:
    """The version 1 body, at most 2 KiB: a message that would take it over is cut short."""
    fields = dict(run)
    payload: dict[str, Any] = {
        "type": BODY_TYPE,
        "version": BODY_VERSION,
        "delivery_id": delivery_id(str(run["run_id"])),
        "attempt": attempt,
        "sent_at": _now_iso(),
        "run": fields,
    }
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    error = fields.get("error")
    while len(data) > MAX_BODY_BYTES and isinstance(error, dict) and error.get("message"):
        message = str(error["message"])
        error = {**error, "message": message[: max(0, len(message) - (len(data) - MAX_BODY_BYTES))]}
        fields["error"] = error
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return data


# -- the transport ------------------------------------------------------------------------------


class _Pinned(http.client.HTTPConnection):
    """An HTTP connection to fixed addresses: the `Host` header names the URL's host, the socket
    goes to each address in turn, and no name is resolved."""

    def __init__(self, host: str, port: int, addresses: tuple[str, ...], timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._addresses = addresses

    def _open(self) -> socket.socket:
        last: OSError | None = None
        for address in self._addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect((address, self.port))
            except OSError as exc:
                sock.close()
                last = exc
                continue
            return sock
        raise last if last is not None else OSError(errno.ECONNREFUSED, "connection refused")

    def connect(self) -> None:
        self.sock = self._open()


class _PinnedTLS(_Pinned):
    def connect(self) -> None:
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(self._open(), server_hostname=self.host)


def _connection(target: Target) -> http.client.HTTPConnection:
    if target.host == "localhost":
        addresses = LOCALHOST_ADDRESSES
    elif target.host in ("127.0.0.1", "::1"):
        addresses = (target.host,)
    elif target.scheme == "https":
        return http.client.HTTPSConnection(target.host, target.port, timeout=TIMEOUT_S)
    else:
        return http.client.HTTPConnection(target.host, target.port, timeout=TIMEOUT_S)
    cls = _PinnedTLS if target.scheme == "https" else _Pinned
    return cls(target.host, target.port, addresses, TIMEOUT_S)


def post(
    target: Target, data: bytes, run_id: str, user_agent: str
) -> tuple[int | None, str | None]:
    """One attempt: (HTTP status, None) when the listener answered, (None, error) when it did not.
    Redirects are never followed (http.client does not follow them)."""
    conn = _connection(target)
    headers = {
        "Host": target.netloc,
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "Trestle-Event": EVENT_HEADER,
        "Trestle-Delivery": delivery_id(run_id),
        "Content-Length": str(len(data)),
    }
    try:
        conn.putrequest("POST", target.target, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders(data)
        response = conn.getresponse()
        status = response.status
        with contextlib.suppress(OSError, http.client.HTTPException):
            response.read(4096)  # the body is ignored
        return status, None
    except TimeoutError:
        return None, "timed out"
    except (OSError, http.client.HTTPException) as exc:
        return None, _short_error(exc)
    finally:
        conn.close()


def _short_error(exc: BaseException) -> str:
    text = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
    text = (text or type(exc).__name__).strip().lower()
    return text.encode("utf-8")[:_ERROR_BYTES].decode("utf-8", "ignore")


def retryable(status: int | None) -> bool:
    """A refused connection or a timeout (no status), 408, 429 or 5xx."""
    return status is None or status in (408, 429) or 500 <= status <= 599


def _status_error(status: int) -> str | None:
    if 300 <= status <= 399:
        return f"http {status}: redirect not followed"
    if 200 <= status <= 299:
        return None
    return f"http {status}"


# -- the sender ---------------------------------------------------------------------------------


def _lock_marker(path: Path) -> int | None:
    """The marker's flock, on a descriptor the caller closes; None when the marker is gone or
    another sender holds it. A marker removed while this call waited is not locked."""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(fd).st_ino != os.stat(path).st_ino:
            raise FileNotFoundError(path)
    except OSError:
        os.close(fd)
        return None
    return fd


def _wall() -> float:
    """Wall-clock now, for the 24-hour cut-off (a test moves it)."""
    return time.time()


def _epoch(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


@dataclass
class CallbackSender:
    """Sends the run callbacks of one home: `kick(run_id)` when a run of this server ends, `scan()`
    at start and on each reaper pass. Each send runs on its own daemon thread, holding the run's
    marker lock from the first attempt to the last."""

    home: Path
    user_agent: str = ""
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if not self.user_agent:
            import trestle

            self.user_agent = f"trestle/{trestle.__version__}"
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def kick(self, run_id: str) -> None:
        """Send the run's callback now if it has one, has ended and no one else is sending it."""
        if not marker_path(self.home, run_id).exists():
            return
        with self._lock:
            if run_id in self._active:
                return
            self._active.add(run_id)
            thread = threading.Thread(
                target=self._run, args=(run_id,), name=f"trestle-callback-{run_id}", daemon=True
            )
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
        thread.start()

    def scan(self) -> None:
        """Every pending marker: each whose lock is free and whose run has ended is sent."""
        root = pending_dir(self.home)
        try:
            names = sorted(entry.name for entry in root.iterdir() if not entry.name.startswith("."))
        except OSError:
            return
        for run_id in names:
            self.kick(run_id)

    def join(self, timeout: float | None = None) -> None:
        """Wait for this process's sends in flight (tests, a draining server)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            thread.join(remaining)

    def _run(self, run_id: str) -> None:
        try:
            self.send(run_id)
        except Exception:  # noqa: BLE001 (the marker stays: the next scan tries again)
            pass
        finally:
            with self._lock:
                self._active.discard(run_id)

    def send(self, run_id: str) -> str | None:
        """Deliver one run's callback under its marker lock; the closing status, or None when the
        callback was not this call's to send (locked elsewhere, the run not ended yet, gone)."""
        from trestle.server.recovery import find_run_dir

        marker = marker_path(self.home, run_id)
        fd = _lock_marker(marker)
        if fd is None:
            return None
        try:
            run_dir = find_run_dir(self.home, run_id)
            if run_dir is None:
                with contextlib.suppress(OSError):
                    if time.time() - marker.stat().st_mtime > ORPHAN_AFTER_S:
                        marker.unlink()
                return None
            return self._send_locked(run_id, run_dir, marker)
        finally:
            os.close(fd)

    def _send_locked(self, run_id: str, run_dir: Path, marker: Path) -> str | None:
        from trestle.server.ledger import RunLedger, ledger_path

        record = read_record(run_dir)
        created = RunLedger.open(ledger_path(run_dir)).last_kind("created") or {}
        url = created.get("callback")
        if record is None or not isinstance(url, str):
            marker.unlink(missing_ok=True)  # a run without a callback: nothing to send
            return None
        if record.get("status") in CLOSED_STATUSES:
            marker.unlink(missing_ok=True)
            return str(record["status"])
        run = run_fields(run_dir)
        if run is None:
            return None  # not ended yet: its owner kicks it, else a later scan
        ended = _epoch(run.get("ended_at"))
        if ended is not None and _wall() - ended > ABANDON_AFTER_S:
            return self._close(run_dir, marker, record, STATUS_ABANDONED)
        # checked against the allow list at admission; the list is not consulted again
        target = target_of(url)
        attempts = int(record.get("attempts") or 0)
        while attempts < len(SCHEDULE_S):
            last = _epoch(record.get("last_attempt_at"))
            if attempts > 0 and last is not None:
                gap = SCHEDULE_S[attempts] - SCHEDULE_S[attempts - 1]
                wait = last + gap - time.time()
                if wait > 0:
                    self.sleep(wait)
            attempts += 1
            # the attempt is counted before it is sent, so a crash never sends a sixth
            record = {**record, "attempts": attempts, "last_attempt_at": _now_iso()}
            atomic_write_json(callback_path(run_dir), record)
            status, error = post(target, body(run, attempts), run_id, self.user_agent)
            record = {
                **record,
                "last_http_status": status,
                "last_error": error if status is None else _status_error(status),
            }
            if status is not None and 200 <= status <= 299:
                record["delivered_at"] = _now_iso()
                return self._close(run_dir, marker, record, STATUS_DELIVERED)
            if not retryable(status):
                return self._close(run_dir, marker, record, STATUS_FAILED)
            atomic_write_json(callback_path(run_dir), record)
        return self._close(run_dir, marker, record, STATUS_FAILED)

    @staticmethod
    def _close(run_dir: Path, marker: Path, record: dict[str, Any], status: str) -> str:
        atomic_write_json(callback_path(run_dir), {**record, "status": status})
        marker.unlink(missing_ok=True)
        return status
