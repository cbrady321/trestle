"""An engine that does not answer, diagnosed through the workflow (L.RB-7.1; WR-ENV-6, B4.2):
the facts the HOST node `host/test_engine_diagnosis.py` and its twin
`twin/test_engine_diagnosis_twin.py` both assert of the one call's answer.

The answer is `blocked` (B4-T2 row 9: the code in a `Blocked` step), its primary code is
`DOCKER_ENGINE_UNREACHABLE` and never `DOCKER_CLI_MISSING` (a CLI that exists but reaches no engine
is not a missing CLI), and the primary path names the stage and the service (`stages.failure_at`,
L.RB-2.2): a backend's readiness node, with the human action a Blocked step carries.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from trestle_packs.container import DOCKER_CLI_MISSING, DOCKER_ENGINE_UNREACHABLE

from trestle_env import stages, tree


def assert_diagnosed_unreachable(answer: Mapping[str, Any]) -> None:
    assert answer["state"] == "succeeded", answer  # the run finished; its class is the verdict
    body = answer["answer"]
    assert body["outcome"] == "blocked", body
    primary = body["primary"]
    assert primary["code"] == DOCKER_ENGINE_UNREACHABLE, primary
    assert DOCKER_CLI_MISSING not in json.dumps(answer), "never diagnosed as a missing CLI"
    named = stages.failure_at("/".join(primary["path"]), primary["code"])
    services = {tree.HTTP_SUPPORT_SERVICE, tree.POSTGRES_SERVICE}  # the sibling backends
    assert named is not None and named.stage is stages.Stage.READINESS, primary
    assert named.service in services and named.code == DOCKER_ENGINE_UNREACHABLE, primary
    assert primary["human_action"], primary  # a Blocked step carries what to do (B4-C5)
    assert all(node["disposition"] != "started" for node in body["listed"]), body["listed"]
