# Pack organization: what is core, which packs, names and versions

2026-10-22. Analysis only; nothing here is built. Inputs:

- Trestle at `fc4999d0` (`v0.4.1`);
- `2026-10-22-trestle-pack-catalog-design.md` (cited `catalog:`), which this refines and does not
  replace;
- the 2026-10-19 pack analysis, its review and the core-updates plan;
- hyper-gremlin (`~/projects/hyper-gremlin`).

The hook mechanism for company-specific `github` behaviour is a parallel analysis. This one only
marks where its layout leaves room for hooks.

## Headline

- **`trestle_packs` is two things under one name.** The repo already says so:
  - *workflow port adapters*: `process`, `container`, `fakes`, `grant`, `toolchain`, `testrun`,
    `provision`;
  - *legacy plain-plugin helpers*: `core`, `docker`, `pytest`, `migrate`. The package's own tests
    call these `legacy` (`packages/trestle-packs/tests/legacy/`), and `container/__init__.py:7-8`
    calls `trestle_packs.docker` "the legacy subpackage".
- **Move the port adapters into core as `trestle.workflow.adapters.*` plus `trestle.workflow.fakes`.**
  Core already treats them as its own:
  - it names them in `PORT_MODULES` (`trestle/child/validate.py:102-114`);
  - it holds them to its import-boundary rule (`tests/proof/test_import_boundaries.py:116-122`);
  - its docs make product guarantees about "the shipped facet" (`docs/agents.md:233`);
  - they import a private core module, `trestle.workflow.services`, at 12 sites.

  The one exception is `provision`, which is Postgres-specific; it goes wherever `trestle-env` goes.
- **Dissolve `trestle_packs` rather than rename it.** Split the legacy helpers into one bundled pack
  per tool: `docker`, `pytest`, `migrate`, plus a `stack` pack that composes them, and `github`
  later. Three of the four example plugins already carry their pack's prefix: `docker_stack`,
  `pytest_run`, `migrate_apply`. Only `integration_pipeline` needs a new name (`stack_pipeline`).
  No `git` pack yet.
- **Name: keep "bundled packs" in prose and `trestle` as the source id. Version: lockstep with
  Trestle, from one source of truth that the gate enforces.** The in-tree version strings are
  stale today. `v0.4.1` ships `version = "0.3.1"` in `pyproject.toml` and `trestle/__init__.py`,
  and so has every release since `v0.3.1`. Any scheme built on those numbers would record wrong
  versions in the narrow R9 stamp.
- **Splitting the packs also narrows grant churn.** A grant pins every declared package's digest,
  and the digest covers the whole import package (`validate.py:249-266`). Today every pack plugin
  declares `trestle_packs`, so an edit to `migrate` would drop a `github` grant.

## What core depends on today

| Dependency | Where | Kind |
|---|---|---|
| `PORT_MODULES = (process, fakes, container, toolchain, grant, provision, testrun)` prefixed `trestle_packs.` | `trestle/child/validate.py:102-114` | publication rule (an import of these requires `env_arg`) |
| `PACK_IMPORT_PREFIX = "trestle_packs"` plus an `import trestle_packs` probe | `trestle/server/plugin_validate.py:17,79-96` | catalog hint |
| string match on `"trestle_packs"` in import errors | `server/registry.py:282`, `server/admission.py:202` | catalog hint |
| hard-coded install hint | `server/plugin_paths.py:15-16`, `server/init_cmd.py:37-43` | operator text |
| guide examples import `trestle_packs` | `trestle/plugin/_guide/examples/pack_wrapper.py:10,15`, `composite_workflow.py:14,132` | docs |
| core tests import `trestle_packs.fakes` (54 sites), `.process` (11) and a handful of others | `tests/fixtures/trees`, `tests/fixtures/workflows`, `tests/tree`, `tests/single` | test substrate |
| conformance suites run the real adapters against the fakes | `tests/proof/suites/ports/*`, `packages/trestle-packs/tests/conformance/*_cases.py` | proof |
| fences and scope guards keyed by path | `tests/proof/fence.d/single.toml:52-66`, `slice-b.toml:13-46`, `tests/legacy/test_legacy_scope.py:18-20` | gate |
| `.[dev,packs]` needed for mypy | `.github/workflows/ci.yml:42-57`, `tests/core/tooling/test_ck14_typecheck.py:37-66` | gate |

