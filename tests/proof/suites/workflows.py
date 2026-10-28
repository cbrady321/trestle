"""MC-35: the Slice A workflow registry (L.SV-5.14; `second_domain_free` added by L.SL-11.1, joined
by the tree workflows at L.TR-6.6).

`SLICE_A_WORKFLOWS` maps a workflow's plugin name to what the guarantee suites and the checkpoint
audits need to run it without knowing it:

* `fixture_path`: the published plugin source, relative to the repository root;
* `env_arg`: the plugin's `env_arg` argument (WR-OWN-8), or None when it declares none;
* `declared_codes`: the stable codes its declaration names (retryable codes and remedy triggers),
  which the suites drive;
* `oq31_eligible_both`: whether the workflow is eligible for both OQ-31 variants.

`tests/proof/suites/test_conformance_matrix.py::test_registry_entries_publish_and_admit` publishes
and admits every entry; J-SINGLE (b)'s vertex audit requires every entry admitted from a node under
`tests/proof/suites/` (the audit plugin reads this table)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

SLICE_A_WORKFLOWS: dict[str, dict[str, Any]] = {
    "spine_leaf": {
        "fixture_path": "tests/fixtures/workflows/spine_leaf.py",
        "env_arg": "env",
        "declared_codes": (),
        "oq31_eligible_both": True,
    },
    # L.SL-11.1: a RECORDED leaf over the fake command port; no Docker, toolchain adapter or AWS
    "second_domain_free": {
        "fixture_path": "tests/fixtures/workflows/second_domain_free.py",
        "env_arg": "env",
        "declared_codes": (),
        "oq31_eligible_both": True,
    },
    # L.TR-6.6: the two tree workflows, registered where they are (MC-B3-01 fixtures, declaration
    # only; `published_source` makes the published copy walk its tree, its declaration untouched)
    "slice_a_tree": {
        "fixture_path": "tests/fixtures/trees/slice_a_tree.py",
        "env_arg": None,
        "declared_codes": (),
        "oq31_eligible_both": True,
    },
    "three_level": {
        "fixture_path": "tests/fixtures/trees/three_level.py",
        "env_arg": None,
        "declared_codes": (),
        "oq31_eligible_both": True,
    },
}

ENTRY_KEYS = frozenset({"fixture_path", "env_arg", "declared_codes", "oq31_eligible_both"})


def fixture_file(entry: dict[str, Any]) -> Path:
    """The entry's plugin source file."""
    return ROOT / str(entry["fixture_path"])


def is_tree(entry: dict[str, Any]) -> bool:
    """Whether the entry is a tree workflow (an MC-B3-01 fixture, `ENTRY` not `DECLARATION`)."""
    return Path(str(entry["fixture_path"])).parent.name == "trees"


# The behaviour a declaration-only tree fixture lacks: every leaf is ready at its first
# observation, so a run walks the whole tree to `passed` and touches nothing (the fixtures' leaves
# are `resource_kind="marker"` with no effects). Only the `Unit` class and the plugin function
# change: the declaration block (`ENTRY = ...`), which admission compiles, is byte-identical.
_UNIT = '''class Unit:
    """A leaf that is ready at its first observation (the published copy of a tree workflow)."""

    def __init__(self, declaration: LeafDeclaration) -> None:
        self._declaration = declaration

    def declare(self) -> LeafDeclaration:
        return self._declaration

    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:
        return Observation(
            present=True,
            selector_present=True,
            identity_proven=True,
            configuration_compatible=True,
            postcondition=CheckResult(True, None, ""),
            preconditions=(),
            currency=(),
            found=(),
            code=None,
            payload=None,
        )

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError("a ready leaf has nothing to advance")

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError("a ready leaf creates nothing")


'''
_IMPORTS = (
    "from trestle.workflow.loop import run_tree\n"
    "from trestle.workflow.values import CheckResult, Observation\n"
)
_ENTRY_FUNCTION = re.compile(
    r"def (?P<name>\w+)\(ctx: Context\) -> dict\[str, str\]:\n"
    r'    return \{"fixture": "(?P=name)"\}\n'
)


def published_source(entry: dict[str, Any]) -> str:
    """The plugin source to publish for an MC-35 entry: the fixture file as it is, except that a
    tree workflow's copy has ready leaves and a plugin function that walks its tree (a choice
    selects over a fake marker with nothing up, so it takes its declared fallback)."""
    source = fixture_file(entry).read_text(encoding="utf-8")
    if not is_tree(entry):
        return source
    start = source.index("class Unit:")
    end = source.index("\n\n\ndef ", start) + 3
    source = source[:start] + _UNIT + source[end:]
    anchor = "from trestle.plugin import Context, trestle\n"
    source = source.replace(anchor, anchor + _IMPORTS, 1)
    match = _ENTRY_FUNCTION.search(source)
    assert match is not None, f"{entry['fixture_path']}: no declaration-only plugin function"
    body = (
        f"def {match['name']}(ctx: Context) -> dict[str, str]:\n"
        + "    run_tree(ctx, ENTRY, {}, ports={})\n"
        + f'    return {{"fixture": "{match["name"]}"}}\n'
    )
    return source[: match.start()] + body + source[match.end() :]
