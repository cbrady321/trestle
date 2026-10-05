"""CK-1 fixture plugin: one line appended to `counter_file` per execution, so a test counts how many
times the plugin's code really ran (a joined retry must not run it again)."""

from __future__ import annotations

from pathlib import Path

from trestle.plugin.surface import Context, trestle


@trestle
def counter(ctx: Context, counter_file: str) -> dict[str, int]:
    path = Path(counter_file)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("ran\n")
    return {"count": len(path.read_text(encoding="utf-8").splitlines())}
