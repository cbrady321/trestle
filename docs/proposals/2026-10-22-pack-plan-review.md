# Review: the pack plan (catalog, hooks, organization)

2026-10-22. Review only; nothing here is built, and no plan document was edited. Inputs:

- `2026-10-22-trestle-pack-catalog-design.md` (cited `catalog:`), `2026-10-22-pack-hooks-analysis.md`
  (`hooks:`), `2026-10-22-pack-organization-analysis.md` (`org:`);
- Trestle at `fc4999d0`, plus the uncommitted `install.sh` edits in the working tree;
- `docs/decisions/service-and-credentials.md` (`decision:`), `skills/trestle/SKILL.md`,
  `docs/agents.md`, `docs/packs.md`, the 2026-10-19 pack documents, the 2a–2d design;
- hyper-gremlin `scripts/install_first_party_plugins.sh` and `packages/*/pyproject.toml`.

The zip was not opened. Facts are cited by file and line; anything marked *inference* has not
been checked by running anything.

## Headline

**The direction is right. The plan is about twice as large as the owner's three asks need, and it
has four defects that would break unattended runs.** The three documents introduce about fifteen
concepts. Five of them do real work now: a pack folder with a small manifest, an install command,
a lock, bundled packs, and a policy file for `github`. The rest is package-manager machinery for a
local tool with one consumer: sources as managed objects, an index file, four tiers chosen by
flag, qualified ids in allowlists, per-pack env, surface versions, `requires.packs`, `[effects]`,
surface tiers, reserved `[hooks]`. It should be deferred, and most of it may never be needed.

The defects, each covered below:

1. The install sequence runs pip **before** it drains, and it restarts the whole service for
   every pack operation.
2. Serving wrappers live from a checkout, with the library installed separately, sets up
   provenance-mismatch storms.
3. Putting lock folders first in the watched-folder list makes an agent's `publish_plugin` write
   into a pack checkout (`registry.py:379-382`).
4. Upgrading would silently drop the `examples/packs` plugins of every operator who listed them.

The organization document gives a sound argument for moving the substrate into core. That move is
not on the critical path of any owner ask, though, and its argument for splitting the packs per
tool disappears once its own follow-ups land.

## Fit to objectives

### Concepts, and whether each earns its place now

| Concept | Source | Verdict |
|---|---|---|
| Pack = folder + `pack.toml` + `plugins/` + optional library | catalog:93-114 | **Keep**, with a minimal manifest: `name`, `summary`, optional `library`, optional `requires.tools` (read by doctor) |
| `trestle pack install` / `remove` / `list` | catalog:216-227 | **Keep.** This is the hyper-gremlin "choose what to install" ask |
| `packs.lock` | catalog:269-286 | **Keep, small**: per pack, its id, wrapper dir, commit, version and library digest. The service needs the pinned state without parsing manifests |
| Bundled packs, versioned in lockstep | catalog:43-46, org §4 | **Keep.** Omit `version` from bundled `pack.toml`; the lock takes `trestle.__version__` |
| Narrow R9 stamp (pack@version in provenance) | catalog:309-318 | **Keep** (owner decision 1); it is cheap once a lock exists |
| Reserved names (`trestle-pack-*`, `trestle_packs`) | catalog:349-357 | **Keep.** Cheap, and it closes F13 |
| Policy file for `github` | hooks §3 | **Keep**, named through the existing `[plugins.env]`, with no core change |
| Library seams as a documented convention | hooks §3 layer 2 | **Keep** as documentation. Make no stability promise before 1.0 |
| Source index `trestle-packs.toml` | catalog:146-162 | **Cut.** Find `*/pack.toml` at depth 2 or less. An index is a second file that must agree with the folders |
| Sources as managed objects (`source add/list/update/remove`) | catalog:218-221 | **Defer.** `pack install <git-url>@<ref>` clones once per install. A source registry is a package manager Trestle would then have to maintain |
| Four tiers set with `--tier` | catalog:177-199 | **Reshape.** Keep owner decision 3, but derive the tier from the kind (built-in → `trestle`, git → `company`, folder → `user`, `[plugins] paths` → `local`), with no flag. Collisions between packs are refused at install time, so precedence only ever decides "loose file versus pack" |
| Qualified ids, and allowlists that name them | catalog:201-208, 194-195 | **Defer.** One `pack` field on each row is enough. Refusing collisions at install makes allowlist shadowing moot |
| `shadows` on the winner | catalog:192 | **Keep, narrowed** to the one legal shadow: a loose `local` file over a pack plugin |
| Per-pack `env` table | catalog:288-293 | **Defer.** The least-privilege argument does not hold: plugins run unsandboxed as the same user (`docs/overview.md`, "Out of scope") and can read `config.toml`. `[plugins.env]` is already read at every run (`conductor.py:204-205`) |
| `surface = "1"` and `API_VERSION` | catalog:77, 386 | **Defer the check.** It is always true until a surface 2.0 exists. Reserve the key |
| `requires.packs` | catalog:130, 141-144 | **Defer.** Only `stack` needs it, and only under the per-tool split, which this review advises against for now |
| `requires.env`, `requires.credentials`, `[effects]` | catalog:126-135 | **Defer.** Doctor niceties and G4, which the owner deferred (`2026-10-19-pack-core-updates-plan.md:76`) |
| `trestle pack doctor` | catalog:226 | **Fold** into `trestle doctor` as a packs section. One doctor |
| `trestle pack new` | catalog:227 | **Keep, minimal**: `pack.toml` + one wrapper + one test. A library only with `--library` |
| Surface tiers, deprecation runway, library AST scan | org §6 | **Defer** to 1.0. Org itself says names may break freely in 0.x (org:394-395) |
| `[hooks]` reserved in `pack.toml` | org:295 | **Cut.** Do not reserve keys for a deferred feature |
| `trestle://guide/packs` (live state) | catalog:304-307 | **Move** into the 2b `status` view and into the `plugin_not_found` message. A guide is static documentation |
| Bound hooks, policy digest in provenance | hooks §3 layer 3, §4 | **Defer** (agreeing with hooks) |

