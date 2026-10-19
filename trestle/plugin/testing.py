"""Unit-test a plugin without a server (R3): `FakeContext`, `call_plugin` and a pytest fixture.

`FakeContext` implements the plugin `Context` surface (`log`, `progress`, `event`, `artifact`,
`attach`, `exec`, `remaining`, `tmp`, `outputs`, `deadline`, `cancelled`) and records what the
plugin did, with no run id, run directory or evidence. `call_plugin` calls a `@trestle` function
the way the child process does: arguments hydrated from their JSON form by the parameter
annotations, and the return value encoded to the JSON an agent would read.

    from trestle.plugin.testing import FakeContext, call_plugin

    def test_open_pr(tmp_path):
        ctx = FakeContext(tmp_path, exec_handler=lambda argv, **kw: exec_result(argv, stdout="{}"))
        assert call_plugin(open_pr.main, {"repo": "x"}, ctx=ctx) == {"opened": True}

With pytest, `pytest_plugins = ["trestle.plugin.testing"]` in a `conftest.py` provides the
`trestle_ctx` fixture: a `FakeContext` under the test's `tmp_path`.

This module is for tests. It is not part of what a published plugin may import: publication
admits only `trestle.plugin`'s exports.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trestle.child import execproc
from trestle.child.context import RESERVED_EVENT_KINDS
from trestle.child.main import _bind_args, _declared_return
from trestle.plugin._codec import encode
from trestle.plugin.surface import ExecResult, is_trestle_plugin

ExecHandler = Callable[..., ExecResult]


def exec_result(
    argv: Sequence[str],
    *,
    returncode: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    cancelled: bool = False,
) -> ExecResult:
    """A scripted `ExecResult`, for an `exec_handler` that stands in for a real command."""
    return ExecResult(
        argv=tuple(argv),
        returncode=None if (timed_out or cancelled) else returncode,
        timed_out=timed_out,
        cancelled=cancelled,
        duration_s=0.0,
        stdout=stdout,
        stderr=stderr,
        output_bytes=len(stdout.encode()) + len(stderr.encode()),
        truncated=False,
        artifact="",
    )


@dataclass
class ExecCall:
    """One `exec` call the plugin made."""

    argv: tuple[str, ...]
    kwargs: dict[str, Any]
    result: ExecResult


@dataclass
class Attachment:
    """One `attach` call: the artifact id, the name, the bytes stored and the id replaced."""

    artifact_id: str
    name: str
    data: bytes
    supersedes: str | None


class FakeContext:
    """The plugin `Context`, recording instead of writing evidence.

    `exec` runs the command for real, bounded as at run time, unless an `exec_handler` is given:
    it is then called as `exec_handler(argv, **kwargs)` and its `ExecResult` is returned, so a
    test can script `gh` or `aws` without the tool. Set `cancelled = True` to stop the run.
    """

    def __init__(
        self,
        root: Path,
        *,
        deadline: datetime | timedelta | float = 300.0,
        exec_handler: ExecHandler | None = None,
    ) -> None:
        self._work = Path(root) / "work"
        self.tmp = self._work / "tmp"
        self.outputs = self._work / "outputs"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.outputs.mkdir(parents=True, exist_ok=True)
        if isinstance(deadline, datetime):
            self.deadline = deadline
        else:
            span = deadline if isinstance(deadline, timedelta) else timedelta(seconds=deadline)
            self.deadline = datetime.now(UTC) + span
        self.cancelled = False
        self.exec_handler = exec_handler
        self.logs: list[str] = []
        self.progress_updates: list[tuple[str, float | None]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.attachments: list[Attachment] = []
        self.exec_calls: list[ExecCall] = []
        self._lock = threading.RLock()

    @property
    def artifacts(self) -> dict[str, bytes]:
        """Each attached name's current bytes (the last attach under that name)."""
        with self._lock:
            return {a.name: a.data for a in self.attachments}

    def log(self, message: str) -> None:
        with self._lock:
            self.logs.append(message)

    def progress(self, message: str, *, fraction: float | None = None) -> None:
        with self._lock:
            self.progress_updates.append((message, fraction))

    def event(self, kind: str, **fields: Any) -> None:
        if not isinstance(kind, str) or not kind:
            raise ValueError("event kind must be a non-empty string")
        if kind in RESERVED_EVENT_KINDS:
            raise ValueError(f"event kind {kind!r} is reserved")
        json.dumps(fields)  # what the runtime could not record, the fake refuses too
        with self._lock:
            self.events.append((kind, dict(fields)))

    def artifact(self, name: str) -> Path:
        if ".." in name or name.startswith("/"):
            raise ValueError("artifact name traversal rejected")
        staging = self._work / "artifact-staging" / f"{name}.partial"
        staging.parent.mkdir(parents=True, exist_ok=True)
        return staging

    def attach(self, path: Path, *, name: str) -> str:
        if not Path(path).is_relative_to(self._work):
            raise ValueError("attach path must be under work/")
        data = Path(path).read_bytes()
        with self._lock:
            supersedes = next(
                (a.artifact_id for a in reversed(self.attachments) if a.name == name), None
            )
            art_id = f"art_fake_{len(self.attachments) + 1}"
            self.attachments.append(Attachment(art_id, name, data, supersedes))
        if Path(path).is_relative_to(self._work / "artifact-staging"):
            Path(path).unlink(missing_ok=True)
        return art_id

    def remaining(self) -> float:
        deadline = self.deadline if self.deadline.tzinfo else self.deadline.replace(tzinfo=UTC)
        return max(0.0, (deadline - datetime.now(UTC)).total_seconds())

    def exec(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | str | None = None,
        env: Mapping[str, str | None] | None = None,
        input: str | bytes | None = None,
        output: str | None = "exec.log",
        max_output_bytes: int = execproc.DEFAULT_MAX_OUTPUT_BYTES,
        credentials: Sequence[str] | None = None,
    ) -> ExecResult:
        if credentials is not None:
            raise NotImplementedError(
                "exec(credentials=...) is reserved until credential grants exist"
            )
        if isinstance(argv, (str, bytes)) or not argv or not all(isinstance(a, str) for a in argv):
            raise TypeError("exec takes argv: a non-empty list of strings, never a shell string")
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "cwd": cwd,
            "env": env,
            "input": input,
            "output": output,
            "max_output_bytes": max_output_bytes,
        }
        kept = b""
        if self.exec_handler is not None:
            result = self.exec_handler(list(argv), **kwargs)
        else:
            left = self.remaining()
            limit = left if timeout is None else min(float(timeout), left)
            result, kept = execproc.run(
                argv,
                timeout=limit,
                cwd=cwd,
                env=env,
                input=input,
                max_output_bytes=max_output_bytes,
                stopped=lambda: self.cancelled,
            )
        if output is not None and kept:
            staged = self.tmp / f"exec-{len(self.exec_calls)}.out"
            staged.write_bytes(kept)
            handle = self.attach(staged, name=output)
            staged.unlink(missing_ok=True)
            result = replace(result, artifact=handle)
        if result.timed_out:
            status = "timed out"
        elif result.cancelled:
            status = "stopped with the run"
        else:
            status = f"exit {result.returncode}"
        self.log(f"exec {Path(argv[0]).name}: {status} in {result.duration_s}s")
        with self._lock:
            self.exec_calls.append(ExecCall(tuple(argv), kwargs, result))
        return result


def call_plugin(
    fn: Callable[..., object],
    args: Mapping[str, object] | None = None,
    *,
    ctx: FakeContext,
) -> object:
    """Call the `@trestle` function `fn` with `ctx` and `args` (their JSON form, as an agent
    sends them) the way the child process does, and return its result as the JSON value the run
    records (None when it returns nothing). A missing required argument raises TypeError; an
    unencodable result raises the encoder's error."""
    if not is_trestle_plugin(fn):
        raise TypeError(f"{getattr(fn, '__name__', fn)!r} is not marked @trestle")
    result = fn(ctx, **_bind_args(fn, dict(args or {})))
    if result is None:
        return None
    return json.loads(encode(result, _declared_return(fn)))


__all__ = ["Attachment", "ExecCall", "FakeContext", "call_plugin", "exec_result"]

try:  # the fixture needs pytest; the rest of this module does not
    import pytest
except ImportError:  # pragma: no cover - pytest is how this module is meant to be used
    pass
else:

    @pytest.fixture
    def trestle_ctx(tmp_path: Path) -> FakeContext:
        """A `FakeContext` under the test's `tmp_path`, with the default 300 s deadline."""
        return FakeContext(tmp_path)

    __all__ += ["trestle_ctx"]
