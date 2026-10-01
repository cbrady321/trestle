"""The hard-coded one-service catalog of tree v0 (MC-B-04 v0; L.RB-0.2; temporary TM-B4-3).

Until the trusted catalog is wired into the request schema (L.RB-1.1, which deletes this module)
the request names services out of this closed list. It holds identifiers only: the schema's
`ServiceName` enum (schema.py) spells the same values, because a plugin's published input schema
is read from the source text and cannot be derived from a runtime constant; a unit test pins the
two together.
"""

from __future__ import annotations

CATALOG_V0: tuple[str, ...] = ("postgres",)
"""The one service the reference environment can name in v0."""
