# Decision record: packs

**Status:** P-1 to P-23 decided 2026-10-22; none implemented.
**Date:** 2026-10-22
**Related:**
- [`service-and-credentials.md`](service-and-credentials.md): Decision 2 (grants pinned to
  digests) and Decision 3 (MCP only), whose agent-facing wording P-6 amends;
- [`plugins.md`](../plugins.md) and [`packs.md`](../packs.md);
- the proposals this record settles, under `docs/proposals/2026-10-22-*`:
  - `trestle-pack-catalog-design.md`, the design, which cites this record by id;
  - `pack-plan-review.md`;
  - `pack-hooks-analysis.md`;
  - `pack-organization-analysis.md`;
  - `pack-developer-experience.md`.

Each decision has a stable id. A later decision that changes one says so in its own entry, and
the earlier entry is marked superseded; nothing is deleted.

## What Trestle is for

Trestle is a flexible, easy to use and easy to extend, local-only service that minimizes console
calls. It is a console replacement that makes agents efficient. Packs serve that goal in three
ways:

- **Ready-made jobs:** they give agents ready-made, unattended jobs (bundled packs).
- **Sharing:** they let people and companies share plugins.
- **Adapting instead of the shell:** when a pack does not fit, the agent adapts the pack rather
  than falling back to the shell.

The owner's principle: changing scripts as needed is cheaper than letting agents use the
console, so a bundled pack must be either outstanding or hackable.

## The problem

- **Packs are anonymous.** Today a pack is a library pip-installed into the service venv plus a
  wrapper folder typed into `[plugins] paths`. Nothing records who owns a pack, which version
  runs, or what it needs.
- **Name collisions are silent.** The first folder wins (`registry.py:205-210`), with one log
  line, and the winner is chosen before validation, so a broken file can hide a valid one.
- **Agents are cut off from pack code.** They may not read a pack's code under the current
  wording, and cannot adapt a pack plugin.
- **The one pack is not up to standard.** The shipped `stack` plugins fail in documented use:
  - `docker_stack`'s minimal form always raises "stack spec has no services";
  - a missing Docker is a bare `RuntimeError`;
  - `pytest_run` raises on failing tests.

## Options considered

A first design with pack sources, a source index, owner tiers with precedence, qualified-id
allowlists, per-pack env and surface checks was reviewed (`pack-plan-review.md`). The review
found it about twice the size the owner's asks need, with four defects that break unattended
runs. A smaller plan replaced it (P-4, P-5). Serving pack source through a new MCP resource was
considered and rejected in favour of a documented folder (P-7).

## Decisions

**P-1. Pack identity on snapshots.** At publication, each snapshot is stamped with its pack name,
version and owner, read from `packs.lock`. `list_plugins`, `describe_plugin` and
`run_provenance` show it. This is a narrow revival of R9; recording `importlib.metadata`
versions in the host is still out of scope.

**P-2. Bundled packs come from the release checkout only.** A bundled pack installs by name from
the running release's `packages/trestle-pack-<name>/`. No wheels go to PyPI or GitHub release
assets for now.

**P-3. No company-specific pack is bundled.** Of the company fork's packages, only the generic
core of `github_pr` becomes a bundled pack (`github`). The rest belongs in the company's own
repository.

**P-4. Packs install from any repository.** `trestle pack install <name | path | git-url@ref[#subdir]>`:

- **Git:** a git install is pinned to a tag or commit, and a branch is refused. The clone uses
  the operator's own git credentials, and Trestle stores no token.
- **Path:** a path install is used in place, for development.
- **One command:** there are no pack sources, index files or `source` commands.

**P-5. Owner is an informational label.** Every installed pack has an owner:

- `trestle`: assigned automatically to bundled packs, and claimable by nothing else;
- `company`: set by `--owner company`;
- `user`: the default for git and path installs;
- `local`: the operator's loose files and agent publishes.