`trestle/workflow` imports nothing from `trestle_packs`. The dependency runs the other way: the
adapters import `trestle.workflow.ports`, `.values`, `.declarations` and the **private**
`trestle.workflow.services`. The workflow package keeps `services` private
(`trestle/workflow/__init__.py:81` lists only `declarations, loop, ports, units, values`). Because
they cannot import `trestle.common.plan`, the adapters also restate core constants and keep them in
step with a drift test (`toolchain/resolver.py:54`, "the SA-03 drift test reads both"). The fakes
are stdlib-only copies of the B3 types (`fakes/__init__.py:6-8`). Both are signs of a split in the
wrong place.

## 1. Foundational code

**Recommendation.** Move these into the `trestle` distribution:

| From `trestle_packs.` | To | Lines | Why core |
|---|---|---|---|
| `process` | `trestle.workflow.adapters.process` | 734 | core's local-process model (pid plus start time); in `PORT_MODULES`; 11 core test sites |
| `container` | `trestle.workflow.adapters.container` | 1116 | `docs/agents.md:233` and the release allowlist (`docs/environment.md`, "the shipped effect facets") are guarantees about this facet; stdlib-only; Docker is needed only at use |
| `fakes` (all but `fakes/provision.py`) | `trestle.workflow.fakes` | 1806 | core's tree tests, fossils and guide examples run on them; they can then use the real B3 types instead of copies |
| `grant` | `trestle.workflow.adapters.grant` | 801 | Decision 2 adopts its shape as the product credential model (`docs/decisions/service-and-credentials.md:197-205`); demo only (D-9); no real AWS |
| `testrun` | `trestle.workflow.adapters.testrun` | 242 | an out-of-process `ExecutionPort` that reads JUnit into the core value `TestCounts` (`testrun/__init__.py:3-6`) |
| `toolchain` | `trestle.workflow.adapters.toolchain` | 905 | resolves the project's pinned tools, which is how unattended runs adopt locally installed tools; calls only `mise ls --current --json`, never `exec`/`install` (`toolchain/resolver.py:11-14`) |

That adds about 5,600 lines to a 29,600-line core (`trestle/workflow` is 5,476 today). What
changes in core:

- `PUBLIC_MODULES` gains `adapters.*` and `fakes`. The publication import rule
  (`validate.py:52-61`) then admits them by name.
- `_PORT_PREFIXES` becomes `trestle.workflow.ports`, `trestle.workflow.adapters` and
  `trestle.workflow.fakes`. No pack name stays in core.
- The restated constants become imports from `trestle.common.plan`, which the workflow package may
  import (`workflow/__init__.py:1-2`). The SA-03 drift checks for them retire.
- The host stays clean for free. `trestle.workflow` is already in `FORBIDDEN_HOST_IMPORTS`
  (`test_import_boundaries.py:34-40`), so the adapters stay on the plugin side.
- Grants are unaffected. Core code is not in `package_digests`. Decision 2 pins
  `source_sha256` and `package_digests` and leaves out the runtime version on purpose
  (`service-and-credentials.md:244-256`). So a change to a core adapter means "trust the release
  you run", which is the `trestle` tier's trust model (`catalog:330`).

**Not foundational:**

- **`core/dag` and `core/readiness` stay out of core.** The tree already owns waves (`needs`,
  `concurrency`) and readiness (`WaitPolicy`). The 2026-10-19 analysis lists these helpers as
  reinvented tree semantics (F14, `2026-10-19-packs-analysis.md:111-120`). Making them public core
  API would give two supported ways to do one thing. They become private helpers of the `docker`
  pack.