### The smallest viable plan

1. **Manifest.** `pack.toml` holds `name`, `summary`, optional `library` (a path to a
   `pyproject.toml` directory) and optional `requires.tools`. Nothing else is read yet.
2. **One install command.** `trestle pack install <name | path | git-url@ref>`:
   - a bare name is a bundled pack in the running release;
   - a path is used in place, for development;
   - a git URL is cloned at a tag or sha into `$TRESTLE_HOME/packs/`.

   For pinned kinds (bundled and git), the command **copies** the `plugins/` folder into
   `$TRESTLE_HOME/packs/<id>/` and installs the library non-editable from the same commit. It
   writes the lock and touches it. The registry includes the lock's mtime in its scan signature,
   so it republishes with no restart. Before pip runs, the command waits only for live runs of
   that pack's plugins.
3. **Registry.** Watched folders are `[plugins] paths` first, then the lock's packs. Two packs that
   claim one plugin name are refused at install. A loose file over a pack plugin wins, and the win
   is reported. The winner is chosen after validation.
4. **Agent surface.** Add a `pack` field on `list_plugins` and `describe_plugin` rows. When the name
   belongs to an available pack, `admission.plugin_not_found` names that pack and the operator's
   command. The `status` view lists the installed packs. `run_provenance` carries pack@version.
5. **Bundled packs.**
   - `stack`: today's four plugins, **with their names unchanged**, until the follow-ups below
     decide their implementation.
   - `github`: later, read-only plugins first, with the policy file.
6. **Upgrades.** `install.sh` and `trestle init --upgrade` rewrite an `examples/packs` entry in
   `[plugins] paths` into an installed `stack`.
7. **Company behaviour.** A policy file plus library seams, with nothing in core.

That is one small core module (manifest, lock, install) and edits to `registry.py`,
`plugin_paths.py`, `admission.py` and the catalog hint. Measured against the catalog's own table
(catalog:384-399), that is about one M and four S changes instead of two M and eleven S. It meets
all three asks: optional bundled packs, packs from other repositories, and company behaviour
without forking `github`.

## Agent experience

