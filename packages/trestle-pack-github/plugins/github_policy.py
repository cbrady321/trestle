"""github_policy: the github pack's effective policy, and where it came from.

The recipe: read the file `TRESTLE_GITHUB_POLICY` names (none: the defaults), attach it as
`github-policy.json`, answer it. No command runs and nothing is sent to GitHub. An invalid file
is `Blocked("github.policy_invalid")`, naming the file and the key, for the operator to fix.
"""

from __future__ import annotations

from trestle.plugin import Context, trestle

from trestle_pack_github import attach_policy, gh_host, load_policy
from trestle_pack_github.results import PolicyReport  # where it is defined: the schema reads it


@trestle(packages=["trestle_pack_github"], summary_fields=["source", "sha256", "host"])
def github_policy(ctx: Context) -> PolicyReport:
    """The policy the github plugins run under: bots, triage, which checks count, and the
    guards of `github_pr_open`. `source` is the policy file (or "defaults"), `sha256` its
    digest, `host` the GitHub host (`GH_HOST`, else github.com)."""
    loaded = load_policy()
    attach_policy(ctx, loaded)
    return PolicyReport(
        source=loaded.echo.source,
        sha256=loaded.echo.sha256,
        policy=loaded.policy,
        host=gh_host(),
    )
