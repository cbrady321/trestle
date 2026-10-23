# Packs: the developer experience, and the "outstanding or hackable" bar

2026-10-22. Analysis only; nothing here is built, and no other document was edited. Inputs:

- `2026-10-22-trestle-pack-catalog-design.md`, revision 2 (cited `catalog:`), and its supporting
  documents `2026-10-22-pack-plan-review.md` (`review:`), `2026-10-22-pack-hooks-analysis.md`
  (`hooks:`) and `2026-10-22-pack-organization-analysis.md` (`org:`);
- `docs/decisions/service-and-credentials.md` (`decision:`), `skills/trestle/SKILL.md`,
  `README.md`, `docs/overview.md`, `docs/packs.md`, `docs/plugins.md`;
- Trestle at `fc4999d0` with the working tree as found: `trestle/server/registry.py`,
  `snapshots.py`, `config.py`, `authoring.py`, `trestle/child/validate.py`,
  `trestle/child/plugin_api.py`, `trestle/plugin/_guide/`, `examples/packs/`,
  `packages/trestle-packs/`;
- the packs zip, listed with `unzip -l` only (sizes of `github_pr`); nothing in it was extracted
  or run.

Facts are cited by file and line. Anything marked *inference* was not checked by running it.

## Headline

**The skeleton of revision 2 is right, but it is built for operators. It gives an agent no way to
read a pack, and no cheap way to adapt one.** An agent whose project doesn't fit `docker_stack`
can't see the code. It is told not to read Trestle's home or source, and that is where the wrapper
copy and the library live. The logic it would change sits in a pinned library behind
`python-on-whales`. Its natural fix, republishing under the same name, is refused. With every path
closed, the shell is the easiest way out, which is the outcome the owner's principle forbids.

The fix is small and needs no new tool:

1. **Treat pack source as open.** Serve every installed pack's wrapper and library source
   read-only over MCP, from the exact bytes that run. Keep Trestle's core and its home off-limits.
2. **Adapt by publishing under a new name.** The pack-name refusal stays, but its answer becomes
   the way forward: it names the source to start from and the publish to make.
3. **Set a bar for bundled packs.** Each is either outstanding or hackable, and ideally both:
   wrappers carry the recipe, and the library carries mechanism behind a small documented API.
   **`stack` today is neither.** Fix it before it ships as the first bundled pack.

Keep the install-time copies and the non-editable installs; the provenance argument for them holds
(`review:194-205`). Change the collision rules so that a **declared** override is allowed and an
accidental one is refused. Let live (path) installs rescan when library files change.

## 1. Four tasks, step by step, under revision 2

### (a) An agent adapts `docker_stack` (extra compose flag, or a pre-step)

`stack_up` was a proposed rename. Revision 2 keeps `docker_stack` (`catalog:334-337`).

| Step | Call or file | Where it gets stuck |
|---|---|---|
| 1 | `describe_plugin("docker_stack")` | The schema shows one `compose_file` and no `profiles`, `env_file`, extra arguments, `build` or pre-step. `describe` returns digests and schemas, never source (`registry.py:345-366`). |
| 2 | Reads the code | **Stuck.** The wrapper copy is under `$TRESTLE_HOME/packs/stack/` (`catalog:163-165`): "home", which the server instructions forbid (`authoring.py:61-66`). The library is in the service venv, which is Trestle's install. The guide has no pack source, and its API index covers only `trestle.*` modules (`plugin_api.py:22-27`). |
| 3 | Finds where the flag goes | **Stuck again.** The wrapper is 68 lines that hand off to `StackRunner` (`docker_stack.py:64-68`). The flag belongs in `WhaleComposeBackend.up`, which hard-codes `build=False` and one compose file (`compose_whale.py:23-26, 39-45`). The seam exists, `StackRunner(ctx, backend=...)` over a `ComposeBackend` protocol (`runner.py:34-36`, `backend.py:11-34`), but neither is exported (`trestle_packs/__init__.py:3-14`) or documented. |
| 4 | `publish_plugin(source)` named `docker_stack` | Refused, by design (`catalog:132-133`). The design gives no wording that points to the alternative. |
| 5 | `publish_plugin` as `myproj_stack` | Works under the full profile. The runtime folder is shared, so every agent now sees it. |
| 6 | Restricted profile | `publish_plugin` is gone (`config.py:41-43`). The only way is to hand a file to the operator. |
| 7 | Pre-step instead of a fork | It could chain `run(pre) → run(docker_stack, after=…)`, but only if a plugin for the pre-step exists. The operator `exec` plugin is still unbuilt (`decision:432`). |

