# Packs: bundled packs, installing packs, and owners

2026-10-22, revision 3. Design proposal; nothing here is built.

**Decisions live in [`docs/decisions/packs.md`](../decisions/packs.md).** This document cites them
by id (P-n decided, Q-n proposed, O-n open) and does not restate them.

Revision 2 took the smallest viable plan from the review (`2026-10-22-pack-plan-review.md`),
plus an informational `owner` label. Revision 3 adds the developer-experience decisions
(`2026-10-22-pack-developer-experience.md`, P-6 to P-14):

- agents may read and adapt pack source;
- pinned copies live under `$TRESTLE_HOME/packs/<owner>/<pack>/`;
- agents adapt a pack plugin under a new name;
- packs may declare what they replace;
- bundled packs must be outstanding or hackable.

It also draws on:

- `2026-10-22-pack-hooks-analysis.md`: company behaviour without forking;
- `2026-10-22-pack-organization-analysis.md`: the substrate, the per-tool split, naming and
  versioning.

Revision 1 had pack sources, tier precedence, qualified-id allowlists, per-pack env and more. It
was never committed. What it had that this revision leaves out is listed, with reasons, under
[§ Deferred](#deferred-and-cut).

Inputs:

- Trestle `master` at `fc4999d0`;
- hyper-gremlin's plugin system (`~/projects/hyper-gremlin`);
- the packs zip `~/Downloads/trestle-packs (1).zip`, cited as `zip:`. It was read only: nothing in
  it was run or imported, and no git command touched it.

## What Trestle is for, and what this must not break

Trestle is a flexible, easy to use and easy to extend, local-only service that minimizes console
calls. It is a console replacement that makes agents efficient: one MCP call runs a whole local
job and returns a bounded answer. This design is judged against that. It must:

- **Keep the zero-ceremony path.** Drop a `.py` file in a watched folder, and it is a plugin. A
  pack is for *sharing*, never a prerequisite for writing a plugin.
- **Give an agent one answer per dead end.** A missing pack, a broken library or a refused policy
  each ends in a single answer naming the operator's action, never in a chain of discovery calls.
- **Never interrupt unattended runs.** Installing or upgrading a pack does not restart the
  service, and does not stop runs unrelated to that pack.
- **Make adapting a pack cheaper than using the console.** Changing scripts as needed is cheaper
  than letting agents use the console. So a bundled pack is outstanding (it just works,
  unattended) or hackable (an agent can read it and change one file), and agents may read and
  adapt pack source (P-6, P-8).

## Headline

The owner's asks are:

1. bundled, optional packs that users choose from, as in hyper-gremlin;
2. installing packs from repositories other than Trestle's;
3. packs owned by Trestle, a company, a user or a project;
4. company-specific behaviour without forking a bundled pack (`github`).

The plan meets them with:

1. **A pack is a folder with a small `pack.toml`.** It holds `plugins/` (wrapper files) and an
   optional library.
2. **One install command:** `trestle pack install <name | path | git-url@ref>`. It covers bundled
   packs by name, local folders in place, and other repositories pinned to a tag or commit.
3. **An `owner` label** of `trestle`, `company`, `user` or `local`. It is recorded in the lock,
   shown on every plugin row and in `run_provenance`, and is informational only: no precedence
   rules hang on it.
4. **Pinned packs run from a copy made at install time.** Wrappers and library come from one
   commit. No pinned pack is served live from a checkout.
5. **No restart.** The service rescans when the lock changes, and the install waits only for live
   runs of the pack being replaced.
6. **Company behaviour comes from a policy file plus library seams.** Core needs nothing new for
   it.
7. **Bundled packs, one per tool (P-16):** `docker`, `pytest` and `migrate`, with `stack` composing them, now; then `github`, and `env` after the substrate move.

The core change is one small new module (manifest, lock and install) plus edits to
`registry.py`, `plugin_paths.py`, `admission.py` and the catalog hint.

## What transfers from hyper-gremlin

| hyper-gremlin | Trestle |
|---|---|
| First-party pattern packages in `packages/hyper-gremlin-*`, each its own distribution | Bundled packs in `packages/trestle-pack-<name>/`, released with Trestle |
| Users pick which pattern packages to install | `trestle pack install stack` installs one bundled pack; none is installed by default |
| Entry points: install = enable | **Not adopted.** Installing a package must not enable it: credential grants can't pin what an entry point loads (T5). The install command enables a pack explicitly |
| `PluginCollisionError` on a duplicate name | Two packs claiming one plugin name are refused at install. Only a deliberate local file can override a pack plugin, and that override is reported |
| Broken plugin skipped; others load | Already true per plugin (`valid: false`). The winner of a name is chosen *after* validation, so a broken file never hides a valid one |
| JSON catalog with `enabled` and `params` | A pack's policy file (for example `github-policy.toml`) |
| `PLUGIN_API_VERSION` major check | Deferred: lockstep covers bundled packs, and nothing else exists yet |

## Concepts

### Pack

```
<pack dir>/
  pack.toml          # required
  plugins/           # @trestle wrapper files, each @trestle(packages=[...]) over the library
  pyproject.toml     # optional library, pip-installed into the service venv
  src/…  tests/  README.md
```

`trestle pack new <name>` scaffolds the smallest form, `pack.toml` plus one wrapper. With
`--library`, it adds `pyproject.toml`, a package and a `FakeContext` test.

### `pack.toml`

```toml
[pack]
name = "github"                  # [a-z][a-z0-9_]*
summary = "Open, monitor and report on many GitHub PRs in one run"
library = "."                    # optional: directory holding pyproject.toml
[requires]
tools = ["gh", "git"]            # optional: checked by `trestle pack install` on the service PATH
```

Nothing else is read yet. The pack's version is its library's version. For a wrappers-only pack,
it is the git commit, or `path` for a path install.

### Owner

| Owner | Assigned how | Example |
|---|---|---|
| `trestle` | Automatically, and only for a bundled pack installed by name from the running release; nothing can claim it | `stack`, `github` |
| `company` | `--owner company` on install | An organization's pack repository |
| `user` | The default for a git or path install | A person's helpers |
| `local` | Loose files in `[plugins] paths`, and runtime `publish_plugin` | A repo's own `repo_tests.py` |

The owner is shown, not enforced. It tells an agent and the operator whose code a plugin is.
`list_plugins` and `describe_plugin` show it as `pack: "github@0.5.0 (trestle)"`, and
`run_provenance` records it.

### Collisions and folder order

The service watches, in this order:

1. `[plugins] paths`: the operator's loose files (owner `local`);
2. installed packs, from the lock;
3. `$TRESTLE_HOME/plugins/runtime/`: agent publishes. This is a new folder of their own.

Collisions are handled as follows:

- **Two packs claiming one plugin name:** refused at install, naming both packs, unless the
  newer pack declares it in `pack.toml` (P-11). `replaces = "github"` takes over a whole pack, and
  `overrides = ["github_pr_open"]` takes over single plugins. A company fork can then keep the
  bundled names.
- **A loose file with a pack plugin's name:** the file wins. The `list_plugins` row says
  `overrides: "github@0.5.0"`, and `trestle pack list` shows the override. Under the restricted
  profile, an allowlisted name served by an override is also flagged (P-14).
- **An agent publish with a pack plugin's name:** refused, because one agent's file must not
  replace a vetted plugin for every agent. The refusal points the way forward (P-10): it names the
  plugin's source path and a suggested new name, so the agent adapts the plugin under that name.
  An optional `@trestle(adapted_from="github_pr_open")` records the lineage, and `describe_plugin`
  shows it. An operator who has reviewed such a plugin can grant it credentials (P-12).
- **The winner is chosen after validation.** Today `registry.py:210` claims the name before the
  file validates, so a broken file can hide a valid one.

Today `publish_plugin` writes to `plugin_dirs[0]` (`registry.py:379-382`). The runtime folder
fixes that, so agent publishes never land inside a pack copy or the operator's folder.

## Installing

```bash
trestle pack install stack                                   # bundled, owner trestle
trestle pack install git@github.com:acme/trestle-packs.git@v1.4.0#packs/bdc --owner company
trestle pack install ~/code/my-pack                          # path, used in place, owner user
trestle pack list                                            # installed packs + available bundled ones
trestle pack remove github
trestle pack upgrade [<name>…]
```

`install` runs these steps:

1. **Resolve the pack's location.**
   - **Bundled:** the running release's `packages/trestle-pack-<name>/`.
   - **Git:** clone at the tag or commit (a branch is refused), using the operator's own git
     credentials; Trestle stores no token. The optional `#subdir` picks one pack in a multi-pack
     repo.
   - **Path:** the directory itself.
2. **Check the pack.** Read `pack.toml`, look for `requires.tools` on the service `PATH`, and
   refuse names that collide with an installed pack.
3. **Wait for live runs of this pack's plugins only.** On upgrade, the plugins being replaced.
   With `--no-wait`, refuse while any are live.
4. **Copy pinned packs** (bundled and git) into `$TRESTLE_HOME/packs/<owner>/<pack>/`, for
   example `packs/trestle/stack/` and `packs/company/bdc/`. Then install the library
   **non-editable from that copy** (P-7).
   - The copy holds the wrappers and the library source from one commit: the exact code that
     runs.
   - It is the documented place where agents read pack source, with their own file tools
     (P-6). The rest of `$TRESTLE_HOME` stays off-limits.
   - Path installs stay live, for development: an editable library and wrappers served in place.
     The rescan also watches the library's file times, so a library edit republishes with no
     restart (P-13).
   - `--replace <pack>` lets a path install swap out a bundled pack, for example a working copy
     of `stack` being fixed.
5. **Write the lock** (`$TRESTLE_HOME/packs.lock`). It records each pack's name, owner, location,
   ref and commit, version, wrapper folder and install time. Writing it changes its mtime.
6. **The service rescans.** The registry adds the lock's mtime to its scan signature
   (`registry.py:151-164`), and republishes the changed pack without a restart.

Bundled libraries install with `--no-deps` for `trestle*` names, so pip never looks for them on
PyPI.

Upgrades need two more things:

- **Credential grants.** Grants pin `package_digests` (Decision 2, G2), so upgrading a library
  drops its plugins' grants. `trestle pack upgrade` lists the grants it dropped and exits
  non-zero, unless given `--regrant`, which re-pins only grants that existed before the upgrade.
- **Migration of existing installs.** `install.sh` and `trestle init --upgrade` rewrite an
  `examples/packs` entry in `[plugins] paths` into installed `docker`, `pytest`, `migrate` and `stack` packs. Otherwise the folder
  vanishes, and with it the plugins: a missing folder is only logged (`plugin_paths.py:73-80`).

## Agent surface

There are no new tools.

- `list_plugins` and `describe_plugin` rows gain `pack` (`"github@0.5.0 (trestle)"`), and
  `overrides` where a loose file or a declared override shadows a pack plugin.
- `describe_plugin` gains `source_path`, where the plugin's wrapper is: under
  `$TRESTLE_HOME/packs/<owner>/<pack>/`, or the path install's own folder. It also gains
  `adapted_from` when the plugin declares it. An agent reads the source with its own file tools,
  with no new tool and no new resource (P-7).
- The guide's packs topic and `skills/trestle/SKILL.md` say pack source is open to read and adapt,
  while Trestle's own source and the rest of its home are not (P-6).
- `admission.plugin_not_found` for a plugin that an available bundled pack provides names that
  pack and the operator's line: `the operator runs: trestle pack install github`. The bundled
  tree lists its plugin names, so no import is needed. That is one answer, with no
  `list_plugins` or guide round trip.
- A pack whose library is missing gives `import_failed` with the operator's command (the 2d
  wording), in place of today's string match on `trestle_packs` (`plugin_validate.py:17`,
  `registry.py:282`, `admission.py:202`).
