"""CL-B2 (WR-EVID-8): a declared secret is never persisted (MC-CORE-13).

L.CL-B2.1: admission writes `spec.json` with the declared arguments redacted and delivers the real
values to the plugin in memory; the sentinel is absent from the spec, the ledger and the
idempotency store. L.CL-B2.2: the write-path scrub keeps the sentinel out of every file the run
writes (events, console, result, child_error, artifacts, the work directory) and out of the MCP
answer. L.CL-B2.3 adds the host-path scan: the same scrub replaces TRESTLE_HOME, the run directory,
the work directory and $HOME by fixed tokens in what a plugin writes, so no answer, view row or
fetch carries a host path.

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.proof import harness, mcp_host, tolerances
from trestle.common import codes, redact
from trestle.common.canonical import args_hash
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.ledger import run_dir_for
from trestle.server.main import Kernel

SENTINEL = "SENTINEL-clb2-tok-7f3a91c2d4"
NESTED_SENTINEL = "SENTINEL-clb2-pw-52e8b0a6"

# A plugin that declares two secrets (one a dotted path into a dict argument), and reports what it
# received as digests, so the test can prove the real values arrived without printing them.
ECHO_SOURCE = """\
import hashlib
import os
import subprocess
import sys

from trestle.plugin.surface import Context, trestle


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@trestle(secrets=["token", "auth.password"])
def echo_secret(ctx: Context, token: str, auth: dict[str, str], note: str = "") -> dict[str, str]:
    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('TRESTLE_RUN_SECRETS', 'absent'))"],
        capture_output=True, text=True, check=True,
    )
    return {
        "token_digest": _digest(token),
        "password_digest": _digest(auth["password"]),
        "user": auth["user"],
        "note": note,
        "env_after_call": os.environ.get("TRESTLE_RUN_SECRETS", "absent"),
        "env_in_subprocess": child.stdout.strip(),
    }