The owner is shown on every plugin row and in provenance. No precedence rule depends on it.
*Supersedes* the 2026-10-22 morning decision "the more specific tier wins
(`local > user > company > trestle`)".

**P-6. Pack source is open to agents; Trestle's core and home stay closed.**

- Decision 3 itself never restricted reading: "Execution goes through Trestle. Reading does
  not." (`service-and-credentials.md:406-408`). The "never from source" rule lives only in
  agent-facing wording.
- That wording (`trestle/server/authoring.py:61-66`, `skills/trestle/SKILL.md`, the guide) is
  amended. Agents may read and adapt pack source, meaning the wrappers and pack libraries under
  `$TRESTLE_HOME/packs/`. Trestle's own source and the rest of `$TRESTLE_HOME` stay off-limits.
- Execution is unchanged: it still goes through Trestle.

**P-7. Pack source is read with ordinary file tools, from a documented folder.**

- **Pinned packs** (bundled and git) are copied at install time to
  `$TRESTLE_HOME/packs/<owner>/<pack>/`, for example `packs/trestle/stack/` and
  `packs/company/bdc/`.
  - Each copy holds the wrappers and the library source from one commit: the exact code that
    runs.
  - The library is installed into the service venv non-editable from that copy.
- **A path install** stays where it is.
- **`describe_plugin` gives the plugin's source path** in both cases.
- **No new MCP resource.** Trestle and the agent share one machine, so file tools suffice.
- *Rejected:* a `trestle://source/{plugin}` resource. It is redundant on a local-only service,
  and more to build and keep.

**P-8. Recipe in the wrapper, mechanism in the library.** In a bundled pack:

- the wrapper shows the recipe: each command as a visible `ctx.exec` argv, the step order and
  the verdict;
- the library holds the mechanism, behind an exported `__all__` that is the pack's public API;
- a gate test checks the criteria for "outstanding" and "hackable" listed in
  `pack-developer-experience.md`. An agent can then adapt a pack by changing one file.

**P-9. `stack` meets the bar before it is bundled.** It must fix the minimal form of
`docker_stack`, give a `Blocked` answer for missing Docker, add compose variations, have tests
that call each wrapper, and run pytest on the project's interpreter, returning results instead of
raising on failing tests.

**P-10. Agents adapt a pack plugin under a new name.**

- An agent publish that reuses a pack plugin's name stays refused. The refusal names the
  plugin's source path (P-7) and a suggested new name, so the agent's next step is clear.
- An optional `@trestle(adapted_from="<plugin>")` records the lineage, and `describe_plugin`
  shows it.

**P-11. A pack may declare what it replaces.**

- `pack.toml` may declare `replaces = "<pack>"` for a whole pack, or `overrides = [<plugin>…]`
  for single plugins. A company fork then keeps the bundled plugin names.
- Undeclared collisions between packs are refused at install.
- A loose file in `[plugins] paths` still overrides a pack plugin.
- Every override is shown on the winner's row.

**P-12. An operator may grant credentials to a reviewed agent publish.**

- `trestle credentials grant <name> <plugin>` may pin a runtime-published plugin's digests once
  the operator has reviewed it.
- This amends Decision 2's "agent-published plugins never receive credentials" to "never without
  an operator pin". The pin drops on any change, as for every grant.

**P-13. Path installs stay live, and can replace a bundled pack.**

- For a path install, the rescan signature includes the library's file times, so a library edit
  is republished without a restart.
- `trestle pack install <path> --replace <pack>` swaps a bundled pack out for a working copy.

**P-14. The restricted profile flags overrides.** Under `[profile] mode = "restricted"`, an
allowlisted plugin name that is now served by an override (P-11, or a loose file) is flagged in
`list_plugins`, in `trestle pack list` and in the service log.

**P-15 (was Q-1). The foundation goes into core, out of agents' reach.**