Today, by contrast, `publish_plugin("docker_stack")` lands in `plugin_dirs[0]` (`registry.py:379-382`).
That folder is scanned first, and the first path wins (`registry.py:204-211`). So today an agent
*can* silently replace the pack plugin for every agent. Revision 2 closes that hole, but leaves
nothing open in its place.

### (b) An agent writes a new plugin over `github`'s library

1. It needs the library's API. **Stuck:** nothing serves it. The guide's API child describes
   `trestle.plugin` and `trestle.workflow` only (`plugin_api.py:22-27`). The planned library is
   about 72 KB across 15 modules in the zip; `batch.py` alone is 19 KB.
2. It writes a wrapper declaring `packages=["trestle_pack_github"]` and publishes it under a new
   name. That works. The policy file reaches it, because `[plugins.env]` is global.
3. If it declares `credentials=["gh"]`, the grant never comes: agent-published plugins never
   receive credentials (`decision:255-256`). It gets `admission.credential_not_granted`, and its
   one move is to ask the operator.
4. When a Trestle upgrade changes the library, the next refresh re-materializes every watched file
   (`registry.py:227-257`), so the runtime plugin republishes with new digests. If an unexported
   name it relied on moved, it fails to import. Today there is no API stability promise to lean on.

### (c) A human iterates on a bundled pack in the Trestle repo

1. Edit `packages/trestle-pack-stack/`. A bundled install serves a copy, so every edit needs a
   `trestle pack upgrade stack`.
2. Switch to a live path install, `trestle pack install ./packages/trestle-pack-stack`. **Stuck:**
   `stack` is already installed, and install refuses a name that collides (`catalog:160`). Remove
   it first; the owner then reads `user`, not `trestle`.
3. Edit the library. **Stuck:** the scan signature covers only wrapper files (`registry.py:151-164`).
   The editable library's digest changes (`validate.py:249-266`) and nothing republishes, so every
   run stops with `execution.provenance_mismatch` until a wrapper is touched or the service is
   restarted.
4. Credentialed plugins (in `github`) lose their grants on each library edit (`decision:243-252`).
   Each edit needs `--regrant`.
5. Tests: `FakeContext` units plus the gated live node. These work today.

### (d) A company forks a bundled pack

1. Copy `packages/trestle-pack-github` into the company repo by hand. Nothing scaffolds it.
2. Keep the plugin names, so agents' calls and `after=` chains keep working. **Stuck** if the
   bundled `github` is installed: the pack and plugin names collide and the install is refused.
   The way round is `pack remove github`, which is undeclared and shows nowhere.
3. Keep or rename the library? With the same distribution name, pip overwrites the bundled one,
   and a later bundled reinstall silently clobbers it back. With a new name, every wrapper's
   `packages=` changes; that is a find-and-replace across thin wrappers.
4. Every Trestle release (lockstep) leaves the fork behind, and nothing reports the drift.
5. The hooks analysis rightly prefers policy plus seams to forking (`hooks:30-37`). But it can
   only prevent forks if the seams are documented, which they are not yet.

## 2. Where pack source should live

| Option | Readable by any agent in any workspace | Same bytes that run | Fits Decision 3 | Verdict |
|---|---|---|---|---|
| Install-time copy in `$TRESTLE_HOME/packs/` | No: "home" is forbidden, and the home holds `agent.token` and `config.toml` | Yes | Only if agents never read it | Keep as the **runtime** location, not the reading one |
| A visible folder (`~/trestle-packs/`, or the checkout) | Yes, with host file tools | Not guaranteed: the checkout drifts, and the review's storm was exactly this (`review:194-205`) | Needs the rule relaxed for a path | Reject: reintroduces skew |
| **Served over MCP, read-only** | Yes, from any host and workspace, with no path | Yes: served from the snapshot copy (`snapshots.py:180`) and the resolved package (`validate.py:203-223`) | Yes: reading through the server is learning "from the server" | **Adopt** |
| Fork into the workspace | Yes, once copied | The copy diverges by design | Yes | **Adopt, on top of MCP serving**: it is how adaptation happens |