- **`core/artifacts` is deleted.** Its `PackContext` (`core/artifacts.py:10-14`) copies
  `Context`'s `log`, `progress`, `artifact` and `attach` (`trestle/plugin/surface.py:77-80`). If
  the zip's `keep()` is wanted, it belongs on `Context`.
- **`core/paths` and `core/teardown`** go with the docker pack.
- **`provision`** writes the reference environment's Postgres record over `docker exec`, so it is
  not generic. It follows `trestle-env` (§5).

**Rejected alternatives:**

| Alternative | Why not |
|---|---|
| Keep a separate always-installed kit distribution (`catalog:363-370`) | It cannot version on its own: it imports private `services`. It doubles the constants and the types. Its digest makes every kit edit drop every pack grant. The plugin import rule would need a second allowlist |
| A top-level `trestle.kit` | It needs a new entry in `FORBIDDEN_HOST_IMPORTS` and its own allowlist. `trestle.workflow.adapters` says what the code is (implementations of `trestle.workflow.ports`) and inherits the existing machinery |
| Everything into core, the legacy helpers included | That drags `python-on-whales`, `pytest`, `alembic` and `yoyo` into core (`packages/trestle-packs/pyproject.toml` extras) |

**Owner decisions:**

- (1a) The module path: `trestle.workflow.adapters` / `trestle.workflow.fakes`, or another name.
- (1b) Whether `toolchain` and `testrun` join core now, or wait with `provision` for the
  `trestle-env` decision.

## 2. The bundled pack set

**Recommendation:**

| Pack | Plugins (from `examples/packs`) | Library | `requires.packs` | Host tools | Python deps |
|---|---|---|---|---|---|
| `docker` | `docker_stack` (unchanged) | `trestle_pack_docker`: `docker/*`, `dag`, `readiness`, `teardown`, `paths` | none | `docker` with compose | `python-on-whales` (see follow-ups) |
| `pytest` | `pytest_run` (unchanged) | `trestle_pack_pytest` | none | none | `pytest` |
| `migrate` | `migrate_apply` (unchanged) | `trestle_pack_migrate` | none | none | `alembic`, `yoyo-migrations` |
| `stack` | `stack_pipeline` (was `integration_pipeline`) | none: wrappers only, `packages=[the three libraries]` | `trestle:docker`, `trestle:migrate`, `trestle:pytest` | `docker` | none of its own |
| `github` (catalog step 5) | `github_pr_monitor`, `_ci_report`, `_comments`, `_suite`, `_open` | `trestle_pack_github` | none | `gh`, `git` | none |

**Installing only `docker`.** `trestle pack install docker`:

1. installs `packages/trestle-pack-docker` from the release checkout into the service venv;
2. writes `trestle:docker` to config and the lock, then restarts with draining;
3. publishes `docker_stack`.

Nothing pulls in `pytest` or `alembic`. The guide lists `stack` as available, "needs docker,
migrate, pytest".

**Cross-pack dependencies go only in `requires.packs`, never in pip `dependencies`.** If
`trestle-pack-stack` listed `trestle-pack-docker`, or a pack pinned `trestle==0.5.*` as
`catalog:377-378` proposes, then any version mismatch would send pip to PyPI for those names. Given
the stale version strings (§4), that mismatch is likely, and nothing guarantees that the PyPI
projects with those names are ours. `trestle pack install` resolves `requires.packs` from checkout paths first. It installs only
third-party dependencies from the index.

**Names.** Catalog owner decision 2 (`catalog:504-505`: `stack_up`, `stack_pytest`, `stack_migrate`,
`stack_pipeline`) assumed one `stack` pack. With per-tool packs the prefix rule (`catalog:197-199`)
already holds for three names. Keeping them means agents and docs see no churn for those three.
Only `integration_pipeline` becomes `stack_pipeline`.

**No `git` pack.**

