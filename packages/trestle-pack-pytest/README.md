# The `pytest` bundled pack

`plugins/pytest_run.py` is the recipe: every command as a visible `ctx.exec` argv, the step order
and the verdict. `trestle_pack_pytest` is the mechanism, behind its `__all__`: find the project's interpreter and read pytest's JUnit XML into a bounded
`PytestReport`.
`tests/` calls the wrapper with `FakeContext`.

The operator installs it with `trestle pack install pytest` (or `trestle pack install stack`,
which installs it too); the service picks it up without a restart. An agent reads the installed
copy under `$TRESTLE_HOME/packs/trestle/pytest/` and adapts the wrapper by publishing a copy under
a new name. See [`docs/packs.md`](../../docs/packs.md).

The library uses the standard library and `trestle.plugin` only: a bundled library is installed
with `--no-deps`. Its tests run from the repository root:

```bash
python -m pytest packages/trestle-pack-pytest/tests
```