**Core versus pack source.** Keep Trestle's core and its home off-limits. Agents that read
`trestle/server/*` couple to internals and are tempted to "fix" Trestle. Agents that read the home
can see the token and the config. Pack source is different: it is user-level plugin code, meant to
be copied, and every pack plugin is already a template. Open it.

The rule agents quote ("never from Trestle's source or `~/.trestle`", `SKILL.md:139-140`;
`authoring.py:16, 61-66`; `_guide/root.md:4`) is the guide's wording, attributed to Decision 3. The
decision record itself says reading is *not* restricted: "An agent keeps its host's own file tools
for reading source" (`decision:406-408`). The amendment is therefore small. The guide and server
instructions say "do not read Trestle's core source or home; pack source is served at
`trestle://source/…`".

**Shape of the resource**, layered like the guide, so that no read floods the context:

- `trestle://source/{plugin}` returns the wrapper exactly as published, headed by its pack, owner,
  `source_sha256` and package digests, plus one line per library module (path and line count).
- `trestle://source/{plugin}/{module}` returns one library module. Files are read from disk; no
  plugin code is imported, so the host-never-imports rule (`review:181`) holds.
- `trestle://guide/packs/{pack}` returns the pack's README, its plugins, and its public API (the
  library's `__all__`, signatures and docstrings). The existing API child already does this for
  `trestle.*` (`plugin_api.py:31-40`), and a pack can name its API module in `pack.toml`.
- `describe_plugin` gains `pack`, `source_uri` and `api_uri`: fields on an existing answer, not a
  new tool.

## 3. Hackability versus safety and provenance

**Install-time copies: keep them.** They protect unattended runs from a branch switch in a
development checkout (`review:194-205`). That threat is real on the owner's machine. Copies cost
hackability only because the copy is the sole place the source exists. Serving it over MCP
removes that cost.

**Refusing agent publishes over pack names: keep it, and redirect.** In a single-user tool, "one
agent's file replaces a vetted plugin for every agent" (`catalog:132-133`) is not a security
argument. It is a reliability one. Agent A's flag for project X changes agent B's
`docker_stack` run in project Y, unattended and unannounced. Same-name overrides are global,
because the registry is keyed by bare name. A new name is per-project by construction. The refusal
should therefore be one answer that names the next move:
`publication.name_owned_by_pack: docker_stack belongs to stack@0.5.0 (trestle); publish your
adaptation under another name, for example docker_stack_<project>. Source:
trestle://source/docker_stack`.

There is one inconsistency to note. A loose file wins over a pack plugin (`catalog:130-131`), and
an agent with write access to a watched repository folder can create that file with its own tools.
So the refusal guards the MCP route only. That is acceptable, since a loose file is visible in the
project's git, but say so in the decision record.

**Grants.** A hacked plugin losing its grant is correct: the code that would hold the credential
changed. "Agent-published plugins never receive credentials" (`decision:255-256`) is stricter than
it needs to be. The digest pin already ensures an agent cannot grant itself. Let the operator run
`trestle credentials grant gh github_pr_open_acme` against a runtime publish, as a deliberate,
digest-pinned act. The refusal then names that one line. One more point, by *inference* from
`decision:375-379`: for `cli_passthrough` identities such as `gh`, the grant limits what a plugin
may *declare*. It does not stop an undeclared plugin running as the user from calling `gh`. Losing
the grant therefore costs convenience, not containment.

**Restricted profile.** No publishing is the deliberate trade, so keep it. Reading pack source
should remain available, because it is read-only. The way to adapt is to read through MCP, write the
file in the workspace, test it with `FakeContext`, and hand it to the operator. The guide already
says this (`authoring.py:45-49`). One gap: the allowlist is by bare name (`config.py:58-59`), so a
loose file named `docker_stack` inherits the pack plugin's allowlisting. `status` and doctor should
flag "allowlisted name served by an override".

**The model: packs are open-source templates.**

| Rule | Check against collisions and owner |
|---|---|
| Any installed pack's source is readable over MCP | Owner shown on every page; no precedence implied |
| Adapt under a new name; the runtime folder holds it (owner `local`) | No collision is possible |
| An optional `@trestle(adapted_from="docker_stack")` literal, read statically like other keywords | Rows show `adapted_from: docker_stack@stack 0.5.0`; `status` flags adaptations whose base has since been upgraded |
| A same-name agent publish is refused, with the redirect | Keeps the unattended runs of other agents stable |
| A loose operator file overrides, and the override is shown | Unchanged (`catalog:130-131`) |
| A pack may **declare** `overrides = ["github_pr_open"]` or `replaces = "github"` in `pack.toml`; undeclared collisions are refused | Fixes (d); the owner label remains informational |

