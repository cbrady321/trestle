"""Child entry point — foundation runtime + script."""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from trestle.child.context import RuntimeContext
from trestle.child.serialize import ResultTooLarge, write_result
from trestle.child.validate import (
    ProvenanceMismatch,
    ValidationFailed,
    check_package_digests,
    select_entry,
)
from trestle.common import codes, redact
from trestle.common.errtext import sanitize
from trestle.common.fsutil import atomic_write, atomic_write_json
from trestle.common.limits import capture_limits
from trestle.common.types import DeclaredMetadata, RunSpec
from trestle.plugin._codec import hydrate

CHILD_ERROR = "child_error.json"
_EXC_TYPE_MAX = 128


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-child")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    evidence = run_dir / "evidence"
    work = run_dir / "work"
    spec = RunSpec.from_dict(json.loads((evidence / "spec.json").read_text(encoding="utf-8")))
    # MC-CORE-13: the run's real secret values arrive in the environment and leave it here, before
    # any plugin code is imported, so nothing the plugin starts inherits them. They are held in
    # memory only: `spec.json` carries the redacted arguments.
    plugin_args = redact.restore_args(spec.args, redact.take_env(os.environ))

    os.chdir(work)
    os.environ["TMPDIR"] = str(work / "tmp")
    (work / "tmp").mkdir(parents=True, exist_ok=True)

    home = Path(os.environ.get("TRESTLE_HOME", Path.home() / ".trestle"))
    roots = {"home": home, "run": run_dir, "cwd": work, "user-home": Path.home()}
    plugin_path = home / "snapshots" / spec.snapshot_id / "plugin.py"
    try:
        # the declared packages are recorded, not snapshotted (R-J): check their digests against
        # publication's before any plugin code is imported
        check_package_digests(spec.provenance["packages"])
    except ProvenanceMismatch as exc:
        return _fail(evidence, "provenance", codes.EXECUTION_PROVENANCE_MISMATCH, exc, roots)
    try:
        fn = _load_plugin_callable(plugin_path)
    except Exception as exc:
        return _fail(evidence, "load", codes.EXECUTION_IMPORT_FAILED, exc, roots)
    deadline = datetime.fromisoformat(spec.deadline) if spec.deadline else datetime.now(tz=UTC)
    limits = capture_limits()
    ctx = RuntimeContext(
        work=work,
        evidence=evidence,
        deadline=deadline,
        events_path=evidence / "events.ndjson",
        limits=limits,
    )

    try:
        bound_args = _bind_args(fn, plugin_args)
    except Exception as exc:
        return _fail(evidence, "bind", codes.EXECUTION_BIND_FAILED, exc, roots)
    try:
        result = fn(ctx, **bound_args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return code if code is not None else 1
    except Exception as exc:
        return _fail(evidence, "call", codes.EXECUTION_PLUGIN_RAISED, exc, roots)

    if result is not None:
        declared_return = _declared_return(fn)
        try:
            idx = write_result(
                evidence / "result.json",
                result,
                max_bytes=limits.max_result_bytes,
                declared_return=declared_return,
            )
        except ResultTooLarge:
            atomic_write(evidence / "result.state", b"too_large")
            return 0
        except Exception as exc:
            return _fail(evidence, "encode", codes.EXECUTION_RESULT_UNENCODABLE, exc, roots)
        atomic_write(evidence / "result.index", idx.to_json())
    return 0


def _fail(evidence: Path, phase: str, code: str, exc: BaseException, roots: dict[str, Path]) -> int:
    """The failure's one record: the atomic `evidence/child_error.json` {code, phase, message,
    exc_type} (MC-15's feeder), its message through the one sanitizer (MC-CORE-14). The child
    exits 1; the file is the whole handoff (DM-01). A failure to write it is not allowed to hide
    the exit code, so the write is best effort."""
    record = {
        "code": code,
        "phase": phase,
        "message": sanitize(str(exc) or type(exc).__name__, roots),
        "exc_type": type(exc).__name__[:_EXC_TYPE_MAX],
    }
    try:
        atomic_write_json(evidence / CHILD_ERROR, record)
    except OSError:
        pass
    return 1


def _load_plugin_callable(plugin_path: Path) -> Callable[..., object]:
    """Load the snapshot's one entry callable: the manifest's `entry`, by name.

    A snapshot whose manifest predates `entry` falls back to its one marked definition."""
    spec = importlib.util.spec_from_file_location("trestle_plugin_script", plugin_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plugin: {plugin_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["trestle_plugin_script"] = module
    spec.loader.exec_module(module)
    try:
        return select_entry(module, _manifest_entry(plugin_path))
    except ValidationFailed as exc:
        raise RuntimeError(f"{exc} in {plugin_path}") from exc


def _manifest_entry(plugin_path: Path) -> str | None:
    manifest_path = plugin_path.with_name("manifest.json")
    if not manifest_path.is_file():
        return None
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    return DeclaredMetadata.from_manifest(loaded).entry if isinstance(loaded, dict) else None


def _bind_args(
    fn: Callable[..., object],
    args: dict[str, object],
) -> dict[str, object]:
    sig = inspect.signature(fn)
    bound: dict[str, object] = {}
    for name, param in sig.parameters.items():
        if name == "ctx":
            continue
        if name in args:
            bound[name] = _hydrate_arg(fn, param, args[name])
        elif param.default is inspect.Parameter.empty:
            raise TypeError(f"missing required arg: {name}")
    return bound


def _hydrate_arg(fn: Callable[..., object], param: inspect.Parameter, value: object) -> object:
    """One admitted JSON value as its annotation (the codec, MC-CORE-08).

    A postponed annotation is evaluated in the plugin module's own namespace;
    one that cannot be evaluated leaves the value as it arrived (today's
    behaviour), since admission has already accepted it.
    """
    annotation = _resolve_annotation(fn, param.annotation)
    if annotation is inspect.Parameter.empty:
        return value
    return hydrate(annotation, value)


def _resolve_annotation(fn: Callable[..., object], annotation: object) -> object:
    """A parameter or return annotation as an object; `inspect.Parameter.empty` when it is absent
    or a postponed one that cannot be evaluated in the plugin module's namespace."""
    if isinstance(annotation, str):
        try:
            return eval(annotation, getattr(fn, "__globals__", {}))  # noqa: S307
        except Exception:
            return inspect.Parameter.empty
    return annotation


def _declared_return(fn: Callable[..., object]) -> object:
    """The entry point's return annotation for the encoder (None when absent or unresolvable):
    only a set that annotation names is written as an array (WR-EVID-5)."""
    annotation = _resolve_annotation(fn, inspect.signature(fn).return_annotation)
    return None if annotation is inspect.Parameter.empty else annotation


if __name__ == "__main__":
    sys.exit(main())