| Case | Plan as written | What the agent does | Fix |
|---|---|---|---|
| A plugin's pack is not installed | `plugin_not_found`; a `catalog_hint` only when config enables the pack (catalog:282, 301) | `run` refused → `list_plugins` → read `guide/packs`: three calls to learn "ask the operator" | `plugin_not_found` names the providing pack and the line `trestle pack install github`: one answer, no extra call. The lock or the bundled tree already lists the plugin names; no import is needed |
| Pack installed, library missing | Generic per-pack hint (catalog:395) | `import_failed` plus the hint | Good. The hint must carry the operator command (the 2d wording) |
| Surface mismatch | `valid: false`, `publication.surface_incompatible` (catalog:77) | Unspecified for `run` | Name the admission code that `run` returns, or drop the check for now |
| Shadowed plugin | `shadows` on the `list_plugins` row only | `run` by bare name silently gets the winner, and the agent rarely lists | Stamp the pack id into the run view's identity (R9 does this). Refuse pack-to-pack collisions so that only a deliberate local override can shadow |
| The shadow holds no grant | Not discussed | `admission.credential_not_granted` on a plugin that worked yesterday | Doctor and status flag "local file shadows a granted pack plugin" |
| Policy file invalid | `Blocked("github.policy_invalid", …)` (hooks:100-101) | One answer: report and stop | Good |
| Refused by policy (default branch, title pattern) | Code unspecified | — | This is agent-fixable, not human: return `Failed("github.branch_refused", …)` with `resend: will_not_succeed`, never `Blocked`. `Blocked` tells the agent to stop and wait for a person |
| Install restarts the service | Service-wide drain (catalog:229) | `admission.service_draining` for every agent, during an operation unrelated to them | Rescan instead of restarting (below) |
| Agent `publish_plugin` reuses a pack name | Wins as `local:runtime` (catalog:206) | One agent's file replaces a vetted plugin for every agent | Refuse it with a publication code naming the pack. Runtime publishes rank below packs |

Nothing agent-facing is missing beyond these, and no new tool is needed (Decision 3 holds). The
`list_plugins` growth (a `pack` object per row) is acceptable. Keep the value a short string
(`"github@0.7.0"`) rather than an object.

## Extension walkthroughs

**(a) A user, in a repository.**

- *Today.* Write `tools/repo_tests.py`, list `tools/` once in `[plugins] paths`, and restart once
  (paths are read at start, `main.py:95`). Each later file is picked up by the scan
  (`registry.py:151-172`). That is one file per plugin with no ceremony. A helper library adds a
  pip install, `packages=` and a restart.
- *Catalog.*
  - `trestle pack new mine --in repo` writes six files: `pack.toml`, a wrapper, `pyproject.toml`,
    a test, a README and `trestle-packs.toml`.
  - Then `source add … --tier user`, then `pack install mine/mine`, which runs pip and a restart.
  - If the library is installed non-editable, every library edit is a `pack upgrade`.
- *Verdict.* For sharing, the pack form is heavier but fair. For a single repository it is worse
  than today. The catalog keeps `[plugins] paths` (catalog:265-266), so the zero-ceremony path
  survives, but `docs/packs.md` must lead with it.
- *Scaffolding.* `pack new` should default to wrappers only (two files), and installing a path
  should need no `source add`.

**(b) A company.**

- *Steps.* A repository with an index, then `packs/acme_stack/` holding `pack.toml`, `plugins/`,
  `pyproject.toml`, the library, tests, README, PROVENANCE and CHANGELOG: about eight files. Tag
  it. Each operator runs `source add --tier company --ref v1.4.0` and `pack install acme/acme_stack`,
  then `trestle credentials grant` once per credentialed plugin. Every upgrade means a source
  update, a pack upgrade and new grants.
- *Today, for comparison.* Publish a distribution and a wrapper folder; each operator runs pip,
  edits `paths` and restarts.
- *Verdict.* The plan adds pinning and a doctor, which are real gains, at the cost of an index
  file and a tier flag that add nothing. With the smallest plan above it comes to five files and
  two commands: `pack install git@…/acme-packs.git@v1.4.0`, then `grant`.
- *Company `github` behaviour.* hooks:296-298 has the company ship `acme:github-policy`, a pack
  holding only a TOML file. A pack with no plugins should not be needed. The policy file lives
  wherever the operator likes, and one `[plugins.env]` line names it.

## Contradictions