## 4. The bar: outstanding or hackable

**Outstanding** (it just works):

| # | Criterion |
|---|---|
| O1 | Unattended: external commands go through `ctx.exec`, and waits honour `ctx.cancelled` and `ctx.remaining()` |
| O2 | One answer per dead end: a missing tool or login is `Blocked` with a `human_action`; bad input is refused at admission (`Literal`, not a `ValueError`); no bare `RuntimeError` |
| O3 | A bounded verdict: a top-level `ok`, counts, and one line per failure; a test failure is a result, not a raise (`SKILL.md:118`) |
| O4 | Every argument is documented in the schema, plus a copy-paste example reachable over MCP |
| O5 | Common variations are arguments (for `stack`: several compose files, profiles, `env_file`, `build`, pre and post commands) |
| O6 | Tests: a `FakeContext` test per wrapper, plus a gated live check |
| O7 | No known bug in a documented path |

**Hackable** (an agent can easily read and change it):

| # | Criterion |
|---|---|
| H1 | Source served over MCP: the wrapper and the library |
| H2 | The wrapper carries the recipe: the steps in order, each command as a visible argv list, and the judgment. Readable top to bottom, at most about 200 lines |
| H3 | Library calls go to a small exported API (`__all__`), served by the guide, and stable within a minor version |
| H4 | No opaque dependency between the wrapper and the command it runs |
| H5 | A copyable `FakeContext` test sits beside each wrapper |
| H6 | The five likeliest changes (a flag, a pre-step, a wait mode, skipping a stage, a different verdict) need edits to the wrapper only |

**Scores** (pass, partial or fail):

| | O1 | O2 | O3 | O4 | O5 | O6 | O7 | H1 | H2 | H3 | H4 | H5 | H6 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `docker_stack` | partial | fail | partial | partial | fail | partial | **fail** | fail | fail | fail | fail | fail | fail |
| `pytest_run` | fail | fail | fail | pass | partial | pass | pass | fail | partial | fail | partial | pass | partial |
| `migrate_apply` | fail | fail | partial | partial | partial | partial | fail | fail | partial | fail | partial | fail | partial |
| `integration_pipeline` | partial | fail | partial | partial | fail | partial | partial | fail | partial | fail | fail | partial | fail |
| `github` (planned) | pass if the fixes land (`catalog:242-252`) | pass if fixed | pass if fixed | ? | policy file | planned | eight known bugs | fail | fail with thin wrappers | open | pass (`ctx.exec`) | planned | fail |

The evidence behind the `stack` scores:

- **Commands bypass `ctx.exec`.** `docker_stack` runs Docker through `python-on-whales`
  (`compose_whale.py:17-26`). Its custom probes call `subprocess.run` (`readiness.py:65-77`), and
  its poll gets no cancel token (`compose_whale.py:53-56`).
- **Dead ends end in exceptions, not answers.** A missing Docker is a `RuntimeError`, not `Blocked`
  (`runner.py:40-42`). The same goes for a missing compose file and an empty spec (`runner.py:46-53`).
- **A documented path is broken (O7).** The docstring offers a minimal form, "compose_file +
  project with waves defined in the compose file" (`docker_stack.py:49-50`). That form builds a
  spec with no waves, and `all_services()` reads only declared waves (`spec.py:75-81`), so it
  always fails with "stack spec has no services" (`runner.py:50-53`). No test calls the wrapper.
  The only wrapper test covers `pytest_run` (`tests/test_example_with_fake_context.py:1-2`).
- **pytest and migrations run inside the child.** `pytest_run` runs pytest in-process
  (`pytest/runner.py:40`) and so needs the project's dependencies in the service venv (`org:183-187`).
  It also raises on a test failure (`pytest_run.py:27-29`).
- **`migrate_apply` validates late and invites a credential in args.** It refuses bad input with a
  `ValueError` at run time (`migrate_apply.py:22-34`). Its documented Yoyo example puts a password
  in `database_url` (`docs/packs.md:185`), against Decision 2.

