"""First-run bootstrap for Trestle home."""

from __future__ import annotations

from pathlib import Path

from trestle.server.recovery import recover_on_startup

_SEED_ECHO = '''"""Example plugin seeded by trestle init."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def echo(ctx: Context, message: str = "hello") -> dict[str, str]:
    ctx.log(f"echo: {message}")
    return {"message": message}
'''


def run_init(*, home: Path, seed_echo: bool = True) -> int:
    plugins = home / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    if seed_echo:
        echo_path = plugins / "echo.py"
        if not echo_path.exists():
            echo_path.write_text(_SEED_ECHO, encoding="utf-8")
    recover_on_startup(home)
    print(f"trestle home: {home}")
    print(f"plugins: {plugins}")
    if seed_echo:
        print("seeded: echo.py")
    return 0