| # | Topic | Conflict | Fix |
|---|---|---|---|
| 1 | Kit's fate | catalog:363-370 keeps `trestle_packs` as an always-installed kit (rename open, catalog:520); org §3 dissolves it | Adopt org's direction, but only after the follow-ups (decision 1 below). Strike catalog:363-370 and open question 2 |
| 2 | Plugin names | catalog owner decision 2 renames everything to `stack_*` (catalog:373-374, 504-505); org keeps `docker_stack`/`pytest_run`/`migrate_apply` (org:152-155) | Keep the existing names (decision 3). Retire the prefix rule as a requirement (catalog:197-199); install-time collision refusal replaces it |
| 3 | Cross-pack dependencies | catalog:377-378 pins `trestle==0.5.*` in bundled packs; hooks:106 has the company `pyproject` depend on `trestle-pack-github`; org:145-150 forbids pip dependencies between packs (index risk) | Org is right for the bundled names. For companies, depend on the bundled library through pip with `--no-index`-style constraints that resolve `trestle*` names only from the release checkout |
| 4 | Bundled pack versions | hooks:114-115 gives the library its own semver and changelog; org §4 uses lockstep with no per-pack number | Lockstep for bundled packs. Hooks' semver applies only to non-`trestle` packs |
| 5 | Zip merges | catalog:59-61, 419-420 merge `keep()` and the docker additions "into the kit"; org:103-105 deletes `core/artifacts` and puts `keep()` on `Context` | `keep()` on `Context` (or nowhere); the docker additions go to whichever library owns compose |
| 6 | `github` timing | catalog:44-46 says "there are two" bundled packs now; catalog:492-495 and org:134 put it last | Say it plainly: `stack` first, `github` in a later release |
| 7 | Runtime publishes | catalog:186, 206 list `local:runtime` as its own tier and id; the code writes runtime publishes into `plugin_dirs[0]`, a normal folder (`registry.py:379-382`) | Give runtime publishes their own folder (`$TRESTLE_HOME/plugins/runtime`), ranked below packs |
| 8 | Per-pack env and composition | hooks:240-241 puts `TRESTLE_GITHUB_POLICY` in `[packs."trestle:github".env]`, but a company wrapper (hooks layer 2) runs as the company's pack and would not see it | Use `[plugins.env]` (or defer per-pack env, as recommended) |
| 9 | Shadowing as a customization path | org:379-380 offers "shadow by name" as a company route; catalog:197-199 treats shadows as accidents to avoid | Shadowing is an operator override, not an extension mechanism. Remove org option 2 |
| 10 | Library layout | catalog:104 uses `src/trestle_pack_github/`; org:293-294 uses a flat layout | Flat (matches the repo); see code check 2 on editable installs |
| 11 | Default packs | org:307-309 claims today's install turns the four example plugins on by default | False (code check 8). Restate decision 2 accordingly |

All three documents agree on rejecting entry points and on serving wrappers from the checkout.
The second is challenged below on its merits.

## Code checks

| Claim | Finding |
|---|---|
| 1. The registry is first-path-wins | **True** (`registry.py:204-211`: `claimed` set, logs "first path wins"). Unremarked in the plan: the name is claimed **before** validation (`claimed.add` at :210; validation failures at :236-245). A broken plugin in an earlier folder therefore hides a valid one in a later folder. Tier precedence inherits this unless the winner is chosen after promotion |
| 2. Package-digest scope | **True**: only `.py` files under the package (`child/validate.py:249-266`), so package data and policy files are outside it. Unremarked: `_locate_package` walks `sys.path` directories without importing (`:203-223`). *Inference:* an editable install that uses an import hook instead of a path `.pth` (setuptools does this for some layouts) will not be found, and publication then fails "not found on the import path". hatchling's path `.pth` works (the service venv has `_editable_impl_trestle.pth` → the repo). Doctor must check this, or the CLI must install pinned libraries non-editable |
| 3. The host never imports plugin code | **True for the service.** Authoring runs in a throwaway interpreter (`authoring.py:12, 132`); the packs probe is a subprocess (`plugin_validate.py:78-96`); `trestle_packs` and `trestle.workflow` are in `FORBIDDEN_HOST_IMPORTS` (`tests/proof/test_import_boundaries.py:34-40`). One allowlisted CLI exception: `init_cmd.py:39` imports `trestle_packs` |
| 4. Restart semantics on install | Plugin folders are resolved once, at start (`server/main.py:95`). The scan signature covers only wrapper mtimes and sizes (`registry.py:151-164`), so a library upgrade is not seen until a restart republishes. `[plugins.env]` is reread at every run (`conductor.py:204-205`). **So a restart is not inherent**: adding the lock's mtime to the signature gives the same republish without a drain. The catalog's order (pip, then config, then restart; catalog:223) changes the library under live and queued runs |
| 5. The service never needs `importlib.metadata` | **True for the serving path.** The only use is `service.py:336`, in the operator's `upgrade` command |
| 6. Core dependencies on `trestle_packs` | Org's table is **accurate**: `child/validate.py:102-114`; `plugin_validate.py:17, 61-96, 199-203`; `registry.py:282`; `admission.py:202`; `plugin_paths.py:14-17`; `init_cmd.py:37-43`; guide examples `pack_wrapper.py:10,15` and `composite_workflow.py:14,132`; `pyproject.toml:29-35, 53`. Also 12 imports of private `trestle.workflow.services` in the package (counted), with `PUBLIC_MODULES` at `workflow/__init__.py:81` |
| 7. Where runtime publishes land | `writable_plugin_dir()` returns `plugin_dirs[0]` (`registry.py:379-382`). Under catalog:277, "lock packs first" makes that a pack's wrapper folder inside a checkout |
| 8. "The examples are on by default today" (org:307-309) | **False.** `install.sh:70` installs the `.[packs]` library only. `init_cmd.py:42-43` tells the operator to add `examples/packs` by hand, and nothing else adds it |
| 9. The library today is a copy | `.[packs]` installs `trestle-packs @ file:packages/trestle-packs`, which is not editable (`pyproject.toml:30`; a `trestle_packs/` copy in the service venv). "Install from the checkout" already means "copy at install time" |
| 10. Stale version | **True**: `trestle/__init__.py:3` and `v0.4.1:pyproject.toml` both read `0.3.1`. It feeds `runtime_version` in the snapshot id (`snapshots.py:171`) |

