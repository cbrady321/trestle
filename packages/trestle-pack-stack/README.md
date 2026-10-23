# The `stack` bundled pack

`plugins/stack_pipeline.py` brings a Compose stack up, applies optional Alembic migrations, runs
pytest, and tears the stack down exactly once, in one run. It has no library of its own: it uses
the libraries of the `docker`, `pytest` and `migrate` packs, which `pack.toml` requires, so
`trestle pack install stack` installs all four. `tests/` calls the wrapper with `FakeContext`.
See [`docs/packs.md`](../../docs/packs.md).

```bash
python -m pytest packages/trestle-pack-stack/tests
```