- The `status` view from the 2a–2d work lists installed packs, with `grants_stale` after an
  upgrade.
- A policy refusal the agent can fix (a default-branch push, a title pattern) is
  `Failed(..., resend="will_not_succeed")`, never `Blocked`. `Blocked` means "stop and wait for a
  person", and fits only, say, a missing `gh` login.

## Bundled packs

Bundled packs live in this repo under `packages/trestle-pack-<name>/`. They are versioned in
lockstep with Trestle, from one source of truth, checked by a gate test.

- **The collection is called "bundled packs"** in prose, with owner `trestle`.
- **A version bug must be fixed first.** The version string still reads `0.3.1` in every tag from
  v0.3.1 to v0.4.1, in `pyproject.toml` and `trestle/__init__.py`. Snapshots read their runtime
  version from it (`snapshots.py:171`). This is a separate bug, to fix first.

**The bar for a bundled pack (P-8, P-9).** Each bundled pack is outstanding or hackable, and a
gate test checks the criteria in `pack-developer-experience.md`. The wrapper shows the recipe:
each command as a visible `ctx.exec` argv, the step order and the verdict. The library holds the
mechanism behind an exported `__all__`. An agent adapts a pack by changing one file.

`stack` does not meet that bar today, so it is fixed before it is bundled:

- `docker_stack`'s documented minimal form always raises "stack spec has no services"
  (`docker_stack.py:49-50`, `spec.py:75-81`, `runner.py:50-53`);
- a missing Docker is a bare `RuntimeError` (`runner.py:40-42`), where it should be `Blocked`;
- there is one compose file only, with `build=False` hard-coded;
- `pytest_run` runs in the service venv rather than the project's interpreter, and raises on
  failing tests (`examples/packs/pytest_run.py`);
- no test calls the wrappers.

| Pack | Contents | When |
|---|---|---|
| `docker` | `docker_stack`: compose up in waves, with compose files, profiles and per-service logs | First, rebuilt to the bar (P-16, P-17) |
| `pytest` | `pytest_run`: out of process, on the project's interpreter; failing tests are a result, not a crash | First, rebuilt to the bar |
| `migrate` | `migrate_apply`: Alembic or Yoyo, out of process | First, rebuilt to the bar |
| `stack` | `stack_pipeline` (was `integration_pipeline`): compose up, optional migrate, pytest, then one teardown. It `requires.packs` the three above | First |
| `env` | `reference_env`: the reference environment tree, a worked composite-workflow example (P-22) | After the substrate move |
| `github` | The generic core of the zip's `github_pr` plus a policy file | Next: read-only plugins first, then `github_pr_open` with its push guards |

