"""SA-14 in A-1 (L.SV-5.15): the port suite is one file for every implementation, and the V-10
release descriptor is the same value in Python and in JSON.

SA-14 says the shared conformance suite runs unmodified against the fake and the real
implementations (B3-C17, WR-PROOF-4) and that the wire mapping a port returns
(`descriptor_from_wire`) is what the attempt lane records. Both halves are checked from
independent ends: the suite hash against the file on disk, the descriptor against the lane's own
encoder.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from tests.proof.suites.ports import core, families, implementations
from trestle.child import run_services as rs
from trestle.common import lane_format as lf
from trestle.workflow import ports


class Impl:
    def __init__(self, name: str) -> None:
        self.name = name

    def identify(self) -> str:
        return self.name


def _identifies(built: core.Implementation) -> None:
    assert built.impl.identify() == built.impl.name


def _returns_text(built: core.Implementation) -> None:
    assert isinstance(built.impl.identify(), str)


@pytest.mark.parametrize("sa", ["SA-14"])
def test_suite_file_hash_identical_across_impls(sa: str, tmp_path: Path) -> None:
    suite = tmp_path / "family_suite.py"
    suite.write_text("# the family's cases live here\n", encoding="utf-8")
    registry = core.Registry()
    family = registry.register_family(
        "fam",
        [core.Case("identifies", _identifies), core.Case("text", _returns_text)],
        suite_file=suite,
    )
    runs = [
        registry.run_family("fam", lambda n=name: core.Implementation(Impl(n), name=n))
        for name in ("fake", "real-a", "real-b")
    ]
    hashes = {run.suite_sha256 for run in runs}
    assert hashes == {family.suite_sha256} == {core.sha256_of(suite)}
    assert [run.implementation for run in runs] == ["fake", "real-a", "real-b"]
    # the suite core itself is the file every family run rests on: it is one file
    core_sha = core.sha256_of(Path(core.__file__))
    assert len(core_sha) == 64
    # a changed suite is not the same suite: the hash moves, and a run refuses
    suite.write_text("# edited\n", encoding="utf-8")
    assert core.sha256_of(suite) not in hashes
    with pytest.raises(core.SuiteModified):
        registry.run_family("fam", lambda: core.Implementation(Impl("fake"), name="fake"))


FORMS = (
    ports.InRunGroup(),
    ports.InRunGroup(helpers_disclosed=True),
    ports.Durable(ports.DurableOwner.HOST),
    ports.Durable(ports.DurableOwner.ENVIRONMENT),
    ports.ArgvRelease(
        "/usr/bin/docker",
        ("ps", "-aq"),
        frozenset({0, 1}),
        ("stop", "x"),
        timedelta(seconds=2.5),
        ("rm", "x"),
    ),
    ports.ArgvRelease("/usr/bin/x", (), frozenset(), (), timedelta(seconds=30)),
)


@pytest.mark.parametrize("sa", ["SA-14"])
def test_descriptor_json_python_roundtrip(sa: str) -> None:
    for form in FORMS:
        wire = ports.descriptor_to_wire(form)
        text = json.dumps(wire, sort_keys=True)
        # Python -> JSON -> Python
        assert ports.descriptor_from_wire(json.loads(text)) == form
        # and the JSON is byte-identical to the one the attempt lane writes for the same form
        lane_form = rs.descriptor_to_lane(form)
        assert json.dumps(lf.descriptor_record(lane_form), sort_keys=True) == text
        # the lane reads the port's JSON back to the same form it would have written
        assert rs.descriptor_to_lane(json.loads(text)) == lane_form
    # exactly the three forms exist
    assert ports.FORMS == ("in_run_group", "argv", "durable")
    assert {type(f).__name__ for f in FORMS} == {"InRunGroup", "ArgvRelease", "Durable"}


@pytest.mark.parametrize("sa", ["SA-14"])
def test_the_fakes_run_the_one_suite_file(sa: str, tmp_path: Path) -> None:
    """Every registered implementation of a family ran the same family file: one hash, equal to
    the file on disk (B3-C17: the suite runs unmodified against every implementation)."""
    hashes: dict[str, set[str]] = {}
    for impl_id, (family, factory) in implementations.IMPLEMENTATIONS.items():
        run = core.run_family(family, lambda f=factory: f(tmp_path))
        assert run.implementation == impl_id
        hashes.setdefault(family, set()).add(run.suite_sha256)
    assert set(hashes) == {"Command Execution", "Local Process Supervision"}
    for family, seen in hashes.items():
        assert seen == {core.sha256_of(Path(families.__file__))}, family
    marker_runs = [r for r in core.REGISTRY.runs if r.family == "Local Process Supervision"]
    assert len(marker_runs) >= 2  # fake-marker and fake-marker-durable, one suite
