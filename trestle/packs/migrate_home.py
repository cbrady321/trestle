"""The `examples/packs` migration (P-16), run by `trestle init --upgrade` (and so by
`trestle service upgrade` and `scripts/install.sh`).

Up to 0.5.2 the tool plugins were loose files in the checkout's `examples/packs/`, listed in
`[plugins] paths`. They are now the bundled packs `docker`, `pytest`, `migrate` and `stack`, and
that folder is gone. A home whose `[plugins] paths` lists it gets `stack` installed (which
installs the other three, `requires.packs`), and then loses the entry, so the plugins keep their
names with no manual edit. If the install fails, the config is left as it was and the operator's
command is printed: the upgrade itself still completes.

`config.toml` is edited as text, only the `paths = [...]` array of the `[plugins]` table, and the
result must parse to the same configuration minus the dropped entries; anything else (a dotted
`plugins.paths` key, an inline table) is left alone with the operator's line.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

from trestle.packs import PackError
from trestle.server.plugin_paths import expand_plugin_path

MIGRATED_PACK = "stack"
TOOL_PACKS = ("docker", "pytest", "migrate", "stack")
OPERATOR_LINE = f"trestle pack install {MIGRATED_PACK}"

_TABLE = re.compile(r"^[ \t]*\[\[?[^\]\n]*\]\]?[ \t]*(#[^\n]*)?$", re.MULTILINE)
_PLUGINS = re.compile(r"^[ \t]*\[[ \t]*plugins[ \t]*\][ \t]*(#[^\n]*)?$", re.MULTILINE)
_PATHS = re.compile(r"^[ \t]*paths[ \t]*=[ \t]*(?=\[)", re.MULTILINE)


def is_examples_packs(raw: str, home: Path) -> bool:
    """A `[plugins] paths` entry naming a checkout's `examples/packs` folder."""
    return expand_plugin_path(raw, home).parts[-2:] == ("examples", "packs")


def _array_end(text: str, start: int) -> int | None:
    """The index just past the `]` closing the TOML array that opens at `text[start]`."""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "#":
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            continue
        if ch in "\"'":
            triple = text.startswith(ch * 3, i)
            quote = ch * 3 if triple else ch
            i += len(quote)
            while i < len(text) and not text.startswith(quote, i):
                i += 2 if ch == '"' and text[i] == "\\" else 1
            i += len(quote)
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def drop_plugin_paths(text: str, drop: Callable[[str], bool]) -> str | None:
    """`text` with the `[plugins] paths` entries `drop` selects removed, or None when the array
    cannot be rewritten safely (or nothing is dropped)."""
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    plugins = before.get("plugins")
    paths = plugins.get("paths") if isinstance(plugins, dict) else None
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return None
    kept = [p for p in paths if not drop(p)]
    if len(kept) == len(paths):
        return None
    header = _PLUGINS.search(text)
    if header is None:
        return None
    following = _TABLE.search(text, header.end())
    table_end = following.start() if following else len(text)
    found = _PATHS.search(text, header.end(), table_end)
    if found is None:
        return None
    end = _array_end(text, found.end())
    if end is None or end > table_end:
        return None
    rendered = "[" + ", ".join(json.dumps(p) for p in kept) + "]"
    out = text[: found.end()] + rendered + text[end:]
    try:
        after = tomllib.loads(out)
    except tomllib.TOMLDecodeError:
        return None
    before["plugins"]["paths"] = kept
    return out if after == before else None


def migrate_examples_packs(
    home: Path,
    install: Callable[[str], object],
    installed: Callable[[], set[str]],
    say: Callable[[str], None],
) -> bool:
    """Replace an `examples/packs` entry of `[plugins] paths` with the installed tool packs.
    True when the config was rewritten."""
    config = home / "config.toml"
    if not config.is_file():
        return False
    text = config.read_text(encoding="utf-8")

    def drop(raw: str) -> bool:
        return is_examples_packs(raw, home)

    try:
        raw = tomllib.loads(text).get("plugins", {}).get("paths", [])
    except (tomllib.TOMLDecodeError, AttributeError):
        return False
    if not isinstance(raw, list) or not any(isinstance(p, str) and drop(p) for p in raw):
        return False
    rewritten = drop_plugin_paths(text, drop)
    if rewritten is None:
        say(
            "examples/packs: could not rewrite [plugins] paths in config.toml; the operator "
            f"removes the examples/packs entry and runs: {OPERATOR_LINE}"
        )
        return False
    if not set(TOOL_PACKS) <= installed():
        try:
            install(MIGRATED_PACK)
        except PackError as exc:
            say(
                f"examples/packs: the tool plugins are bundled packs now, and installing them "
                f"failed ({exc}); config.toml is unchanged. The operator runs: {OPERATOR_LINE}"
            )
            return False
    config.write_text(rewritten, encoding="utf-8")
    say(
        "examples/packs: the tool plugins are the bundled packs "
        f"{', '.join(TOOL_PACKS)} now; removed the examples/packs entry from [plugins] paths"
    )
    return True


def migrate_home(home: Path, say: Callable[[str], None]) -> bool:
    """`migrate_examples_packs` with the real installer."""
    import io

    from trestle.packs.install import Installer
    from trestle.packs.lock import read_lock

    out = io.StringIO()
    installer = Installer(home=home, out=out)

    def install(name: str) -> object:
        try:
            return installer.install(name)
        finally:
            for line in out.getvalue().splitlines():
                say(line)
            out.seek(0)
            out.truncate()

    return migrate_examples_packs(home, install, lambda: {p.name for p in read_lock(home)}, say)