### `github`, and company behaviour without forking

Almost everything company-specific in the zip's `github_pr` is data:

- the bot list (`comments.py:14`);
- the triage keywords;
- the fixed commit message (`open.py:96`);
- the `origin` remote;
- the template paths;
- a `github.com`-only URL pattern (`target.py:8`);
- "every check is required" (`monitor.py:194`);
- a 90 s grace period (`manifest.py:66`).

So, in order:

1. **A policy file first.** `github-policy.toml` is named by one `[plugins.env]` line
   (`TRESTLE_GITHUB_POLICY`). Unknown keys are refused. Every run reports the policy digest and
   attaches the effective policy, so company behaviour is visible to agents.
2. **Library seams for code-shaped variation.** These are the `OpenBackend` and `GhTransport`
   protocols the zip already has (`open.py:42-53`), plus a `CommentTriage` protocol. A company
   pack customizes by wrapping: its own wrappers declare
   `packages=["trestle_pack_github", "acme_github"]`, so digests, provenance checks and grant pins
   cover the company code with no core change.
3. **Config-bound hooks: deferred** until a second bundled pack needs them (hooks analysis §3).

The fixes it needs before shipping are unchanged from revision 1:

- the comments op calls a `gh graphql` subcommand that does not exist, and expects JSON from
  `gh pr diff`;