"""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def kernel_with(tmp_path: Path, source: str, name: str) -> Kernel:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(exist_ok=True)
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    published = kernel.control.publish_plugin(source)
    assert isinstance(published, PublishView), published
    assert published.name == name
    return kernel


def run_ok(kernel: Kernel, plugin: str, args: dict[str, Any], **kw: Any) -> RunView:
    view = kernel.control.run(plugin=plugin, args=args, wait_ms=tolerances.HARNESS_WAIT_MS, **kw)
    assert isinstance(view, RunView), view
    return view


def occurrences(root: Path, needle: str) -> list[str]:
    """Every file under `root` (relative names) whose bytes contain `needle`."""
    raw = needle.encode("utf-8")
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and raw in path.read_bytes()
    )


REAL_ARGS = {
    "token": SENTINEL,
    "auth": {"user": "alice", "password": NESTED_SENTINEL},
    "note": "visible",
}


@pytest.mark.proves(
    "WR-EVID-8",
    "WR-EVID-8:secret-absent-spec-ledger-store",
    "core",
    "core",
    "PROC",
    "CI",
)
def test_declared_secret_not_in_spec_ledger_or_store(tmp_path: Path) -> None:
    kernel = kernel_with(tmp_path, ECHO_SOURCE, "echo_secret")
    view = run_ok(kernel, "echo_secret", REAL_ARGS, idempotency_key="clb2-store-key")
    assert view.state == "succeeded", view
    run_dir = run_dir_for(kernel.home, view.run_id)

    # the plugin received the real values (only their digests are echoed) ...
    result = json.loads((run_dir / "evidence" / "result.json").read_text(encoding="utf-8"))
    assert result["token_digest"] == digest(SENTINEL)
    assert result["password_digest"] == digest(NESTED_SENTINEL)
    assert result["user"] == "alice" and result["note"] == "visible"
    # ... and the environment that carried them is gone before the call, and from its children
    assert result["env_after_call"] == "absent"
    assert result["env_in_subprocess"] == "absent"

    # spec.json carries the redacted arguments, every other argument untouched
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert spec["args"] == {
        "token": redact.REDACTED,
        "auth": {"user": "alice", "password": redact.REDACTED},
        "note": "visible",
    }
    for needle in (SENTINEL, NESTED_SENTINEL):
        assert (run_dir / "evidence" / "spec.json").read_bytes().count(needle.encode()) == 0
        assert (run_dir / "evidence" / "ledger.ndjson").read_bytes().count(needle.encode()) == 0
        store = kernel.home / "idempotency.json"
        assert store.is_file()
        assert store.read_bytes().count(needle.encode()) == 0
        # the whole run directory and the home's index files, the result's digests aside
        assert occurrences(run_dir, needle) == []
        assert occurrences(kernel.home, needle) == []


def test_args_hash_is_over_the_real_intent(tmp_path: Path) -> None:
    """Join semantics survive redaction: the hash is of what the caller asked for, so the same
    key with the same secret joins the run and a different secret is a conflict."""
    kernel = kernel_with(tmp_path, ECHO_SOURCE, "echo_secret")
    first = run_ok(kernel, "echo_secret", REAL_ARGS, idempotency_key="clb2-join-key")
    run_dir = run_dir_for(kernel.home, first.run_id)
    created = [
        json.loads(line)
        for line in (run_dir / "evidence" / "ledger.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
        if '"created"' in line
    ][0]
    assert created["args_hash"] == args_hash(REAL_ARGS)
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert spec["args_hash"] == args_hash(REAL_ARGS) != args_hash(spec["args"])

    joined = run_ok(kernel, "echo_secret", REAL_ARGS, idempotency_key="clb2-join-key")
    assert joined.run_id == first.run_id

    other = {**REAL_ARGS, "token": SENTINEL + "-other"}
    refused = kernel.control.run(
        plugin="echo_secret",
        args=other,
        wait_ms=tolerances.HARNESS_WAIT_MS,
        idempotency_key="clb2-join-key",
    )
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.IDEMPOTENCY_KEY_CONFLICT
    assert SENTINEL not in refused.message


def test_redact_args_and_values_shapes() -> None:
    args = {"a": "x", "b": {"c": ["p", "q"], "d": 1}, "e.f": "dotted-key"}
    declared = ["b.c", "e.f", "missing", "a"]
    assert redact.redact_args(args, declared) == {
        "a": redact.REDACTED,
        "b": {"c": redact.REDACTED, "d": 1},
        "e.f": redact.REDACTED,
    }
    assert args["a"] == "x"  # the input is never modified
    values = redact.secret_values(args, declared)
    assert values == {"a": "x", "b.c": ["p", "q"], "e.f": "dotted-key"}
    assert redact.restore_args(redact.redact_args(args, declared), values) == args
    assert redact.secret_strings(values) == {"x", "p", "q", "dotted-key"}
    # no declaration: the arguments come back equal
    assert redact.redact_args(args, []) == args


# --- L.CL-B2.2: the write-path scrub ----------------------------------------------------------

# A plugin that puts its secret on every path a plugin can write: its log and progress events, its
# stdout and stderr (and a child process's), a scratch file, an attachment (text, and with `mode`
# a binary), an outputs file, an exception message and its return (a leaf and a key).
LEAKER_SOURCE = """\
import hashlib
import subprocess
import sys

from trestle.plugin.surface import Context, trestle


@trestle(secrets=["token"])
def leaker(ctx: Context, token: str, mode: str = "ok") -> dict[str, list[str]]:
    ctx.log(f"log {token}")
    ctx.progress(f"progress {token}")
    print(f"stdout {token}", flush=True)
    print(f"stderr {token}", file=sys.stderr, flush=True)
    subprocess.run(
        [sys.executable, "-c", "import sys; print('child ' + sys.argv[1], flush=True)", token],
        check=True,
    )
    (ctx.tmp / "scratch.txt").write_text(f"scratch {token}", encoding="utf-8")
    text = ctx.tmp / "attached.txt"
    text.write_text(f"attached {token}", encoding="utf-8")
    ctx.attach(text, name=f"attached-{token}.txt")
    refused = "no"
    if mode == "binary_attach":
        blob = ctx.tmp / "blob.bin"
        blob.write_bytes(b"\\x00\\x01" + token.encode() + b"\\x02")
        try:
            ctx.attach(blob, name="blob.bin")
        except ValueError:
            refused = "yes"
    if mode == "binary_output":
        (ctx.outputs / "blob.bin").write_bytes(b"\\x00\\x01" + token.encode() + b"\\x02")
    (ctx.outputs / "report.txt").write_text(f"report {token}", encoding="utf-8")
    if mode == "raise":
        raise RuntimeError(f"failed with {token}")
    digest = hashlib.sha256(token.encode()).hexdigest()
    return {"echo": [token], f"key-{token}": [digest], "refused": [refused]}
