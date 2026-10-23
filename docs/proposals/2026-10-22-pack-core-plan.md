# Pack core: implementation plan (delivery order step 5)

2026-10-22. Plan for step 5 of the delivery order in
`2026-10-22-trestle-pack-catalog-design.md`, on top of v0.5.1. Decisions are cited by id from
`docs/decisions/packs.md` (P-n). The `examples/packs` migration (the last step-5 bullet) is left
to the session that owns `examples/packs/` and `packages/trestle-packs/`.

Hard rules kept throughout: ten MCP tools; installing is an operator CLI command only; no restart
on install (the service rescans); a `.py` file in a watched folder is still a plugin.

## Piece 1: the `trestle.packs` module and `trestle pack` (install, lock, list, remove)

- `trestle/packs/manifest.py`: read `pack.toml`: `[pack] name, summary, library, replaces,
  overrides`, `[requires] tools, packs`, `[config] files` (P-11, P-16, P-20). Unknown keys
  refused.
- `trestle/packs/lock.py`: `$TRESTLE_HOME/packs.lock` (JSON, written atomically under a file
  lock; each write moves its mtime). One entry per pack: name, owner, kind (`bundled`, `git`,
  `path`), location, ref, commit, version, pack dir, wrapper dir, library dir, `replaces`,
  `overrides`, config files, install time (P-1, P-5).
- `trestle/packs/install.py`: resolve the location (bundled from the release's
  `packages/trestle-pack-<name>/` (P-2); git at a tag or commit, a branch refused (P-4); a path
  in place), check `requires.tools` on the service `PATH`, install `requires.packs` from the same
  release (P-16), refuse undeclared plugin-name collisions with installed packs (P-11), wait for
  live runs of the replaced pack's plugins only (`--no-wait` refuses), copy pinned packs into
  `$TRESTLE_HOME/packs/<owner>/<pack>/` (P-7), install the library from the copy non-editable
  (path installs: editable; bundled `trestle*` libraries with `--no-deps`), write the lock.
  `--replace <pack>` swaps an installed pack out (P-13). The pip and git steps go through small
  injected runners so unit tests need neither.
- `trestle/cli.py`: `trestle pack install | list | remove | upgrade`.
- Tests: `tests/packs/` with fake runners; live checks for `git` (a local repo, `file://`) and
  `pip` (a throwaway venv), skipped when the host tool is missing, as the docker node is.

## Piece 2: the registry reads the lock (folder order, rescan, winner after validation)

- `trestle/server/registry.py`: watched folders in order: `[plugins] paths` (owner `local`),
  installed packs from the lock, `$TRESTLE_HOME/plugins/runtime/` (owner `local`). The scan
  signature adds the lock's mtime and, for path installs, the library's file times (P-13), so an
  install or a library edit republishes with no restart.
- Every candidate file is validated; the winner of a name is the first *valid* one in folder
  order, a declared `replaces`/`overrides` pack winning over the pack it names (P-11). An
  undeclared pack-to-pack collision that reaches the registry anyway (a hand-edited lock) is a
  `failed` row, not a silent pick.
- Each published plugin keeps its source: wrapper path, owner, pack label, what it overrides.
  `list_plugins` rows gain `pack` and `overrides` (P-1, P-11); `describe_plugin` gains `pack`,
  `overrides`, `source_path` and `adapted_from` (P-7, P-10); `@trestle(adapted_from=...)` is a new
  declared keyword.
- Restricted profile: an allowlisted name served by an override is flagged on its row
  (`override_flagged`), in `trestle pack list`, and in the service log (P-14).
- Files: `registry.py`, `plugin_schema.py`, `plugin/surface.py`, `common/types.py`,
  `server/project.py`, `server/doctor.py`, `trestle/packs/`.

## Piece 3: the runtime publish folder, the redirecting refusal, the `plugin_not_found` hint

- `publish_plugin` writes to `$TRESTLE_HOME/plugins/runtime/`, never into a pack copy or the
  operator's folder.
- A publish under a name a pack (or an operator's loose file) serves is refused with a new code,
  `publication.name_taken`; the message names the plugin's `source_path` and a suggested new name
  (P-10).
- `admission.plugin_not_found` for a name an available, not-installed bundled pack provides names
  the pack and `the operator runs: trestle pack install <pack>`. The bundled tree is scanned by
  AST, with no import.
- Files: `registry.py`, `admission.py`, `common/codes.py`, `trestle/packs/bundled.py`.

## Piece 4: grants for reviewed runtime publishes, configuration digests and pack identity in provenance

- `trestle credentials grant NAME PLUGIN --reviewed` pins an agent-published plugin's digests
  (P-12). Without `--reviewed` the refusal says how. The pin drops on any change, as for every
  grant.
- At admission the host reads each file named by the pack's `[config] files` variables (from
  `[plugins.env]`, else the service environment) and records its sha256 in the run's spec
  provenance, with the pack identity (P-1, P-20). `run_provenance` gains `pack` and `config`.
- Files: `credentials_cli.py`, `admission.py`, `common/types.py`, `query/fs.py`,
  `query/views.py`, `query/catalog.py`.

## Not in this branch

- The `examples/packs` migration (`install.sh`, `trestle init --upgrade`): owned by the session
  that owns `examples/packs/` and `packages/trestle-packs/`.
- `trestle pack new` scaffolding, `upgrade --regrant`, and the guide's packs topic rewrite
  (the P-6 wording session owns the guide text).