- annotations are read from the wrong endpoint;
- it pushes before checking the branch, and can edit other people's PRs;
- its raw subprocess calls need moving to `ctx.exec`;
- the `ctx.deadline` workaround goes, in favour of `ctx.remaining()`;
- a failed `gh` login becomes `Blocked`;
- the digest needs top-level `ok` and counts;
- the employer's traces (spec ids, aliases) are stripped.

The proposed defaults are generic bot detection, required checks from
`gh pr checks --required` (unverified), and "no checks scheduled" blocking.

### A generic project launcher (P-23: not now)

**What it would be.** The zip's `hibob_project_up` starts a project's app on the host, outside
Docker, for example a Python API or a Gradle service. It installs or repairs the app's
dependencies (poetry, uv or pip; the Gradle wrapper), starts it, and waits until it is healthy:
the process is alive, its log shows no fatal pattern, and its health URL answers. A generic
`project_up` pack would keep that engine. The company's specifics would move into a profile file,
as `github` keeps its specifics in a policy file: which commands start which app, on which port,
and what counts as healthy.

**Why not now.**

1. **Apps would die with the run.** The point of such a launcher is to leave an app running for
   the next test. Trestle stops every process a run started when the run ends (K-8), so an app
   started by `project_up` would be killed as soon as the plugin returned. Keeping it alive needs
   a "durable" process lifetime for plain plugins, which core deliberately does not offer yet
   (review item R7, deferred).
2. **Its process handling is unsafe as written.** It kills whatever is listening on a known port,
   and signals recorded process ids that may since belong to an unrelated program. It must be
   rebuilt on core's process-identity checks first.
3. **It can install into the wrong Python.** Its pip fallback may install into Trestle's own
   environment.
4. **The defaults are company-specific.** Ports, start commands and a migration script are the
   employer's (P-3).

**What it would take:** the durable-lifetime decision, the ownership rewrite, a profile format,
and a P-8 rebuild. Until then a test suite can start an app *within* one run, and the app stops
with it.

### The rest of the zip

- **Merged into `stack`'s library:** `keep()`, and the docker additions (several compose files,
  profiles, `no_deps`, stopping individual services).
- **Not used:**
  - the zip's volume-deleting teardown;
  - its `dag`, `readiness`, `pytest` and `migrate`, which the repo's versions supersede or
    duplicate;
  - the shell CLIs and shims.
- **Not adopted, as company-specific:** `bdc_compose_up`, `hibob_project_up`, `local_system_up`,
  `chain`, `logs`, `http_stub` and `kafka`. A company installs those from its own repository with
  `--owner company`.
- **No personal data copied.** None of the zip's personal or organizational data (paths, repo
  names, an email address) may be copied into bundled packs or fixtures.

## Deferred and cut

| Revision 1 concept | Now | Why |
|---|---|---|
| Pack sources, the `trestle-packs.toml` index, `trestle pack source …` | Cut | A git URL with a ref and an optional `#subdir` does the same job with no index file |
| Tier precedence (`local > user > company > trestle`) | Replaced | `owner` is informational. Pack-to-pack collisions are refused, and only a local file overrides |
| Qualified-id allowlists, `<source>:<pack>/<plugin>` | Deferred | Bare names stay unique because collisions are refused |
| Per-pack `env` tables | Deferred | `[plugins.env]` is enough; the github policy file is named there |
| `surface` version checks | Deferred | Lockstep covers bundled packs; add the check when the first non-bundled pack needs it |
| `requires.env`, `requires.credentials`, `[effects]` | Deferred | No consumer yet. `requires.tools` stays, because install can check it cheaply. `requires.packs` is back (P-16), because `stack` needs the tool packs |
| Reserved `[hooks]` table and config-bound hooks | Deferred | Policy plus seams cover `github` |
| Substrate into core, dissolving `trestle_packs` | Decided (P-15), after the follow-ups | The foundation stays out of agents' reach; moved after the dependency follow-ups below, so code about to be rewritten is not moved first |
| One bundled pack per tool (`docker`, `pytest`, `migrate`) | Decided (P-16) | Users choose which tools to install, as in hyper-gremlin |
| Restart on install | Cut | A rescan replaces it |