"""


def _events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "evidence" / "events.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _ledger_rows(run_dir: Path, kind: str) -> list[dict[str, Any]]:
    path = run_dir / "evidence" / "ledger.ndjson"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [row for row in rows if row.get("kind") == kind]


def _run_leaker(kernel: Kernel, mode: str) -> tuple[RunView, Path]:
    view = run_ok(kernel, "leaker", {"token": SENTINEL, "mode": mode})
    return view, run_dir_for(kernel.home, view.run_id)


@pytest.mark.proves(
    "WR-EVID-8",
    "WR-EVID-8:secret-absent-everywhere",
    "core",
    "core",
    "PROC+MCP",
    "CI",
)
def test_sentinel_absent_everywhere(tmp_path: Path) -> None:
    kernel = kernel_with(tmp_path, LEAKER_SOURCE, "leaker")
    runs: dict[str, tuple[RunView, Path]] = {}
    for mode in ("ok", "raise", "binary_attach", "binary_output"):
        runs[mode] = _run_leaker(kernel, mode)

    # every mode: 0 occurrences in the whole run directory and in the run's answer
    for mode, (view, run_dir) in runs.items():
        assert occurrences(run_dir, SENTINEL) == [], (mode, occurrences(run_dir, SENTINEL))
        assert SENTINEL not in json.dumps(view.to_dict()), mode
    assert occurrences(kernel.home, SENTINEL) == []

    ok_view, ok_dir = runs["ok"]
    assert ok_view.state == "succeeded"
    console = (ok_dir / "evidence" / "console" / "stdout.log").read_text(encoding="utf-8")
    assert f"stdout {redact.REDACTED}" in console and f"child {redact.REDACTED}" in console
    stderr = (ok_dir / "evidence" / "console" / "stderr.log").read_text(encoding="utf-8")
    assert f"stderr {redact.REDACTED}" in stderr
    messages = [
        e["payload"]["message"] for e in _events(ok_dir) if "message" in e.get("payload", {})
    ]
    assert f"log {redact.REDACTED}" in messages and f"progress {redact.REDACTED}" in messages
    result = json.loads((ok_dir / "evidence" / "result.json").read_text(encoding="utf-8"))
    assert result["echo"] == [redact.REDACTED]
    # the plugin did receive the real value: the digest it computed is of the sentinel
    assert result[f"key-{redact.REDACTED}"] == [digest(SENTINEL)]
    artifacts = sorted((ok_dir / "evidence" / "artifacts").iterdir())
    bodies = sorted(p.read_text(encoding="utf-8") for p in artifacts)
    assert bodies == [f"attached {redact.REDACTED}", f"report {redact.REDACTED}"]
    names = [row["name"] for row in _ledger_rows(ok_dir, "artifact_available")]
    assert "report.txt" in names
    assert (ok_dir / "work" / "tmp" / "scratch.txt").read_text() == f"scratch {redact.REDACTED}"

    # a failing run: the message reaches the ledger, meta.json and the answer scrubbed
    raise_view, raise_dir = runs["raise"]
    assert raise_view.state == "failed"
    assert raise_view.error is not None
    assert raise_view.error["message"] == f"failed with {redact.REDACTED}"
    child_error = json.loads((raise_dir / "evidence" / "child_error.json").read_text("utf-8"))
    assert child_error["message"] == f"failed with {redact.REDACTED}"
    assert _ledger_rows(raise_dir, "error_record")[0]["message"] == child_error["message"]

    # a binary that holds the secret is refused, never written, and marked
    attach_view, attach_dir = runs["binary_attach"]
    assert json.loads((attach_dir / "evidence" / "result.json").read_text("utf-8"))["refused"] == [
        "yes"
    ]
    limits = (attach_dir / "evidence" / "capture_limits.ndjson").read_text(encoding="utf-8")
    assert redact.BINARY_LIMIT in limits
    output_view, output_dir = runs["binary_output"]
    limit_rows = _ledger_rows(output_dir, "limit_exceeded")
    assert [m["limit"] for m in limit_rows[0]["markers"]] == [redact.BINARY_LIMIT]
    assert (output_dir / "work" / "outputs" / "blob.bin").read_bytes() == redact.REFUSED_BINARY
    promoted = [row["name"] for row in _ledger_rows(output_dir, "artifact_available")]
    assert "blob.bin" not in promoted and "report.txt" in promoted

    # over MCP: the run answer and the host's whole home hold no occurrence either
    plugins = tmp_path / "mcp-home" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "leaker.py").write_text(LEAKER_SOURCE, encoding="utf-8")
    with mcp_host.McpHost(home=tmp_path / "mcp-home") as host:
        answer = host.call(
            "run",
            {
                "plugin": "leaker",
                "args": {"token": SENTINEL, "mode": "raise"},
                "completion": "terminal",
                "wait_ms": tolerances.HARNESS_WAIT_MS,
            },
        )
        assert answer["state"] == "failed", answer
        assert SENTINEL not in json.dumps(answer)
        run_id = answer["run_id"]
        for view in ("run", "last_error", "run_tail", "run_events", "run_provenance"):
            rows = host.call("query", {"view": view, "params": {"run_id": run_id}})
            assert SENTINEL not in json.dumps(rows), view
        home = host.home
    assert occurrences(home, SENTINEL) == []


def test_console_scrub_joins_chunks_and_trims_a_capped_tail(tmp_path: Path) -> None:
    from trestle.wrapper.reactor import _write_console

    scrubber = redact.Scrubber(secrets=frozenset({SENTINEL}))
    split = [f"a {SENTINEL[:9]}", f"{SENTINEL[9:]} b"]
    path = tmp_path / "stdout.log"
    _write_console(path, split, 10_000, scrubber)
    assert path.read_text(encoding="utf-8") == f"a {redact.REDACTED} b"
    # a capture cut inside the secret leaves no half of it at the end
    _write_console(path, [f"tail {SENTINEL[:11]}"], 10_000, scrubber, capped=True)
    assert path.read_text(encoding="utf-8") == f"tail {redact.REDACTED}"
    # without a cap, a partial secret is ordinary text
    _write_console(path, [f"tail {SENTINEL[:11]}"], 10_000, scrubber)
    assert path.read_text(encoding="utf-8") == f"tail {SENTINEL[:11]}"


def test_scrub_forms_and_files(tmp_path: Path) -> None:
    secret = 'pa"ss\\wörd'
    escaped = json.dumps(secret)[1:-1]
    text = f"plain {secret} json {escaped} end"
    assert redact.scrub(text, {secret}) == f"plain {redact.REDACTED} json {redact.REDACTED} end"
    assert redact.scrub(text.encode(), {secret}) == redact.scrub(text, {secret}).encode()
    assert redact.scrub(text, set()) == text
    assert redact.scrub_json({"k-" + secret: [secret, 1, None]}, {secret}) == {
        f"k-{redact.REDACTED}": [redact.REDACTED, 1, None]
    }
    assert redact.holds_secret(b"\x00" + secret.encode(), {secret})
    assert not redact.holds_secret(b"\x00 nothing", {secret})
    # files: text is rewritten, a clean binary is untouched, a holding binary is refused
    (tmp_path / "t.txt").write_text(f"x {secret}", encoding="utf-8")
    (tmp_path / "clean.bin").write_bytes(b"\x00\x01clean")
    (tmp_path / "held.bin").write_bytes(b"\x00" + secret.encode())
    assert redact.scrub_tree(tmp_path, {secret}) == 1
    assert (tmp_path / "t.txt").read_text(encoding="utf-8") == f"x {redact.REDACTED}"
    assert (tmp_path / "clean.bin").read_bytes() == b"\x00\x01clean"
    assert (tmp_path / "held.bin").read_bytes() == redact.REFUSED_BINARY


# --- L.CL-B2.3: handles, never host paths -----------------------------------------------------

# A plugin that puts every host path it can name on every channel it writes: its log and progress
# events, its stdout and stderr, an outputs file, an attachment, an exception message and its
# return. Nothing here is a secret; a host path is not one, but an agent has no use for it.
PATHY_SOURCE = """\
import os
import sys
from pathlib import Path