**Thin or fat wrappers?** Neither extreme. A thin wrapper over a pip-installed library is the
hardest thing for an agent to adapt: the change lands in code it can neither edit nor republish. A
fully self-contained wrapper duplicates DAG planning, polling and log capture in every adaptation.
The rule should be **recipe in the wrapper, mechanism in the library**. The wrapper builds the
`docker compose` argv itself and runs it with `ctx.exec`. It decides the order and judges the
result. The library supplies `plan_waves`, `poll_until`, log attachment and result shapes, through
`__all__`.

This lines up with the organization analysis's own follow-ups: drive Docker through the core
engine, and run pytest and migrations out of process (`catalog:287-294`). Do those follow-ups
*before* `stack` ships as bundled, not after (`catalog:311`). For `github`, each plugin's flow
(push guards, monitor loop, verdict) belongs in its wrapper. Transport, models and policy loading
belong in the library.

## 5. What to keep, change or cut in revision 2

| Item | Recommendation |
|---|---|
| Pack folder, `pack.toml`, one `install` command, `owner` label, rescan without restart, the lock | **Keep** |
| Install-time copies for pinned packs | **Keep**, as the runtime location only. Agents read through MCP |
| Non-editable install of pinned libraries | **Keep.** With recipe-in-wrapper, adaptations seldom need library edits, and when they do, the agent copies one function into its wrapper |
| Path installs stay live | **Keep, and fix:** add the library tree's mtimes to the scan signature for live packs (`registry.py:151-164`), so a library edit republishes instead of storming `provenance_mismatch` |
| Path install over an installed bundled pack | **Change:** `--replace` swaps it to live and records the swap; `trestle pack install stack` swaps it back |
| Collisions between packs refused | **Change:** refuse undeclared collisions; allow `overrides = […]` and `replaces = "<pack>"` in `pack.toml`, shown on rows, in `pack list` and in `run_provenance` |
| Loose file overrides a pack plugin | **Keep**, and note that it is reachable by an agent writing to a watched repository folder |
| Agent publishes go to their own folder | **Keep** (`catalog:125, 137-138`) |
| Agent publish over a pack name is refused | **Keep**, but the refusal names the source URI and the rename. That is the whole difference between a dead end and a path |
| `trestle pack new` | **Keep it small** (wrappers only by default, `review:137-138`). Add `--from <pack>` to scaffold a renamed fork for case (d). It is an operator CLI; agents use the guide example instead |
| Guide | **Change:** add `trestle://source/…` and `trestle://guide/packs/{pack}`, plus a topic `adapting`: read the source, copy it, rename it, add `adapted_from`, test, publish. Fix `topics/packs.md` step 3, which still says "restarts the service". Amend the "do not read source" sentence for packs |
| Agent surface | **No new tool.** Add resources and fields on `describe_plugin`. `publish_plugin(based_on=…)` would change a frozen tool schema; the `adapted_from` literal in the source carries the same lineage, and survives being handed to an operator |
| Grants | **Change** `decision:255-256` to "never *automatically*": an operator may pin a reviewed runtime publish |
| `stack` as the first bundled pack | **Change the order:** fix O2, O3, O5, O7 and H2 first (`ctx.exec` argv in the wrapper, `Blocked`, `ok`, the minimal-form bug, compose variations), and export the API |
| `github` | **Change:** fat wrappers per plugin over a small exported library, and documented seams before any company forks |

## 6. Owner decisions needed

1. **Amend the Decision 3 wording for packs.** Pack source (wrappers and libraries) is open and
   served read-only over MCP. Trestle's core source and its home stay off-limits. Recommended: yes.
2. **Recipe in the wrapper, mechanism in the library** becomes the rule for bundled packs, with
   the O and H criteria as a gate test. Recommended: yes.
3. **`stack` meets the bar before it is bundled.** This moves the `ctx.exec` and out-of-process
   follow-ups ahead of bundling. Recommended: yes.
4. **Adapt under a new name.** Keep the refusal of same-name agent publishes, make it redirect, and
   add the optional `adapted_from` literal. Recommended: yes.
5. **Declared overrides.** `overrides` and `replaces` in `pack.toml`, with undeclared collisions
   still refused. Recommended: yes.
6. **Operator grants for runtime publishes.** Relax "agent-published plugins never receive
   credentials" to "never without an operator pin". Recommended: yes.
7. **Live installs rescan on library change,** and a path install can `--replace` a bundled pack.
   Recommended: yes.
8. **Restricted allowlists.** Flag (now) or pin (later) allowlisted names that an override serves.
   Recommended: flag now.