## Risks and sequencing

**What breaks unattended runs, most severe first:**

1. **Provenance-mismatch storms.** The catalog serves wrappers live from the checkout
   (catalog:112-114) and reports a moved HEAD only in doctor (catalog:284-286). On the owner's
   machine, the `trestle` source *is* the development tree: the editable `.pth` points at
   `~/projects/trestle`, which carries uncommitted edits today.
   - If the library is editable, a branch switch changes the digests. Every admitted run then
     stops with `execution.provenance_mismatch`, and every later run does too, because the
     registry does not republish on a library change (check 4).
   - If the library is a copy, a branch switch republishes new wrappers over an old library, and
     the two skew.

   Either way the agent's only move is "the operator republishes". **Fix:** pinned kinds serve a
   copy under `$TRESTLE_HOME/packs/<id>/` taken at install time, wrappers and library from one
   commit. Only path installs are live.
2. **Install drains the world.** A service-wide drain-restart for any pack operation means an
   hour-long Docker run blocks `pack install github`, or delays it until the run finishes.
   **Fix:** rescan, and wait only for runs of the affected pack's plugins.
3. **pip before drain.** A live child can import a half-replaced library, and a queued run starts
   against new digests. **Fix:** wait first, then pip, then touch the lock.
4. **Grant drops on routine upgrades.** Grants pin `package_digests` per plugin (decision §2(a)).
   Lockstep means a Trestle update that touches `trestle_pack_github` drops `github_pr_open`'s `gh`
   grant, and `update.sh` is exactly the run nobody watches.
   - **Fix:** `trestle pack upgrade` lists the dropped grants and exits non-zero unless given
     `--regrant`, which re-pins only grants that existed before the upgrade.
   - The `status` view shows `grants_stale`.
   - The per-library split does narrow this (org:40-42); so does keeping `github` its own library,
     which every document already does.
5. **Silent loss of the examples on upgrade.** A deleted `examples/packs` path is logged and
   skipped (`plugin_paths.py:73-80`); the plugins vanish and agents get `plugin_not_found`.
   **Fix:** a migration in `install.sh` and `init --upgrade`.
6. **Renames break chains.** Idempotency keys and `after=` chains name plugins. Renaming
   `docker_stack` breaks running automations for no gain.
7. **Index confusion.** If any bundled pack ever names a `trestle*` pip dependency, pip may resolve
   it from PyPI (org:145-150). Install bundled libraries with `--no-deps` for `trestle*` names.

**Delivery order:**

1. Fix the stale version now (an independent bug).
2. Let 2a–2d land, so the `status` view and the new `catalog_hint` wording exist.
3. A short decision record.
4. The smallest viable plan: install, lock, rescan, collision refusal, the `plugin_not_found`
   hint, the examples migration, and `stack` with its names unchanged.
5. `github`: read-only plugins plus the policy file; `open` last, with its push guards
   (catalog:441-449).