- No git plugin exists today.
- Of the `github` plugins, only `github_pr_open` uses git: a branch check, commit and push
  (`catalog:441-449`). The read-only plugins need only `gh`.
- A `git` pack would ship zero plugins, or plugins invented for the occasion, and would add a
  required install in front of `github`.

Instead, write github's git transport as its own module (`trestle_pack_github/git.py`, over
`ctx.exec`, with no `gh` import), so it can be lifted out when a second consumer appears. The hook
analysis may provide that consumer, if company hooks need guarded git operations.

**Rejected:**

- **One `stack` pack** (the catalog draft): installing Docker support would force `pytest` and
  `alembic` into the service venv. That is the opposite of choosing what to install, the
  hyper-gremlin model the owner cited.
- **A pack per plugin with no `stack`**: the composition plugin has to live somewhere, and a pack
  whose only content is wrappers is cheap.

**Follow-ups, not part of the move.** Two of the libraries carry third-party risk worth a
keep/fork/replace review:

- **`trestle_pack_docker`** drives Compose through `python-on-whales` (`docker/compose_whale.py:17`),
  while the core facet calls the Docker CLI directly through an `ExecutionPort`. *Replace* it with
  the core engine: one Docker code path and one fewer dependency.
- **`pytest_run` and `migrate_apply`** import `pytest` and `alembic` into the child
  (`pytest/runner.py:84-99`, `migrate/alembic_runner.py:19-28`). The project's own dependencies then
  have to be in the service venv, which an unattended run cannot count on. *Replace* both with
  out-of-process runs on the project's resolved interpreter. That uses the core `testrun` and
  `toolchain` adapters, and those two packs then need no Python dependencies at all.

**Owner decisions:**

- (2a) Keep `docker_stack`, `pytest_run` and `migrate_apply`, which amends catalog owner decision 2, or rename
  them all to `stack_*`.
- (2b) The pack name `docker` or `compose`.
- (2c) No `git` pack until a second consumer appears.

## 3. The shared library's name

**Recommendation: dissolve it, and retire the name.**

| Today | Goes to |
|---|---|
| `process`, `container`, `fakes`, `grant`, `testrun`, `toolchain` | core (§1) |
| `docker`, `core/{dag,readiness,teardown,paths}` | `trestle_pack_docker` |
| `pytest` | `trestle_pack_pytest` |
| `migrate` | `trestle_pack_migrate` |
| `core/artifacts` | deleted |
| `provision` (and `fakes/provision.py`) | with `trestle-env` (§5) |

`trestle_packs` and `trestle-packs` join the reserved names (`catalog:349-357`), so an install from
another source that uses them is refused. That closes the fork collision of F13 for good.

**No compatibility shim.** There is one consumer, and fossils are inert bytes. The fossil driver
reads only the ledger and never imports the snapshot's `plugin.py` (`tests/proof/d2_driver.py:36-55`).
The per-pack catalog hint can say "`trestle_packs` was removed in vX; see `docs/packs.md`".

**Rejected:**

- **Rename to `trestle_kit`** (`catalog:520`). It keeps a grab-bag that mixes core substrate with
  tool helpers and has a single digest.
- **Keep the name.** It reads as "the packs" and holds none.

**Owner decision:** (3a) No shim.

## 4. Collection name and version

**Name.** Keep **bundled packs** in prose and **`trestle`** as the machine name: it is the source
id already in qualified ids (`trestle:docker/docker_stack`). "Bundled" says exactly what is true:
the packs ship in every release and stay off until installed. The alternatives fail on that
(`catalog:516-519`):

- "official" makes other tiers second-class;
- "standard" and "core" suggest always on;
- "first-party" is jargon;
- a new brand would be a third vocabulary beside the source id and the tier.

How it surfaces:

- **CLI:** `trestle pack install docker` (a bare name means bundled) and `trestle pack list
  --available`, where the source column reads `trestle (bundled)`.
