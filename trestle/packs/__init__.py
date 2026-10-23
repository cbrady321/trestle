"""Packs (docs/decisions/packs.md): a folder with a `pack.toml`, wrapper files under `plugins/`
and an optional library. `trestle pack install` copies pinned packs under
`$TRESTLE_HOME/packs/<owner>/<pack>/` and records every installed pack in
`$TRESTLE_HOME/packs.lock`; the registry reads the lock and rescans when it changes, so an install
needs no restart. Agents never install anything: installing is an operator command."""

from __future__ import annotations


class PackError(Exception):
    """A pack command refused: the message is the operator's whole answer."""
