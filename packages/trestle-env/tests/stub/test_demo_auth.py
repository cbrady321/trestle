"""L.RB-9.3: the demo-credential precondition of the reference tree, against the stub issuer
(WR-ENV-8, WR-ENV-14; B3-C8, B3-C11, J-5a, J-25, J-25a, V-11.1; D-9: AWS is DEMO ONLY, never real).

The tree's own `CredentialUnit` runs through the real loop (MC-26's rig: the real child services
and lane under a manual clock) over `FakeGrant`, the stub issuer's in-memory port, with a dependent
that `needs` it. What the stub stands for, a real identity provider, is out of scope for ever (D-9),
so both labels are stub-proven. The join is the runtime's: the unit only states the policy (refresh
a credential that cannot be used, or that does not outlive the ROOT deadline plus the margin).

The host scope the join compares the credential's generation with is read live from
`DemoHostScope` (`HostScopeReads`, the only producer of `HostScopeReading` entries): every join
sees the issuer's current generation, so a refresh that rotates it is not read as a stale consumer.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.common.plan.vocabulary import NodeClass
from trestle.workflow import codes, ports
from trestle.workflow.values import Resend
from trestle_packs.fakes.grant import FakeGrant
from trestle_packs.grant import DemoHostScope

from trestle_env import tree

IDENTITY = "demo-user"
DEPENDENT = "dependent"
DEADLINE_S = 600  # the tree rig's root deadline; the join's margin is `kit.MARGIN_S`
LIMIT = timedelta(seconds=DEADLINE_S + kit.MARGIN_S)  # what a credential must outlive
LONG = LIMIT + timedelta(hours=1)
SLICE = timedelta(seconds=tree.STAGE_BUDGET_S)  # a node's slice: seconds, the root's is minutes
SHORT = LIMIT - timedelta(seconds=1)  # past any slice, one second short of the ROOT deadline


class RigGrant(FakeGrant):
    """The stub issuer under the rig's manual clock: generation `n` (from 0) expires
    `lifetimes[n]` after `kit.NOW` (the last lifetime repeats), instead of after the wall clock."""

    def __init__(self, lifetimes: Sequence[timedelta], identity: str = IDENTITY) -> None:
        self._plan = list(lifetimes)
        super().__init__(identity)

    def advance(self) -> str:
        generation = super().advance()
        n = len(self._issued) - 1
        self._expires_at = kit.NOW + self._plan[min(n, len(self._plan) - 1)]
        return generation


class LiveScope:
    """A `HostScopeReading` that asks the issuer at each join (duck-typed: the join reads
    `.readings`)."""

    def __init__(self, grant: FakeGrant) -> None:
        self._scope = DemoHostScope(grant, now=lambda: kit.NOW)

    @property
    def readings(self) -> Any:
        return self._scope.readings().readings


def rig_over(tmp_path: Path, grant: FakeGrant) -> tk.TreeRig:
    root = tk.group(
        "environment",
        (tk.bind(tree.CREDENTIAL_UNIT), tk.bind(DEPENDENT, tree.CREDENTIAL_UNIT)),
    )
    marker = tk.PathMarker()
    return tk.tree_rig(
        tmp_path,
        root,
        {tree.CREDENTIAL_UNIT: tree.CredentialUnit(), DEPENDENT: tk.leaf_unit(DEPENDENT)},
        marker,
        deadline_s=DEADLINE_S,
        port_impl={**tk.port_map(marker), ports.GrantReads: grant, ports.GrantRefresh: grant},
    )


def run(rig: tk.TreeRig, grant: FakeGrant) -> dict[str, dict[str, Any]]:
    walked = rig.rig.loop()
    walked.host_scope = LiveScope(grant)
    walked.run()
    return rig.ends()


def issues(rig: tk.TreeRig) -> list[dict[str, Any]]:
    return [
        r for r in rig.rows() if r.get("path") == tree.CREDENTIAL_UNIT and r["class"] == "issue"
    ]


LABEL_8 = "WR-ENV-8:mfa-blocked-human-action"
LABEL_14 = "WR-ENV-14:lifetime-vs-root-deadline-refresh-or-block"


def first_row(rig: tk.TreeRig, path: str, cls: str) -> int:
    return next(n for n, r in enumerate(rig.rows()) if r.get("path") == path and r["class"] == cls)


def dependent_started(rig: tk.TreeRig) -> bool:
    return rig.marker.paths("create") == [DEPENDENT]


@pytest.mark.proves("WR-ENV-8", LABEL_8, "B", "B", "STUB", "CI")
def test_interactive_identity_blocked_no_dependent_started(tmp_path: Path) -> None:
    grant = RigGrant([LONG])
    grant.set_interactive(True)
    rig = rig_over(tmp_path, grant)
    ends = run(rig, grant)
    end = ends[tree.CREDENTIAL_UNIT]
    assert end["condition"] == "blocked" and end["code"] == codes.CREDENTIAL_INTERACTIVE
    # the exact human action names the identity; the re-send is the one that succeeds after it
    assert end["human_action"] == (
        f"Authenticate identity {IDENTITY} interactively with its issuer, then re-send."
    )
    assert end["resend"] == Resend.SUCCEEDS_AFTER_ACTION.value
    # one refresh was tried, no human flow was attempted, and the issuer's state did not change
    assert len(issues(rig)) == 1 and grant.refreshes == 0
    # no dependent started: it never ran, so it has no condition, and nothing was created for it
    assert ends[DEPENDENT]["condition"] is None
    assert not dependent_started(rig)
    answer = tk.answer_of(rig)
    assert answer.primary.node_class is NodeClass.BLOCKED
    assert answer.primary.human_action == end["human_action"]


@pytest.mark.proves("WR-ENV-14", LABEL_14, "B", "B", "STUB", "CI")
@pytest.mark.parametrize("mode", ["refreshable", "not_refreshable"])
def test_short_lifetime_refreshed_before_dependent(tmp_path: Path, mode: str) -> None:
    """A credential that does not outlive the root deadline plus the margin gets one refresh first:
    a refresh that extends it lets the dependent start after it; one that does not ends the node
    `CREDENTIAL_LIFETIME_INSUFFICIENT` (EXHAUSTED, answered BLOCKED) with no dependent started."""
    grant = RigGrant([SHORT, LONG] if mode == "refreshable" else [SHORT])
    rig = rig_over(tmp_path, grant)
    ends = run(rig, grant)
    credential = ends[tree.CREDENTIAL_UNIT]
    (issue,) = issues(rig)  # the one declared remedy, never a second refresh
    assert issue["remedy"]["code"] == codes.CREDENTIAL_LIFETIME_INSUFFICIENT
    assert grant.refreshes == 1
    if mode == "refreshable":
        assert credential["condition"] == "satisfied"
        assert ends[DEPENDENT]["condition"] == "satisfied" and dependent_started(rig)
        # the refresh happened BEFORE the dependent started
        assert first_row(rig, tree.CREDENTIAL_UNIT, "issue") < first_row(rig, DEPENDENT, "issue")
    else:
        assert credential["condition"] == "blocked"
        assert credential["code"] == codes.CREDENTIAL_LIFETIME_INSUFFICIENT
        assert ends[DEPENDENT]["condition"] is None and not dependent_started(rig)
        answer = tk.answer_of(rig)
        assert answer.primary.node_class is NodeClass.EXHAUSTED  # B4-T2 row 8
        assert str(answer.outcome) == "blocked"  # B4-T3: EXHAUSTED answers BLOCKED
        assert "deadline" in credential["human_action"]


@pytest.mark.proves("WR-ENV-14", LABEL_14, "B", "B", "STUB", "CI")
def test_lifetime_checked_against_root_deadline_not_slice(tmp_path: Path) -> None:
    """A credential that outlives every node's slice but not the ROOT deadline plus the margin is
    refreshed; one that outlives the root deadline plus the margin by a second is left alone."""
    assert SHORT > SLICE  # it would pass a check made against a slice
    grant = RigGrant([SHORT, LONG])
    ends = run(rig_over(tmp_path / "short", grant), grant)
    assert grant.refreshes == 1 and ends[tree.CREDENTIAL_UNIT]["condition"] == "satisfied"

    just_enough = RigGrant([LIMIT + timedelta(seconds=1)])
    rig = rig_over(tmp_path / "enough", just_enough)
    ends = run(rig, just_enough)
    assert just_enough.refreshes == 0 and issues(rig) == []
    assert ends[tree.CREDENTIAL_UNIT]["condition"] == "satisfied"
    assert ends[DEPENDENT]["condition"] == "satisfied" and dependent_started(rig)
