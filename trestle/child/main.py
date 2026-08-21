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
from typing import cast

from trestle.child.context import RuntimeContext
from trestle.child.serialize import ResultTooLarge, write_result
from trestle.common.fsutil import atomic_write
from trestle.common.limits import capture_limits
from trestle.common.types import RunSpec
from trestle.plugin.surface import is_trestle_plugin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-child")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    evidence = run_dir / "evidence"
    work = run_dir / "work"
    spec = RunSpec.from_dict(json.loads((evidence / "spec.json").read_text(encoding="utf-8")))

    os.chdir(work)
    os.environ["TMPDIR"] = str(work / "tmp")
    (work / "tmp").mkdir(parents=True, exist_ok=True)

    home = Path(os.environ.get("TRESTLE_HOME", Path.home() / ".trestle"))
    plugin_path = home / "snapshots" / spec.snapshot_id / "plugin.py"
    fn = _load_plugin_callable(plugin_path)
    deadline = datetime.fromisoformat(spec.deadline) if spec.deadline else datetime.now(tz=UTC)
    limits = capture_limits()
    ctx = RuntimeContext(
        work=work,
        evidence=evidence,
        deadline=deadline,
        events_path=evidence / "events.ndjson",
        limits=limits,
    )

    bound_args = _bind_args(fn, spec.args)
    try:
        result = fn(ctx, **bound_args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return code if code is not None else 1
    except Exception:
        return 1

    if result is not None:
        try:
            idx = write_result(
                evidence / "result.json",
                result,
                max_bytes=limits.max_result_bytes,
            )
        except ResultTooLarge:
            atomic_write(evidence / "result.state", b"too_large")
            return 0
        atomic_write(evidence / "result.index", idx.to_json())
    return 0


def _load_plugin_callable(plugin_path: Path) -> Callable[..., object]:
    spec = importlib.util.spec_from_file_location("trestle_plugin_script", plugin_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plugin: {plugin_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["trestle_plugin_script"] = module
    spec.loader.exec_module(module)
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and is_trestle_plugin(obj):
            return cast(Callable[..., object], obj)
    raise RuntimeError(f"no @trestle plugin in {plugin_path}")


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
            bound[name] = args[name]
        elif param.default is inspect.Parameter.empty:
            raise TypeError(f"missing required arg: {name}")
    return bound


if __name__ == "__main__":
    sys.exit(main())
