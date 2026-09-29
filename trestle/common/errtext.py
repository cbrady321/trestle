"""The one bounded, sanitized error-message text (MC-CORE-14).

A failure's message crosses from the child into the run record and the answer. `sanitize` is the
only path it takes: each root prefix a host path would leak through is replaced by a fixed token,
then the text is cut to `MESSAGE_MAX` bytes of UTF-8 at a character boundary. It imports nothing
of the plugin, the workflow or the packs (DM-17).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

MESSAGE_MAX = 512  # bytes of UTF-8

# root name -> the fixed token that replaces its path prefix
TOKENS: Mapping[str, str] = {
    "home": "<home>",
    "run": "<run>",
    "cwd": "<cwd>",
    "user-home": "<user-home>",
}


def sanitize(message: str, roots: Mapping[str, Path]) -> str:
    """Replace every root prefix in `message` by its token, then bound it to `MESSAGE_MAX` bytes.

    A root is named by a key of `TOKENS` ("home", "run", "cwd", "user-home"; any other key becomes
    `<key>`). Both the path as given and its resolved form are replaced, and the longest path first,
    so a run directory under the home reads `<run>`, never `<home>/runs/...`. A path matches only at
    a boundary: `/a/b` is replaced in `/a/b/c` and `/a/b:` but not in `/a/bc`.
    """
    pairs: list[tuple[str, str]] = []
    for name, root in roots.items():
        token = TOKENS.get(name, f"<{name}>")
        for form in {str(root), _resolved(root)}:
            if len(form.rstrip("/")) > 1:  # a bare "/" would replace every slash
                pairs.append((form.rstrip("/"), token))
    text = message
    for form, token in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
        text = re.sub(re.escape(form) + r"(?![\w.\-])", token.replace("\\", "\\\\"), text)
    return _bound(text)


def _resolved(root: Path) -> str:
    try:
        return str(root.resolve())
    except OSError:
        return str(root)


def _bound(text: str, limit: int = MESSAGE_MAX) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore")