- The substrate moves into core as `trestle.workflow.adapters` and `trestle.workflow.fakes`:
  `process`, `container`, `fakes`, `grant`, `testrun`, `toolchain`, plus `provision` (P-22).
  `trestle_packs` is dissolved, and its name is reserved.
- The line is P-6's: **pack code is open to read and adapt; the foundation is not.** It is
  Trestle's own code, and agents build on it through its public API and never edit it.
- *Sequencing:* after the dependency follow-ups (pytest and migrate out of process, Docker on the
  core engine), so code about to be rewritten is not moved first.

**P-16 (was Q-2). One bundled pack per tool.**

- The bundled packs are `docker`, `pytest` and `migrate`, and `stack` composes them.
- `requires.packs` comes back from the deferred list, for this reason: `trestle pack install
  stack` installs the tool packs it needs from the same release.
- `install.sh` and `trestle init --upgrade` turn an existing `examples/packs` entry into all
  four. A fresh install adds no packs and prints the available list, so users choose, as in
  hyper-gremlin.

**P-17 (was Q-3). Plugin names.**

- `docker_stack` (pack `docker`), `pytest_run` (pack `pytest`) and `migrate_apply` (pack
  `migrate`) keep their names. Each already carries its pack's prefix.
- `integration_pipeline` becomes `stack_pipeline` (pack `stack`). It runs a compose stack up,
  applies optional Alembic migrations, runs pytest, and tears the stack down exactly once.
- The pack prefix is a convention, not a test.
- *Supersedes* the 2026-10-22 decision to rename all four (`stack_up`, `stack_pytest`,
  `stack_migrate`, `stack_pipeline`). Renaming breaks idempotency keys and `after=` chains.

**P-18 (was Q-4). Lockstep versions, starting at 0.5.2.**

- Bundled packs carry Trestle's version, read from one source of truth. A gate test fails when
  any declared version disagrees, or falls behind the newest release tag.
- The stale `0.3.1` (unchanged in every tag from v0.3.1 to v0.4.1) is replaced by **0.5.2** in
  that release.

**P-19 (was Q-5). `github` customization: policy, then seams, then hooks, with hooks held to a
design bar.**

- `github` ships first with a policy file (named through `[plugins.env]`) and library seams
  (`OpenBackend`, `GhTransport`, `CommentTriage`).
- Config-bound hooks are **supported**. They are built only once their design passes an
  adversarial review against all of the following:
  1. **Pinned.** A bound hook's package joins the plugin's declared packages, so digests,
     snapshot identity, `provenance_mismatch` and credential-grant pins cover it.
  2. **Child-only.** Hook code runs only in the plugin's child process, never in the host.
  3. **Installed packs only.** A binding names a hook in an installed pack, never an arbitrary
     import path.
  4. **Typed and versioned.** Each hook point is a typed protocol with its own version, under
     the same deprecation policy as the pack's API.
  5. **Visible.** `describe_plugin` and each run's result name the hooks bound.
  6. **Isolated.** A failing hook ends the run with a hook-named code, never a silent fallback.
  7. **Testable.** Hook implementations can be tested with `FakeContext`.
- A hook design that cannot meet all seven is not built. Policy and seams carry the load until
  one can.

**P-20 (was Q-6). Behaviour-changing configuration is part of provenance.**

- A pack lists the environment variables that name its configuration files in
  `pack.toml [config] files`, for example `["TRESTLE_GITHUB_POLICY"]`.
- At admission, the **host** reads each named file's digest and records it in the run's
  provenance. The host does it, not the plugin, so a plugin cannot misreport it.
- The plugin also echoes the digest and attaches the effective policy, so the agent sees it in
  the result.
- Two runs with the same snapshot and the same configuration digests ran the same code with the
  same settings.

**P-21 (was Q-7). `github` defaults: safe and generic.**

