"""The toolchain resolver's conformance cases (L.RB-4.2; B3-C12, B3-C17 (3)-(5), B3-E3, MC-B-06).

Registered through the suite core's `register_family` as family `toolchain` and run, UNMODIFIED,
against the stdlib fake and the mise-backed resolver over the absolute-path stub `stub_mise`. A case
reaches the implementation only through `resolve(project, tool)` and the factory's extras:

- `project`, and the tools of its world: `tool` (installed, pinned at `pin`, so it reports
  `installed_version`), `other_tool` (installed), `missing_tool` (listed, `installed: false`),
  `unlisted_tool` (not listed at all), `mismatch_tool` (installed, but its executable reports a
  version that is not its pin), `hollow_tool` (listed installed, no executable under its install
  path), `long_pin_tool` (listed, not installed, pinned at a text longer than `TOKEN_MAX`);
- `install_root`: the directory every resolved executable lives under;
- `surface(mode)`: what the manager prints from now on, `normal`, `drift` (valid JSON outside the
  window) or `malformed` (not JSON); `reinstall()`: the pinned tool's install changes (a newer
  version of `tool`, still the pin's, replaces it: `newer_version`);
- `mise_calls()`: the argv (after the program) of every question the adapter put to the manager;
  each is `ls --current --json <tool>`: one tool per question keeps the answer inside the console
  excerpt the port hands an adapter (B3-C14, `TEXT_MAX`);
- `plant_decoy(name)`: an executable called `name` first on the search path, its path returned;
- `reach.envelope`: the tree hashed around every call (`ToolchainResolver.resolve` may write only
  `${ENVELOPE}/cache/**` and `${ENVELOPE}/state/**`).

A resolution is the pin's own executable or an `Unresolved`; the search path is never consulted, and
the only question the manager is asked is that listing (no `exec`, `run`, `install` or shim).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from tests.proof.suites.ports import core
from tests.proof.suites.ports.families import read

FAMILY = "toolchain"
TOOLCHAIN_MISSING = "execution.toolchain_missing"
TOOLCHAIN_INTERFACE_DRIFT = "adapter.toolchain_interface_drift"
NAME_MAX = 128
TOKEN_MAX = 256
EXEC_PATH_MAX = 1024
HUMAN_ACTION_MAX = 1024
LIST_ARGV = ("ls", "--current", "--json")


def _resolve(built: core.Implementation, tool: str, project: str | None = None) -> Any:
    with read(built, "ToolchainResolver.resolve"):
        return built.impl.resolve(project or built.extras["project"], tool)


def _is_resolved(result: Any) -> bool:
    return hasattr(result, "executable")


def _refused(result: Any, code: str) -> None:
    assert not _is_resolved(result), f"resolved: {result}"
    assert result.code == code, result


def an_installed_pin_resolves_to_its_own_absolute_executable(built: core.Implementation) -> None:
    result = _resolve(built, built.extras["tool"])
    assert _is_resolved(result), result
    assert os.path.isabs(result.executable) and len(result.executable) <= EXEC_PATH_MAX
    assert os.path.realpath(result.executable).startswith(
        os.path.realpath(built.extras["install_root"])
    )
    assert result.executable.endswith(f"/bin/{built.extras['tool']}")
    assert result.reported_version.startswith(built.extras["pin"])
    assert result.reported_version == built.extras["installed_version"]
    for text in (result.pin_fingerprint, result.adoption_fingerprint):
        assert text and len(text) <= TOKEN_MAX


def resolution_is_repeatable_and_fingerprints_say_what_they_cover(
    built: core.Implementation,
) -> None:
    tool, other = built.extras["tool"], built.extras["other_tool"]
    first = _resolve(built, tool)
    assert _resolve(built, tool) == first
    second = _resolve(built, other)
    assert _is_resolved(second)
    assert second.pin_fingerprint != first.pin_fingerprint  # a pin's own
    assert second.adoption_fingerprint != first.adoption_fingerprint  # a tool's own installs


def the_adoption_fingerprint_changes_when_the_tools_install_changes(
    built: core.Implementation,
) -> None:
    tool = built.extras["tool"]
    before = _resolve(built, tool)
    other = _resolve(built, built.extras["other_tool"])
    built.extras["reinstall"]()
    after = _resolve(built, tool)
    assert _is_resolved(before) and _is_resolved(after)
    assert after.adoption_fingerprint != before.adoption_fingerprint
    assert after.pin_fingerprint == before.pin_fingerprint  # the pin did not change
    assert after.reported_version == built.extras["newer_version"]
    assert after.executable != before.executable
    assert _resolve(built, built.extras["other_tool"]) == other  # nothing else moved


def a_pin_that_is_not_installed_is_unresolved_toolchain_missing(built: core.Implementation) -> None:
    refused = _resolve(built, built.extras["missing_tool"])
    _refused(refused, TOOLCHAIN_MISSING)
    assert refused.tool == built.extras["missing_tool"]
    assert refused.human_action and refused.tool in refused.human_action


def a_tool_the_listing_does_not_name_or_a_project_it_does_not_know_is_missing(
    built: core.Implementation,
) -> None:
    _refused(_resolve(built, built.extras["unlisted_tool"]), TOOLCHAIN_MISSING)
    _refused(_resolve(built, built.extras["tool"], "no-such-project"), TOOLCHAIN_MISSING)


def an_executable_that_is_not_the_pin_or_is_absent_is_missing(built: core.Implementation) -> None:
    _refused(_resolve(built, built.extras["mismatch_tool"]), TOOLCHAIN_MISSING)
    _refused(_resolve(built, built.extras["hollow_tool"]), TOOLCHAIN_MISSING)


def a_decoy_on_the_search_path_is_never_chosen(built: core.Implementation) -> None:
    missing, tool = built.extras["missing_tool"], built.extras["tool"]
    decoy_of_missing = built.extras["plant_decoy"](missing)
    decoy_of_tool = built.extras["plant_decoy"](tool)
    refused = _resolve(built, missing)
    _refused(refused, TOOLCHAIN_MISSING)  # a decoy exists, and is not a resolution
    result = _resolve(built, tool)
    assert _is_resolved(result)
    assert result.executable != decoy_of_tool and result.executable != decoy_of_missing
    assert not result.executable.startswith(os.path.dirname(decoy_of_tool) + os.sep)


def output_outside_the_window_is_interface_drift_never_a_guess(
    built: core.Implementation,
) -> None:
    tool = built.extras["tool"]
    for mode in ("drift", "malformed"):
        built.extras["surface"](mode)
        refused = _resolve(built, tool)
        _refused(refused, TOOLCHAIN_INTERFACE_DRIFT)
        assert refused.human_action and len(refused.human_action) <= HUMAN_ACTION_MAX
    built.extras["surface"]("normal")
    assert _is_resolved(_resolve(built, tool))  # the surface came back; nothing was remembered


def only_the_listing_question_is_ever_asked_of_the_manager(built: core.Implementation) -> None:
    for tool in ("tool", "missing_tool", "unlisted_tool", "mismatch_tool"):
        _resolve(built, built.extras[tool])
    calls = built.extras["mise_calls"]()
    assert calls, "the resolver asked the manager nothing"
    for call in calls:
        assert call[:3] == LIST_ARGV and len(call) == 4, call  # never exec, run, x, install, a shim
    assert {call[3] for call in calls} >= {built.extras["tool"], built.extras["unlisted_tool"]}


def unresolved_values_stay_within_their_bounds(built: core.Implementation) -> None:
    long_name = "t" * 300
    refused = _resolve(built, long_name)
    _refused(refused, TOOLCHAIN_MISSING)
    assert len(refused.tool) <= NAME_MAX
    assert len(refused.human_action) <= HUMAN_ACTION_MAX
    refused = _resolve(built, built.extras["long_pin_tool"])
    _refused(refused, TOOLCHAIN_MISSING)
    assert 0 < len(refused.pin) <= TOKEN_MAX
    assert len(refused.human_action) <= HUMAN_ACTION_MAX


def a_resolve_leaves_the_envelope_as_it_found_it(built: core.Implementation) -> None:
    for tool in (built.extras["tool"], built.extras["missing_tool"]):
        _resolve(built, tool)  # each under the watcher: filesystem, process and network diff


CASES: Sequence[core.Case] = (
    core.Case(
        "an_installed_pin_resolves_to_its_own_absolute_executable",
        an_installed_pin_resolves_to_its_own_absolute_executable,
    ),
    core.Case(
        "resolution_is_repeatable_and_fingerprints_say_what_they_cover",
        resolution_is_repeatable_and_fingerprints_say_what_they_cover,
    ),
    core.Case(
        "the_adoption_fingerprint_changes_when_the_tools_install_changes",
        the_adoption_fingerprint_changes_when_the_tools_install_changes,
    ),
    core.Case(
        "a_pin_that_is_not_installed_is_unresolved_toolchain_missing",
        a_pin_that_is_not_installed_is_unresolved_toolchain_missing,
    ),
    core.Case(
        "a_tool_the_listing_does_not_name_or_a_project_it_does_not_know_is_missing",
        a_tool_the_listing_does_not_name_or_a_project_it_does_not_know_is_missing,
    ),
    core.Case(
        "an_executable_that_is_not_the_pin_or_is_absent_is_missing",
        an_executable_that_is_not_the_pin_or_is_absent_is_missing,
    ),
    core.Case(
        "a_decoy_on_the_search_path_is_never_chosen", a_decoy_on_the_search_path_is_never_chosen
    ),
    core.Case(
        "output_outside_the_window_is_interface_drift_never_a_guess",
        output_outside_the_window_is_interface_drift_never_a_guess,
    ),
    core.Case(
        "only_the_listing_question_is_ever_asked_of_the_manager",
        only_the_listing_question_is_ever_asked_of_the_manager,
    ),
    core.Case(
        "unresolved_values_stay_within_their_bounds", unresolved_values_stay_within_their_bounds
    ),
    core.Case(
        "a_resolve_leaves_the_envelope_as_it_found_it",
        a_resolve_leaves_the_envelope_as_it_found_it,
    ),
)

core.register_family(FAMILY, CASES)