The **dependency follow-ups**, from the organization analysis:

- run pytest and migrations out of process, on the project's own interpreter, through the core
  `testrun` and `toolchain` adapters;
- drive Docker through the core engine instead of `python-on-whales`.

Then `stack` needs almost no Python dependencies, and the substrate move and the per-tool split
can be reconsidered.

## Delivery order

1. **0.5.2 release:** the version string becomes 0.5.2 (P-18), and this design and the decision
   record ship with it.
2. **Fix the tool plugins first, in place** (owner, 2026-10-22). This happens right after 0.5.2,
   in its own session, before any installer work. The plugins still live in `examples/packs/`
   over `packages/trestle-packs`, so agents get working plugins now. Later steps only move them.
   - `docker_stack`:
     - the documented minimal form works;
     - a missing Docker is `Blocked`;
     - compose files, profiles, `no_deps`, stopping single services and `build` are supported;
     - it drives the `docker compose` CLI through `ctx.exec`, not `python-on-whales`.
   - `pytest_run` runs out of process, on the project's interpreter, and returns failing tests as
     a result.
   - `migrate_apply` runs out of process.
   - `integration_pipeline` is renamed `stack_pipeline` (P-17).
   - Every wrapper has a test that calls it.
   - The recipe sits in the wrapper and the mechanism in the library (P-8).

   This covers the old "dependency follow-ups" for these plugins.
3. **Let the 2a–2d work land**, so the `status` view and the operator-command hint wording exist.
4. **The agent-facing wording change (P-6):** the server instructions, guide, skill and
   `docs/agents.md`. Pack source becomes open to read and adapt.
5. **The core mechanism:**
   - install, the lock and the rescan, including library file times for path installs,
     `--replace` and `requires.packs`;
   - the owner-subdivided pack copies;
   - collision refusal unless declared (`replaces`, `overrides`), with the winner chosen after
     validation;
   - the runtime publish folder and the redirecting refusal;
   - `source_path` and `adapted_from` on `describe_plugin`;
   - the `plugin_not_found` hint;
   - grant pins for reviewed runtime publishes;
   - configuration digests in provenance (P-20);
   - the restricted-profile flag;
   - the `examples/packs` migration.
6. **Move the fixed plugins into bundled packs** `docker`, `pytest`, `migrate` and `stack`
   (P-16). They already meet the bar after step 2.
7. **`github`:** read-only plugins with the policy file first; `github_pr_open` last, with its
   push guards. Hooks only once their design passes P-19's review.
8. **The substrate move into core** (P-15), with the `trestle-env` split (P-22) and the `env`
   pack.

Each step lands in its own PR, with `FakeContext` tests and, where a host tool is needed, a live
check gated like the docker node.

## Decisions

All decisions are recorded in [`docs/decisions/packs.md`](../decisions/packs.md):

- **P-1 to P-23, decided 2026-10-22.** These include:
  - the owner label, installs from any repository, and open pack source with the foundation
    closed;
  - one bundled pack per tool, and the plugin names;
  - lockstep versions starting at 0.5.2;
  - `github`'s policy, seams and design-gated hooks, its defaults, and configuration digests in
    provenance;
  - the `trestle-env` split.
- **P-23 (was Q-8), decided by the owner:** no generic project launcher now (see above).
- **Delivery:** the decision record and these proposals ship in 0.5.2.
