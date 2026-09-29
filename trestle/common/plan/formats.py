"""The admitted plan's wire format (MC-20; L.SV-3.3).

`spec.json` key `plan` is the canonical JSON of an `AdmittedPlan`'s fields plus `plan_digest`, the
sha256 of the canonical JSON of every other field (B2-C2 Post). `format_version` is part of what
is hashed, so a plan of another format never verifies as this one. `declaration_digest` (the
`DeclaredTree.digest` compiled from, MC-34; null for a plain plugin, which has no declaration)
travels beside it. This module is the dict-level codec; `compiler.AdmittedPlan.to_json/from_json`
wrap it. Stdlib and `trestle.common.plan.declared` only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from trestle.common.plan.declared import canonical_json

PLAN_FORMAT = 1


class UnknownPlanFormat(ValueError):
    """`format_version` is not one this reader understands."""


class PlanInvalid(ValueError):
    """The text is not a well-formed admitted plan (shape or digest)."""


def plan_digest(body: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of a plan body (every field but `plan_digest`)."""
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def encode(body: Mapping[str, Any], digest: str | None) -> str:
    """Canonical JSON of `body` plus its `plan_digest` (`None` only for the implicit plan)."""
    return canonical_json({**body, "plan_digest": digest})


def decode(text: str) -> tuple[dict[str, Any], str | None]:
    """The body and digest of an encoded plan.

    Raises `UnknownPlanFormat` for another `format_version` and `PlanInvalid` for text that is not
    an object, or whose digest does not match (a plan with a digest must verify; a digest of
    `None` is the implicit plan's and is accepted only with an empty declaration digest).
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PlanInvalid("not_json") from exc
    if not isinstance(data, dict):
        raise PlanInvalid("not_an_object")
    version = data.get("format_version")
    if version != PLAN_FORMAT or isinstance(version, bool):
        raise UnknownPlanFormat(f"format_version {version!r}")
    if "plan_digest" not in data:
        raise PlanInvalid("no_digest")
    digest = data.pop("plan_digest")
    if digest is None:
        if data.get("declaration_digest") is not None:
            raise PlanInvalid("digest_missing")
    elif digest != plan_digest(data):
        raise PlanInvalid("digest_mismatch")
    return data, digest