| Area | Default | Policy can |
|---|---|---|
| Bots | GitHub's own signal: author type `Bot`, or a login ending `[bot]`. No vendor names built in | Add logins and patterns |
| Required checks | Branch protection's required checks, via `gh pr checks --required` | Add or drop named checks |
| No checks scheduled | After the grace period: its own verdict, `no_checks`, never counted as passed (`ok: false`) | Opt in: `no_checks = "pass"` |
| Push | Never the default branch or a detached HEAD; commit only the listed paths; the commit message comes from the manifest, with no built-in default | Set a branch name pattern |
| Existing PRs | Edited only with `update_existing: true`, and only PRs the acting identity opened | Allow editing any PR |
| Host | `github.com`; GitHub Enterprise through `GH_HOST` in `[plugins.env]` | — |
| Waits | Bounded by `ctx.remaining()`; poll intervals from policy | Set intervals and grace |

A policy refusal the agent can fix is `Failed` with `resend: will_not_succeed`. `Blocked`
covers only what needs a person, such as a missing `gh auth login`.

**P-22 (was O-1). `trestle-env` is split.** This was decided by the design author, on the
owner's delegation.

- **Into core, with P-15:** `provision`, its fake and the trusted catalog model (`catalog/`,
  `closure`, `realization`, `schema`, `stages`). They are foundation, and security-relevant,
  so they stay out of agents' reach.
- **Into the bundled pack `env`:** the reference environment tree and its plugin,
  `reference_env`. It is a worked example of a composite workflow, and should be readable and
  adaptable. The plugin keeps its name, per P-17's reasoning.
- *Rejected:*
  - all into core: it would put demo data such as pinned images into core;
  - all as a pack: it would put the trusted catalog where agents may edit it;
  - test-only: operators would lose a working example.

**P-23 (was Q-8). No generic `project_up` pack now.** Decided by the owner, 2026-10-22, on the
design doc's § "A generic project launcher":

- apps it starts would be stopped when the run ends (K-8), and core offers no durable lifetime
  for plain plugins yet (R7, deferred);
- it kills by port and signals recorded pids without core's process-identity checks;
- its pip fallback can install into Trestle's own environment;
- its defaults are company-specific (P-3).
- *Revisit* once a durable-lifetime decision exists; it then needs the ownership rewrite, a
  profile format and a P-8 rebuild.

## Delivery

- **Shipped in 0.5.2:** this record and the pack proposals (owner, 2026-10-22).
- **Next, right after 0.5.2, in its own session:** fix the tool plugins in place (`docker_stack`,
  `pytest_run`, `migrate_apply`, `integration_pipeline` → `stack_pipeline`) to the P-8 and P-9 bar,
  before any installer work (owner, 2026-10-22). The design's delivery order, step 2, has the
  detail.
- **Not yet built:** the decisions themselves; the design's delivery order applies.

## Consequences

- **The agent-facing wording changes** (P-6): the server instructions, the guide index and topics,
  `skills/trestle/SKILL.md` and `docs/agents.md`.
- **New and changed agent-visible behaviour**, all with no new MCP tool:
  - `describe_plugin` gains `source_path`, `pack` and `adapted_from`;
  - `list_plugins` rows gain `pack` and `overrides`;
  - `admission.plugin_not_found` names a missing bundled pack and the operator's command;
  - the same-name publish refusal names the source and a new name.
- **Core changes:**
  - a small `trestle/packs/` module (manifest, lock, install);
  - the registry:
    - the rescan signature includes the lock's mtime, plus the library's file times for path
      installs;
    - the winner of a name is chosen after validation;
    - collisions are refused unless declared;
    - agent publishes get their own folder;
  - grants that can be pinned to runtime publishes;
  - the restricted-profile flag.
- **`stack` is rebuilt to the P-8 and P-9 bar** before it ships as a bundled pack.
- **Upgrades:**
  - `install.sh` and `trestle init --upgrade` rewrite an `examples/packs` entry into an
    installed `stack`;
  - `trestle pack upgrade` reports the grants it dropped.
