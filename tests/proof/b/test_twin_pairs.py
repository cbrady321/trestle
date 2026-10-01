"""L.RB-0.5: MC-B-03, the CI twin of every docker_host node.

`twin_problems` is a pure function of collected nodes; the planted tests give it synthetic nodes,
and the live test collects the packs and env test roots (every root that exists) under both HOST
gate values. Until a docker_host node exists the live audit is vacuous, never a skip.
"""

from __future__ import annotations

import pytest

from tests.proof import meta as meta_mod
from tests.proof.b import stub_labels as sl
from tests.proof.b import twin_audit as ta

SUFFIX = sl.TWIN_SUFFIX
HOST = "packages/trestle-env/tests/host/test_b_spine.py::test_one_call"
TWIN = "packages/trestle-env/tests/twin/test_b_spine_twin.py::test_one_call"
LABEL = "WR-ENV-10:one-request"
LABELS = {
    LABEL + SUFFIX: {"id": LABEL + SUFFIX, "posture": "stub_proven"},
    LABEL: {"id": LABEL, "posture": "claim"},
}


def _node(nodeid, labels, docker_host=False):
    return {
        "nodeid": nodeid,
        "labels": list(labels),
        "docker_host": docker_host,
        "host_only": False,
    }


def _pair():
    return [_node(HOST, [LABEL, "B1.1"], docker_host=True), _node(TWIN, [LABEL + SUFFIX])]


def test_live_every_docker_host_node_has_its_twin():
    nodes = ta.collect_nodes(
        [
            p
            for p in ("packages/trestle-packs/tests", "packages/trestle-env/tests")
            if (ta.ROOT / p).is_dir()
        ]
    )
    labels = {str(lb["id"]): lb for lb in meta_mod._load_all_labels()}  # noqa: SLF001
    assert ta.twin_problems(nodes, labels, SUFFIX) == []


def test_planted_complete_pair_is_clean():
    assert ta.twin_problems(_pair(), LABELS, SUFFIX) == []


def test_planted_docker_host_node_without_a_twin_rejected():
    problems = ta.twin_problems(_pair()[:1], LABELS, SUFFIX)
    assert any("not collected" in p and TWIN in p for p in problems)


def test_planted_twin_missing_a_label_rejected():
    nodes = [_node(HOST, [LABEL, "WR-X:other"], docker_host=True), _node(TWIN, [LABEL + SUFFIX])]
    assert any("WR-X:other" + SUFFIX in p for p in ta.twin_problems(nodes, LABELS, SUFFIX))


def test_planted_twin_registering_a_matrix_clause_rejected():
    nodes = [_node(HOST, [LABEL], docker_host=True), _node(TWIN, [LABEL + SUFFIX, "B1.1"])]
    assert any(
        "no matrix clause" in p and "B1.1" in p for p in ta.twin_problems(nodes, LABELS, SUFFIX)
    )


def test_planted_twin_label_not_stub_proven_rejected():
    labels = {**LABELS, LABEL + SUFFIX: {"id": LABEL + SUFFIX, "posture": "claim"}}
    assert any("not declared stub_proven" in p for p in ta.twin_problems(_pair(), labels, SUFFIX))


def test_planted_twin_that_is_itself_docker_host_rejected():
    nodes = [
        _node(HOST, [LABEL], docker_host=True),
        _node(TWIN, [LABEL + SUFFIX], docker_host=True),
    ]
    assert any("CI node" in p for p in ta.twin_problems(nodes, LABELS, SUFFIX))


def test_planted_host_node_outside_the_host_directory_rejected():
    nodes = [_node("packages/trestle-env/tests/unit/test_x.py::t", [LABEL], docker_host=True)]
    assert any("no twin id" in p for p in ta.twin_problems(nodes, LABELS, SUFFIX))


@pytest.mark.parametrize(
    ("host", "twin"),
    [
        (HOST, TWIN),
        (f"{HOST}[a-b]", f"{TWIN}[a-b]"),
        (
            "packages/trestle-packs/tests/container/test_conformance.py::test_port_suite[real]",
            "packages/trestle-packs/tests/container/test_conformance.py::test_port_suite[fake]",
        ),
        (
            "packages/trestle-packs/tests/container/test_engine_codes.py::t[real-unreachable-endpoint]",
            "packages/trestle-packs/tests/container/test_engine_codes.py::t[fake]",
        ),
        ("tests/x/test_y.py::t", None),
    ],
)
def test_twin_nodeid_follows_mc_b_03(host, twin):
    assert ta.twin_nodeid(host) == twin


def test_planted_adversary_stub_clause_carrying_a_stub_label_rejected():
    labels = {"WR-VERIFY-5:x": {"id": "WR-VERIFY-5:x", "posture": "stub_proven"}}
    nodes = [_node("t::a", ["B4.5", "WR-VERIFY-5:x"]), _node("t::b", ["B9.1"])]
    problems = ta.adversary_problems(nodes, labels, SUFFIX, sl.NO_STUB_CLAUSES)
    assert len(problems) == 1 and "B4.5" in problems[0]
    # a twin-suffixed label is a STUB label whatever its declared posture
    nodes = [_node("t::c", ["B9.1", "WR-Y:z" + SUFFIX])]
    assert ta.adversary_problems(nodes, {}, SUFFIX, sl.NO_STUB_CLAUSES)