from trestle.plugin.surface import Context, trestle


@trestle
def pathy(ctx: Context, mode: str = "ok") -> dict[str, str]:
    home = os.environ["TRESTLE_HOME"]
    cwd = os.getcwd()
    user = str(Path.home())
    line = f"home={home} tmp={ctx.tmp} cwd={cwd} user={user}/x"
    ctx.log(line)
    ctx.progress(line)
    print(line, flush=True)
    print(line, file=sys.stderr, flush=True)
    report = ctx.outputs / "where.txt"
    report.write_text(line, encoding="utf-8")
    ctx.attach(report, name="where-attached.txt")
    if mode == "raise":
        raise RuntimeError(line)
    return {"home": home, "cwd": cwd, "tmp": str(ctx.tmp), "user": f"{user}/x"}
"""

VIEWS = (
    "run",
    "last_error",
    "run_tail",
    "run_events",
    "recent_runs",
    "recent_failures",
    "run_provenance",
    "run_artifacts",
    "artifact_refs",
)


def _host_paths(home: Path, run_dir: Path) -> list[str]:
    """The strings a run's answer must never carry: TRESTLE_HOME, the run and work directories,
    the process's working directory and $HOME, each as given and resolved."""
    paths = {home, run_dir, run_dir / "work", Path.cwd(), Path.home()}
    return sorted({str(p) for p in paths} | {str(p.resolve()) for p in paths})


