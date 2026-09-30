"""The stage vocabulary of the reference workflow (L.RB-2.2; hld-wr-environment Provisioning &
System Test, WR-ENV-9): a failure at any stage names the stage AND the service, so a caller never
has to guess which step of a multi-stage setup failed, or on what.

Five stages, each reported where it happens:

* `catalog`: an identifier the request names is not in the catalog. Refused by admission before
  a run id, naming the identifier and `valid_listed_at` (`identifier_sets.<set>`).
* `closure`: the Compose closure of the selection could not be derived
  (`plugins/_bind.derive_closure`; the resolver's V-11 code unchanged).
* `readiness`, `provisioning`, `test`: a work node of the tree failed. A node's canonical path
  is `<stage prefix>.<service>`, so the answer's `primary.path` IS the stage and the service:
  `backend.postgres` is the readiness stage of the postgres service, `provision.<service>` the
  provisioning stage, `test.<name>` the system test. `failure_at` reads it back.

The stage a node belongs to is its unit-name prefix and nothing else: no code decides a stage, so a
V-11 code is never redefined here (no environment code is involved).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Stage(StrEnum):
    CATALOG = "catalog"
    CLOSURE = "closure"
    READINESS = "readiness"
    PROVISIONING = "provisioning"
    TEST = "test"


NODE_PREFIXES: Final[dict[str, Stage]] = {
    "backend": Stage.READINESS,
    "provision": Stage.PROVISIONING,
    "test": Stage.TEST,
}
"""The unit-name prefix of each node stage; `<prefix>.<service>` is a node's canonical path."""

PATH_SEPARATOR: Final = "/"  # a node below the root is one segment; nesting joins with `/`


@dataclass(frozen=True)
class StageFailure:
    stage: Stage
    service: str
    code: str

    def text(self) -> str:
        """One bounded line naming both: `<stage> stage failed for <service> (<code>)`."""
        return f"{self.stage.value} stage failed for {self.service} ({self.code})"[:200]


def failure_at(path: str, code: str) -> StageFailure | None:
    """The stage and service a node path names, or None when the path is no stage node (the
    root, or a node whose prefix is not one of `NODE_PREFIXES`). The last segment decides."""
    leaf = path.rsplit(PATH_SEPARATOR, 1)[-1]
    prefix, dot, service = leaf.partition(".")
    stage = NODE_PREFIXES.get(prefix)
    if not dot or not service or stage is None:
        return None
    return StageFailure(stage, service, code)


def catalog_failure(code: str, identifier: str) -> StageFailure:
    """An identifier refused before a run id (admission's UNKNOWN_IDENTIFIER)."""
    return StageFailure(Stage.CATALOG, identifier, code)


def closure_failure(code: str, identifier: str) -> StageFailure:
    """A closure the resolver refused, or one naming a service the catalog does not hold."""
    return StageFailure(Stage.CLOSURE, identifier, code)
