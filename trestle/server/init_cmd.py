"""First-run bootstrap for Trestle home."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from trestle.server import lease
from trestle.server.home import (
    HOME_FORMAT,
    HomeRefused,
    check_local_mount,
    home_format,
    is_legacy,
    write_format,
    write_marker,
)
from trestle.server.ledger import RunLedger, evidence_dir, iter_run_dirs, ledger_path

_SEED_ECHO = '''"""Example plugin seeded by trestle init."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def echo(ctx: Context, message: str = "hello") -> dict[str, str]:
    ctx.log(f"echo: {message}")
    return {"message": message}
'''


def _packs_hint() -> str:
    try:
        import trestle_packs  # noqa: F401
    except ImportError:
        return 'workflow packs: pip install -e ".[packs]" (see docs/packs.md)'
    return "workflow packs: trestle serve --plugin-dir examples/packs (see docs/packs.md)"


@dataclass
class UpgradeReport:
    live: list[str] = field(default_factory=list)


def upgrade_home(home: Path) -> UpgradeReport:
    """`trestle init --upgrade` on a v0.3.0 home (every v0.3.0 server stopped first: that is the
    operator's job). Each non-terminal run gets a live marker with no owner, so the reaper
    finalizes it once its recorded leader is gone (Old runs); then `home/keys/` is seeded
    (Problem B); `home/format` is written last, so an upgrade that dies part way is run again."""
    report = UpgradeReport()
    for run_dir in iter_run_dirs(home):
        ledger = RunLedger.open(ledger_path(run_dir))
        created = ledger.last_kind("created")
        if created is None or ledger.terminal_state() is not None:
            continue
        run_id = str(created.get("run_id", run_dir.name))
        write_marker(
            home,
            run_id,
            {
                "owner": None,
                "month": run_dir.parent.name,
                "state": "running" if ledger.has_kind("started") else "queued",
                "lease_key": created.get(lease.LEASE_KEY_FIELD),
                "deadline": _spec_deadline(run_dir),
                "arrival": created.get("at"),
            },
        )
        report.live.append(run_id)
    seed_keys(home)
    write_format(home)
    return report


def seed_keys(home: Path) -> None:
    """Problem B's hook (step 3): seed `home/keys/` from the created rows (and the expiries
    `idempotency.json` recorded), replayed by created.at then run id. Until then the v0.3.0 store
    `idempotency.json` stays the key store and needs no seeding."""


def _spec_deadline(run_dir: Path) -> float | None:
    from datetime import datetime

    try:
        spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(spec["deadline"])).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_init(*, home: Path, seed_echo: bool = True, upgrade: bool = False) -> int:
    """`trestle init`: create the home (format 2) and seed the plugins directory. It never
    recovers or reaps runs. A v0.3.0 home is refused unless `upgrade`."""
    try:
        check_local_mount(home)
    except HomeRefused as exc:
        print(f"trestle: {exc}", file=sys.stderr)
        return 2
    recorded = home_format(home)
    upgraded: UpgradeReport | None = None
    if recorded is None and is_legacy(home):
        if not upgrade:
            print(
                f"trestle: {home}: a v0.3.0 home; stop every v0.3.0 server, then run "
                "`trestle init --upgrade`",
                file=sys.stderr,
            )
            return 2
        upgraded = upgrade_home(home)
    elif recorded is None:
        write_format(home)
    elif recorded != HOME_FORMAT:
        print(f"trestle: {home}: home format {recorded} is not {HOME_FORMAT}", file=sys.stderr)
        return 2
    plugins = home / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    if seed_echo:
        echo_path = plugins / "echo.py"
        if not echo_path.exists():
            echo_path.write_text(_SEED_ECHO, encoding="utf-8")
    print(f"trestle home: {home}")
    if upgraded is not None:
        print(f"upgraded to format {HOME_FORMAT}: {len(upgraded.live)} live runs listed")
    print(f"plugins: {plugins}")
    if seed_echo:
        print("seeded: echo.py")
    print(_packs_hint())
    return 0
