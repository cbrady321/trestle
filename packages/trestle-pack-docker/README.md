# The `docker` bundled pack

`plugins/docker_stack.py` is the recipe: every command as a visible `ctx.exec` argv, the step order
and the verdict. `trestle_pack_docker` is the mechanism, behind its `__all__`: find the docker CLI, plan start-up waves, wait within the run's budget, the
`StackResult` shape, and `fake_cli`, a fake `docker` CLI with Compose's semantics for tests.
`tests/` calls the wrapper with `FakeContext`.

The operator installs it with `trestle pack install docker` (or `trestle pack install stack`,
which installs it too); the service picks it up without a restart. An agent reads the installed
copy under `$TRESTLE_HOME/packs/trestle/docker/` and adapts the wrapper by publishing a copy under
a new name. See [`docs/packs.md`](../../docs/packs.md).

The library uses the standard library and `trestle.plugin` only: a bundled library is installed
with `--no-deps`. Its tests run from the repository root:

```bash
python -m pytest packages/trestle-pack-docker/tests
```