@pytest.mark.proves(
    "WR-EVID-8",
    "WR-EVID-8:no-host-path-in-answer",
    "core",
    "core",
    "MCP",
    "CI",
)
def test_no_host_path_in_run_answers_views_fetch(tmp_path: Path) -> None:
    plugins = tmp_path / "home" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "pathy.py").write_text(PATHY_SOURCE, encoding="utf-8")
    with mcp_host.McpHost(home=tmp_path / "home") as host:
        answers: dict[str, Any] = {}
        run_ids: dict[str, str] = {}
        for mode in ("ok", "raise"):
            answers[f"run-{mode}"] = host.call(
                "run",
                {
                    "plugin": "pathy",
                    "args": {"mode": mode},
                    "completion": "terminal",
                    "wait_ms": tolerances.HARNESS_WAIT_MS,
                },
            )
            run_ids[mode] = answers[f"run-{mode}"]["run_id"]
        assert answers["run-ok"]["state"] == "succeeded", answers["run-ok"]
        assert answers["run-raise"]["state"] == "failed", answers["run-raise"]

        for mode, run_id in run_ids.items():
            for view in VIEWS:
                params = {"run_id": run_id} if view in RUN_SCOPED else {}
                answers[f"{view}-{mode}"] = host.call("query", {"view": view, "params": params})
            artifact_rows = answers[f"run_artifacts-{mode}"]["items"]
            handles = [f"{run_id}/result"] + [row["artifact_id"] for row in artifact_rows]
            for handle in handles:
                windows = (
                    [{"kind": "head", "count": 50}, {"kind": "jsonpath", "expr": "$"}]
                    if handle.endswith("/result")
                    else [{"kind": "head", "count": 50}, {"kind": "grep", "pattern": "="}]
                )
                for window in windows:
                    key = f"fetch-{handle}-{window['kind']}"
                    answers[key] = host.call("fetch", {"target": handle, "window": window})
        home = host.home
        run_dir = next(iter((home / "runs").glob("*/r_*")))

    forbidden = _host_paths(home, run_dir)
    for name, answer in answers.items():
        text = json.dumps(answer)
        leaked = [path for path in forbidden if path in text]
        assert leaked == [], (name, leaked, text[:400])
    # the answers still say something: the tokens stand where the paths were
    assert "<home>" in json.dumps(answers["run_tail-ok"]), answers["run_tail-ok"]
    ok_result = answers[f"fetch-{run_ids['ok']}/result-head"]
    assert "<cwd>" in json.dumps(ok_result), ok_result
    assert answers["last_error-raise"]["items"], answers["last_error-raise"]
    artifact_id = answers["run_artifacts-ok"]["items"][0]["artifact_id"]
    assert "<home>" in json.dumps(answers[f"fetch-{artifact_id}-head"])
    assert os.path.isdir(run_dir)


RUN_SCOPED = frozenset(
    {"run", "last_error", "run_tail", "run_events", "run_provenance", "run_artifacts"}
)
