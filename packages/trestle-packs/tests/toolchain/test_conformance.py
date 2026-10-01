"""L.RB-4.2: the Toolchain Resolution family's suite (`ToolchainResolver`), run against the stdlib
`FakeToolchainResolver` and the mise-backed `MiseToolchainResolver` over the stub `stub_mise`.
One suite file (`tests/conformance/toolchain_cases.py`), registered through `register_family` and
run by `run_family`, UNMODIFIED, against every implementation (WR-PROOF-4, SA-14). Planted defects
prove the suite is not vacuous. The resolver is STUB-PROVEN: the stub is the only mise there is
(D-1, OPEN-MISE-HOST undecided), and the binding is `STUB . CI`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from conformance import toolchain_cases
from tests.proof.suites.ports import core

from trestle_packs.fakes.toolchain import FakeToolchainResolver, Resolved, Unresolved
from trestle_packs.toolchain import MiseToolchainResolver

from . import rig

BINDINGS = [
    pytest.param(
        "fake",
        id="fake",
        marks=[
            pytest.mark.proves(
                "WR-ENV-15", "WR-ENV-15:adapter-exact-or-unresolved", "B", "B", "STUB", "CI"
            ),
        ],
    ),
    pytest.param(
        "mise-stub",
        id="mise-stub",
        marks=[
            pytest.mark.proves(
                "WR-PROOF-4", "WR-PROOF-4:b-toolchain-suite", "B", "B", "STUB", "CI"
            ),
            pytest.mark.proves(
                "WR-VERIFY-8", "WR-VERIFY-8:b-stub-read-facets-toolchain", "B", "B", "STUB", "CI"
            ),
        ],
    ),
]


def factory(binding: str, base: Path) -> Any:
    return rig.fake_toolchain(base) if binding == "fake" else rig.mise_stub_toolchain(base)


@pytest.mark.parametrize("binding", BINDINGS)
def test_toolchain_resolver_suite(binding: str, tmp_path: Path) -> None:
    run = core.run_family(toolchain_cases.FAMILY, factory(binding, tmp_path))
    assert run.cases_run == tuple(c.name for c in toolchain_cases.CASES)
    assert run.suite_sha256 == core.sha256_of(Path(toolchain_cases.__file__))


# planted defects: each resolver has one, and the suite (unmodified) names the case that catches it


class _PathFallback(FakeToolchainResolver):
    """Falls through to a tool found on the search path when the pin is not installed."""

    def resolve(self, project: str, tool: str) -> Any:
        result = super().resolve(project, tool)
        found = None if isinstance(result, Resolved) else shutil.which(tool)
        return result if found is None else Resolved(found, "0.0.1", "p", "a")


class _GuessesOnDrift(FakeToolchainResolver):
    """Resolves a tool from a listing it could not read: a guess."""

    def resolve(self, project: str, tool: str) -> Any:
        result = super().resolve(project, tool)
        if (
            isinstance(result, Unresolved)
            and result.code == toolchain_cases.TOOLCHAIN_INTERFACE_DRIFT
        ):
            return Resolved(f"/usr/bin/{tool}", "0", "p", "a")
        return result


class _TrustsTheListing(FakeToolchainResolver):
    """Takes an installed entry as the pin without asking which version its executable is."""

    def resolve(self, project: str, tool: str) -> Any:
        result = super().resolve(project, tool)
        if isinstance(result, Unresolved) and tool == "old":
            return Resolved(f"/nowhere/{tool}", "3.9.1", "p", "a")
        return result


class _AsksTheManagerToExecute(FakeToolchainResolver):
    """Also has the manager execute the tool (`mise exec`), as a shim-shaped resolver would."""

    def resolve(self, project: str, tool: str) -> Any:
        result = super().resolve(project, tool)
        self.calls.append(("exec", tool, "--", tool, "--version"))
        return result


class _AdoptionIsThePin(FakeToolchainResolver):
    """An adoption fingerprint that ignores the host's installs (it repeats the pin's own)."""

    def resolve(self, project: str, tool: str) -> Any:
        result = super().resolve(project, tool)
        if isinstance(result, Resolved):
            return Resolved(
                result.executable,
                result.reported_version,
                result.pin_fingerprint,
                result.pin_fingerprint,
            )
        return result


class _UnboundedAction(FakeToolchainResolver):
    """A human action far over HUMAN_ACTION_MAX."""

    def resolve(self, project: str, tool: str) -> Any:
        result = super().resolve(project, tool)
        if isinstance(result, Unresolved):
            return Unresolved(result.code, result.tool, result.pin, result.human_action * 200)
        return result


class _WritesOutsideCache(FakeToolchainResolver):
    """Leaves a note in the envelope's root on every resolve."""

    envelope: Path | None = None

    def resolve(self, project: str, tool: str) -> Any:
        if self.envelope is not None:
            (self.envelope / "last-resolved").write_text(tool, encoding="utf-8")
        return super().resolve(project, tool)


@pytest.mark.parametrize(
    ("defect", "caught_by"),
    [
        (_PathFallback, "a_decoy_on_the_search_path_is_never_chosen"),
        (_GuessesOnDrift, "output_outside_the_window_is_interface_drift_never_a_guess"),
        (_TrustsTheListing, "an_executable_that_is_not_the_pin_or_is_absent_is_missing"),
        (_AsksTheManagerToExecute, "only_the_listing_question_is_ever_asked_of_the_manager"),
        (_AdoptionIsThePin, "the_adoption_fingerprint_changes_when_the_tools_install_changes"),
        (_UnboundedAction, "unresolved_values_stay_within_their_bounds"),
        (_WritesOutsideCache, "a_resolve_leaves_the_envelope_as_it_found_it"),
    ],
)
def test_toolchain_suite_catches_planted_defects(
    defect: type[FakeToolchainResolver], caught_by: str, tmp_path: Path
) -> None:
    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(toolchain_cases.FAMILY, rig.fake_toolchain(tmp_path, defect))
    assert caught_by in str(caught.value)


def test_a_relative_mise_path_is_refused_before_anything_runs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        MiseToolchainResolver("mise", rig.CommandPort(), {})