6. The dependency follow-ups (org:177-187): out-of-process pytest and migrate on
   `toolchain`/`testrun`, and Docker on the core engine.
7. Only then the substrate move and the dissolution, and a per-tool split if anything heavy is
   still left.

Org's migration (about 70 test import sites, fences, `compat_map`, path-keyed scope guards;
org:312-339) is right-sized for a move. It is wrong-sized as step 3 of the critical path, because
it moves legacy code that step 6 rewrites.

Separately: the skill's flagship `repo_tests` is not in the repository. It lives in a user folder
(the catalog itself cites it as a local example, catalog:187). Meanwhile `pytest_run` runs pytest
**in-process in the service venv** (`examples/packs/pytest_run.py:7,21`), which is the wrong
interpreter for a project. The bundled set should ship the plugin agents actually use: a
`repo_tests`-shaped runner on the project's resolved interpreter. That serves "fewer calls" more
than any packaging change does.

## Pending decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Substrate into core; dissolve `trestle_packs` | **Yes in principle, later in order.** The evidence (private `services` imports, `PORT_MODULES`, drift tests) is sound. Do it after the follow-ups, so legacy code that is about to be rewritten is not moved first. Keep `trestle_packs` as `stack`'s library until then |
| 2 | Per-tool packs; `install.sh` defaults docker, pytest, migrate, stack | **No, for now.** The split exists to avoid heavy dependencies (org:171-173), and org's own follow-ups remove them. Ship one `stack`. Default `--packs`: migrate existing users; otherwise install none and print the available list. The "today's default" rationale is wrong (check 8) |
| 3 | Keep three names; rename only `integration_pipeline` | **Yes**, and amend owner decision 2. Make the prefix rule a convention, not a test. Rename `integration_pipeline` only if it is being touched anyway |
| 4 | Lockstep; fix the stale 0.3.1 | **Yes to both.** Fix the version in the next release, independent of packs. Bundled `pack.toml` carries no version, and a gate test ties each `pyproject` version to `trestle.__version__` |
| 5 | Policy first for `github`; seams second; bound hooks deferred | **Yes.** Name the file through `[plugins.env]`, so core needs no change. Agent-fixable refusals return `Failed`, not `Blocked`. Leave out the `github_policy` plugin; the echoed `policy_sha256` and the attached `policy.json` suffice |
| 6 | Policy digest in run provenance | **Defer.** The `*_POLICY` env-name convention (hooks:331-333) is magic naming. Echoing the digest in the result is enough for one consumer |
| 7 | `github` defaults | Bots: `isBot`, a `[bot]` suffix, `github-actions`, `dependabot`; drop the vendor names. Required checks: `branch_protection` through `gh pr checks --required` (*inference:* needs no admin token), falling back to `all`. `none_scheduled = "block"`: a silent pass is the one outcome an unattended run cannot recover from. Commit message `{title}`; refuse default branches; `update_existing = false` |
| 8 | Later, a generic `project_up` | **Not now.** Revisit when a second consumer exists. Its neighbours in the zip carry AWS preparation, which must stay fake |

## Must-fix list, ranked

1. **Serve pinned packs from an install-time copy** (wrappers plus library from one commit), not
   live from a checkout: no provenance storms, no skew.
2. **Replace restart-on-install with a rescan** (the lock's mtime in the scan signature), and wait
   only for the affected pack's runs **before** pip.
3. **Migrate `examples/packs`** in `install.sh` and `init --upgrade`, so no operator loses plugins
   on upgrade.
4. **Keep runtime publishes out of pack folders and below packs.** Give them their own folder, and
   refuse `publish_plugin` for a name a pack owns.
5. **One answer for a missing pack.** `admission.plugin_not_found` names the providing pack and the
   operator command. Live pack state goes in the `status` view, not a guide topic.
6. **Choose the collision winner after validation**, and refuse pack-to-pack collisions at install.
7. **Handle grant drops on upgrade**: list them, exit non-zero without `--regrant`, show
   `grants_stale` in `status`.
8. **Resolve the contradictions table**, above all the kit's fate, plugin names and pip
   dependencies between packs.
9. **Cut or defer** the index file, source commands, the tier flag, qualified-id allowlists,
   per-pack env, surface checks, `requires.packs`, `[effects]`, surface tiers and `[hooks]`.
10. **Put a `repo_tests`-shaped, out-of-process test runner in the bundled set** ahead of the
    per-tool split.