- **Docs:** a "Bundled packs" table in `docs/packs.md`, plus each pack's README.
- **Agent rows:** `pack: {"id": "trestle:docker", "version": "0.5.0"}` (`catalog:299-300`).

**Version: lockstep, one number.** "Bundled packs 0.5.0" means Trestle 0.5.0.

| Scheme | Fit with checkout-only install (catalog owner decision 4) | Gate | R9 stamp | Grants | Verdict |
|---|---|---|---|---|---|
| Lockstep with Trestle | Exact: the release checkout is the pin; pack and core always come from one commit | one run, no matrix | `trestle:docker@0.5.0` names the release | pinned to digests, so a version bump alone drops nothing | **adopt** |
| Separate collection version (calver or semver) | Two numbers for one commit; nothing ships separately | same | adds a number that maps 1:1 to the release | same | reject |
| Per-pack semver plus a collection release | Signals per-pack change, but only matters once packs ship apart from Trestle | needs a compatibility matrix | each stamp needs a hand bump | same | reject now; revisit if catalog owner decision 4 ever allows wheels |

The deciding evidence is that **versions are not bumped today**:

- `git show v0.4.1:pyproject.toml` has `version = "0.3.1"`, and `trestle/__init__.py:3` agrees;
- all five tags from `v0.3.1` to `v0.4.1` carry `version = "0.3.1"` (checked with `git show
  <tag>:pyproject.toml`);
- the release flow since `v0.3.2` is "push, record commit, tag" (memory "Release graft local");
- `runtime_version`, which feeds the snapshot id, is that stale `__version__`
  (`server/snapshots.py:171`).

Per-pack semver would multiply a bump that is already skipped. So:

