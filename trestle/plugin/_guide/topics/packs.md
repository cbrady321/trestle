# Packs

A pack is a folder: a `pack.toml`, thin `@trestle` wrapper files under `plugins/`, and
optionally the library they call:

1. Ship the library as a normal Python distribution (`library = "."` in `pack.toml`).
2. Write one wrapper file per tool. Each declares the library with
   `@trestle(packages=["your_pack"])`.
3. The operator installs the pack with `trestle pack install`; the service publishes its wrappers
   with no restart. Agents never install anything: report the command to the operator.
4. Unit-test the wrappers with `trestle.plugin.testing`.
5. After a library upgrade, wrappers stop with `execution.provenance_mismatch` until they are
   published again (`trestle pack upgrade` republishes them).

The tool plugins are bundled packs: `docker_stack`, `pytest_run`, `migrate_apply` and
`stack_pipeline`, installed with `trestle pack install stack`. `describe_plugin` gives a pack
plugin's `source_path`: the pack's installed copy, which you may read.

A pack's source, its wrappers and its library, is open: when a pack does not fit, read it with
your file tools and adapt it rather than falling back to the shell. Trestle's own source and home
stay closed. Running still goes through Trestle.

See `trestle://guide/examples/pack_wrapper`.
