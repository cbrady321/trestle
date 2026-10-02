"""Producers of Slice B's fossil band `slice-b` (L.RB-12.7; MC-11, MC-B3-05, CM-5; DM-50).

`python -m tests.proof.fossils generate --checkpoint slice-b --states all` runs the producers that
`tests/fixtures/fossils/slice-b/MANIFEST.toml` names (L.J-SLICE-B.1 only runs the generator; the
producers are finished here because the checkpoint lane's globs cover fossils and reviews only).
The states are roots of the REFERENCE workflow (`trestle_env`'s `reference_env` plugin, unmodified)
on the twins' fake binding (`twin.fake_binding`: a fake Docker engine in the plugin's own
process), written by head through the host's own admission (`ControlSurface.run`), so each spec
carries a `plan` that names the declared reference tree, which no reader before B has seen (a root
of the Slice B tree has an environment key, `ArgBinding`s and identifier sets):

- `b-terminal-reference_env`: a finalized root: both backends ready, the run passes;
- `b-views-reference_env`: its own real run of the same plugin; the expectation is its child views;
- `b-inflight-reference_env_postgres_wait`: a non-terminal root, copied while the supporting service
  is ready and the Postgres readiness (the fake presents a planted wrong password) is polling; the
  live run is then cancelled and reaped: the crash image of a server killed at that moment.

State ids are prefixed `b-` and unique across every MANIFEST (MC-11). The helpers of the tree-lift
producers (`tests.tree.gen_fossils`: waits, polling, reaping, tidy) are reused as they are. Every
timing bound is the harness's or the published clock's (SA-05).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import harness, records, tolerances
from tests.tree import gen_fossils as gf
from trestle.common import clock
from trestle.common.types import RequestOutcome, RunView
from trestle.server.main import Kernel

ROOT = Path(__file__).resolve().parents[3]
ENV_PACKAGE = ROOT / "packages" / "trestle-env"
PLUGIN = ENV_PACKAGE / "trestle_env" / "plugins" / "reference_env.py"
PLUGIN_NAME = "reference_env"
ARGS = {"env": "dev"}
INFLIGHT_LEAVES = 2  # the two backends create a container each
# the twins' fake binding seams (`twin.fake_binding.SEAM`, `.WRONG_PASSWORD_SEAM`; the `twin`
# package is the env tests', importable by the plugin processes only, so the spellings are pinned by
# `test_fossil_manifest.py` instead of imported)
HEALTHY_SEAM = "twin.fake_binding:fake_ports"
WRONG_PASSWORD_SEAM = "twin.fake_binding:wrong_password_ports"


@contextmanager
def _fake_binding(seam: str) -> Iterator[None]:
    """The plugin processes bind the fake engine `seam` names and see this checkout's packages."""
    sys_path = [
        str(ROOT),
        str(ROOT / "packages" / "trestle-packs"),
        str(ENV_PACKAGE),
        str(ENV_PACKAGE / "tests"),
    ]
    changes: Mapping[str, str | None] = {
        "TRESTLE_ENV_PORTS": seam,
        "TRESTLE_ENV_FAKE_STATE": None,
        "TRESTLE_DOCKER_PATH": None,
        "PYTHONPATH": os.pathsep.join(
            [*sys_path, *([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])]
        ),
    }
    saved = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _kernel(home: Path) -> Kernel:
    """A kernel over `home` whose only plugin is the reference plugin."""
    plugin_dir = Path(tempfile.mkdtemp(prefix="fossil-plugin-"))
    shutil.copy(PLUGIN, plugin_dir / PLUGIN.name)
    return harness.fresh_kernel(plugin_dirs=[plugin_dir], home=home)


def produce_b_terminal_reference_env(home: Path) -> None:
    """A finalized reference root: both backends ready on the fake engine, the run passes."""
    with _fake_binding(HEALTHY_SEAM):
        kernel = _kernel(home)
        with gf._reaping(home):  # noqa: SLF001
            view = kernel.control.run(
                plugin=PLUGIN_NAME,
                args=dict(ARGS),
                wait_ms=gf.TERMINAL_WAIT_MS,
                completion="terminal",
            )
            assert not isinstance(view, RequestOutcome), view
            assert isinstance(view, RunView), view
    (run_dir,) = gf._run_dirs(home)  # noqa: SLF001
    assert gf._terminal_of(run_dir) == "succeeded", gf._terminal_of(run_dir)  # noqa: SLF001
    gf._plan_bearing_tree(run_dir)  # noqa: SLF001
    gf._tidy(home)  # noqa: SLF001


def produce_b_views_reference_env(home: Path) -> None:
    """The same finalized root as `b-terminal-reference_env` (its own real run), whose expectation
    is its child views."""
    produce_b_terminal_reference_env(home)


def produce_b_inflight_reference_env_postgres_wait(home: Path) -> None:
    """A non-terminal reference root: copied while the supporting service is ready and the Postgres
    readiness (a planted wrong password: it never holds) is polling; the copy has no terminal
    row."""
    scratch = Path(tempfile.mkdtemp(prefix="fossil-inflight-"))
    views: list[Any] = []
    with _fake_binding(WRONG_PASSWORD_SEAM):
        kernel = _kernel(scratch)

        def drive() -> None:
            views.append(
                kernel.control.run(
                    plugin=PLUGIN_NAME,
                    args=dict(ARGS),
                    wait_ms=gf.TERMINAL_WAIT_MS,
                    completion="terminal",
                )
            )

        conductor = threading.Thread(target=drive)
        conductor.start()
        try:
            with gf._reaping(scratch), gf._fast_group_stop():  # noqa: SLF001
                gf._wait_until(lambda: bool(gf._run_dirs(scratch)), "the admission")  # noqa: SLF001
                (live,) = gf._run_dirs(scratch)  # noqa: SLF001
                gf._wait_until(gf.Polling(live, INFLIGHT_LEAVES), "the readiness polls")  # noqa: SLF001
                home.mkdir(parents=True, exist_ok=True)
                for name in gf.HOME_ENTRIES:
                    source = scratch / name
                    if source.is_dir():
                        shutil.copytree(
                            source, home / name, ignore=shutil.ignore_patterns("__pycache__")
                        )
                    elif source.is_file():
                        shutil.copy(source, home / name)
                kernel.control.cancel(live.name)
                conductor.join(timeout=clock.stop_bound + tolerances.JOIN_WAIT_S)
                assert not conductor.is_alive(), "the live run never ended"
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    (copy,) = gf._run_dirs(home)  # noqa: SLF001
    assert gf._terminal_of(copy) is None, "the copy already has a terminal row"  # noqa: SLF001
    rows = records.lane_rows(copy)
    assert not rows.problems and not rows.torn, rows.problems
    assert {"plan", "issue", "confirmation"} <= {row.cls for row in rows.rows}
    gf._plan_bearing_tree(copy)  # noqa: SLF001
    gf._tidy(home)  # noqa: SLF001