- **One source of truth.** The release commit sets `trestle/__init__.py` from the tag. Each
  bundled `pyproject.toml` and `pack.toml` repeats the number (or `pack.toml` omits it for the
  `trestle` tier, and the lock takes Trestle's).
- **A gate test** checks that every bundled version equals `trestle.__version__`, and at release
  that it equals the tag. Because a release is a single commit on origin, the bump lands in that
  commit.
- **The lock** also records the `trestle` source commit (`catalog:272`), so provenance is exact
  even between releases.

**Owner decisions:**

- (4a) Keep "bundled packs".
- (4b) Lockstep, with the version-bump step added to the release flow.
- (4c) Fix the stale version in the next release, independent of packs.

## 5. Repo layout and migration

**Layout:**

```
trestle/workflow/adapters/{process,container,grant,testrun,toolchain}/
trestle/workflow/fakes/
trestle-packs.toml                       # the `trestle` source index
packages/trestle-pack-docker/            pack.toml  plugins/docker_stack.py   trestle_pack_docker/  tests/
packages/trestle-pack-pytest/            pack.toml  plugins/pytest_run.py     trestle_pack_pytest/  tests/
packages/trestle-pack-migrate/           pack.toml  plugins/migrate_apply.py  trestle_pack_migrate/ tests/
packages/trestle-pack-stack/             pack.toml  plugins/stack_pipeline.py                       tests/
packages/trestle-pack-github/            (catalog step 5)
packages/trestle-env/                    (owner decides, below)
```

- The layout is flat (`trestle_pack_x/` beside `pyproject.toml`), matching the repo's current
  packages rather than `src/`.
- `pack.toml` reserves a `[hooks]` table for the parallel analysis.
- Each pack keeps its library's public names in `__all__` (§6), so hooks can attach there.

**What goes:**

- `packages/trestle-packs` and `examples/packs`;
- the `.[packs]` extra, and `trestle-packs` from the `env` extra (`pyproject.toml:29-35`);
- `packages/trestle-packs/tests` as a testpath (`pyproject.toml:53`).

CI installs `.[dev]` plus every `packages/trestle-pack-*` editable, the hyper-gremlin pattern of
`scripts/install_first_party_plugins.sh`. A dev-only extra is the alternative.

**`install.sh`.** `--no-packs` (`scripts/install.sh:28,36,70-71`) becomes `--packs a,b|none`. To
keep today's behaviour, where the four example plugins are on by default, the default should be
`docker,pytest,migrate,stack`. `github` is opt-in. `install.sh` has uncommitted local edits (the
`--bin-dir`/`--no-link` work), so this change must merge with them.

**Tests and docs that change:**

- **Core code:**
  - `validate.py:102-114` (`PORT_MODULES` → core prefixes);
  - `plugin_validate.py:17,79-96`, `registry.py:282`, `admission.py:202` and `plugin_paths.py:15-16`
    (one generic per-pack hint, `catalog:395`);
  - `init_cmd.py:37-43`;
  - the two guide examples.
- **Test imports:** a mechanical rewrite of about 70 import sites in `tests/fixtures/{trees,workflows}`,
  `tests/tree`, `tests/single`, `tests/pins/e_packs` and `tests/proof/suites/ports`.
- **Pack tests:**
  - `packages/trestle-packs/tests/{conformance,container,grant,process,testrun,toolchain}` move to
    `tests/adapters/`;
  - `legacy/*` go to the matching packs;
  - `test_packaging.py` is replaced by a lockstep-version test.
- **Gate machinery:**
  - fence globs (`fence.d/single.toml`, `slice-b.toml`, `core.toml:72,130`);
  - `compat_map.toml`;
  - the import-boundary tuples (`test_import_boundaries.py:118-122,146`);
  - the path-keyed scope guard (`test_legacy_scope.py:18-20` reads `wr-ckpt/core:<old path>`), which
    needs an old→new path map or retiring;
  - CK-14's `.[dev,packs]` check.
- **Fossils** stay byte-stable and untouched.
- **Docs:**
  - `docs/packs.md` (rewritten);
  - `docs/plugins.md:380-392`, `docs/agents.md:233,592`, `docs/environment.md:192`;
  - `docs/install.md:32,263,275`, `README.md:29,130`;
  - `scripts/smoke_packs.py` and `scripts/demo_pack_workflows.py`.

**Order.** Each step is one PR, bundled per the PR-bundling rule:

1. **Decision record** `docs/decisions/packs.md`, covering this layout, lockstep versions, the
   surface policy (§6) and the reserved names.
2. **Version hygiene:** the bump step and the lockstep gate test (§4). Small and independent.
3. **Substrate into core:** the six adapters plus the fakes. The rest of `trestle_packs` stays
   importable for the legacy code and `trestle-env`; `reference_env` switches to
   `trestle.workflow.adapters.process.local` (`reference_env.py:20`). This step also updates the
   fences, the boundaries and the guards.
4. **Pack mechanism plus the split**, landed together so the extra disappears in the same step that
   `trestle pack install` arrives:
   - the catalog's core table;
   - `docker`, `pytest`, `migrate` and `stack`;
   - delete `trestle_packs`, `examples/packs` and `.[packs]`;
   - `install.sh --packs`.
5. **Other sources** (catalog step 3).
6. **The `trestle-env` decision**, below.
7. **`github`** (catalog step 5); then the follow-ups in §2.

**`trestle-env` options.** It is 2,895 lines and depends on `trestle-packs`
(`packages/trestle-env/pyproject.toml`, `dependencies`). Its plugin declares
`packages=("trestle_env", "trestle_packs")` (`reference_env.py:38`).

| Option | What moves | Consequences |
|---|---|---|
| A. Bundled pack `env` | `trestle_env` plus `provision` become `packages/trestle-pack-env` | Needs G4 first: the pack declares `effects.port_modules` (`catalog:134,394`). `reference_env` breaks the prefix rule (rename to `env_reference`). Its twin and host tests run in the gate as pack tests |
| B. All into core | `trestle_env` and `provision` join `trestle.workflow` | No G4 dependency, but about 3,300 more core lines of demo-specific data (pinned images, Postgres record) |
| C. Split | `provision` into core adapters; the reference tree and plugin become pack `env` | The adapters are core-complete; the pack is data plus wrappers; G4 is not needed for it |
| D. Test-only | The reference env becomes proof fixtures under `tests/`, and ships no plugin | Smallest product surface; operators lose `reference_env` as a usable plugin |

## 6. Company and user packs

**Building without forking.** There are three ways:

1. **Import a bundled library.** A company wrapper `acme_stack` imports
   `trestle_pack_docker.StackRunner`, declares `packages=["acme_pack", "trestle_pack_docker"]` and
   `requires.packs = ["trestle:docker"]`. Both digests are pinned, so a Trestle upgrade that
   changes the docker library correctly drops acme's grants.
2. **Shadow by name.** A company `docker_stack` wins over the bundled one by tier precedence
   (catalog owner decision 3), and the shadow is reported.
3. **Hooks** for the `github`-style variants (the parallel analysis).

Tree units cannot reference another plugin: `WorkflowEntry.units` is per plugin
(`declarations.py:209-219`). So reuse happens by import, and the import surface has to be stated.

**Surface tiers**, modelled on hyper-gremlin's `DEPRECATION_POLICY.md:7-11`:

| Tier | Members | Contract |
|---|---|---|
| Plugin surface | `trestle.plugin` (`trestle`, `Context`, `Blocked`, `testing`), `trestle.workflow` and its `PUBLIC_MODULES` (incl. `adapters.*`, `fakes`) | Versioned by `trestle.plugin.surface.API_VERSION = "1.0"`, which does not exist yet (`catalog:386`). `pack.toml` `surface = "1"`, checked at install and publication. A name is removed only at a surface major, after one release of `DeprecationWarning` and a `describe_plugin` warning |
| Bundled pack libraries | each library's `__all__` | Versioned with Trestle (lockstep). Removal after one release of `DeprecationWarning`, noted in the pack's CHANGELOG |
| Internal | everything else, `_`-prefixed modules, `trestle.workflow.services` | No promise |

Bundled plugin names and argument schemas are agent-facing. While Trestle is 0.x with one consumer,
they may break in any release with a CHANGELOG line. hyper-gremlin's runway applies from 1.0.

**Close one gap.** The import rule (`validate.py:52-61`) checks plugin source only, so libraries
escape it. That is how `trestle_packs` came to import `services`. Hyper-gremlin only documents its
boundary (`PLUGIN_AUTHORING.md:135`). Trestle can enforce its own: `trestle pack install` and
`doctor` run the same AST scan over a non-`trestle`-tier pack's library, and refuse (or warn on)
imports outside the plugin-surface tier. That makes the stable surface the surface company packs
actually use.

**Owner decisions:**

- (6a) The three tiers and the deprecation runway.
- (6b) Whether the library scan refuses or warns.
- (6c) Whether bundled libraries' `__all__` is a supported surface at all, or company packs should
  wait for hooks.

## Owner decisions, collected

| # | Decision | Recommendation |
|---|---|---|
| 1a | Core module path for the substrate | `trestle.workflow.adapters.*`, `trestle.workflow.fakes` |
| 1b | `toolchain`/`testrun` into core now | yes; `provision` waits for `trestle-env` |
| 2a | Plugin names | keep `docker_stack`, `pytest_run`, `migrate_apply`; `stack_pipeline` (amends catalog owner decision 2) |
| 2b | `docker` or `compose` | `docker` |
| 2c | `git` pack | not until a second consumer |
| 3a | Shim for `trestle_packs` | none; the name is reserved |
| 4a | Collection name | "bundled packs" / source `trestle` |
| 4b | Version scheme | lockstep, one source of truth, gate-enforced |
| 4c | Fix the stale `0.3.1` | next release |
| 5a | `install.sh` default packs | `docker,pytest,migrate,stack` |
| 5b | `trestle-env` | choose A–D after step 4 |
| 6a–c | Surface tiers, library scan, bundled `__all__` | as §6 |
