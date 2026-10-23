# trestle-packs

The libraries behind Trestle's tool plugins in `examples/packs/`: `docker_stack`, `pytest_run`,
`migrate_apply` and `stack_pipeline`. Each wrapper carries its recipe (every command as a visible
`ctx.exec` argv, the step order, the verdict); this package holds the mechanism, behind each
module's `__all__`:

- `trestle_packs.docker`: find the docker CLI, plan start-up waves, wait within the run's budget,
  the `StackResult` shape;
- `trestle_packs.pytest`: find the project's interpreter, read pytest's JUnit XML into a bounded
  `PytestReport`;
- `trestle_packs.migrate`: the project's interpreter, Alembic revision lines, `MigrateResult`;
- `trestle_packs.fakes.docker_cli`: a fake `docker` CLI with Compose's semantics, for tests.

The plugins run docker, pytest, Alembic and Yoyo out of process, so the service's environment
needs none of them. Install alongside Trestle:

```bash
pip install -e ".[dev]"                    # from repo root
pip install -e "packages/trestle-packs"    # [all] adds pytest, Alembic, Yoyo for the pack's tests
```

Point Trestle at the example plugins:

```bash
trestle service restart   # after adding examples/packs to [plugins] paths in $TRESTLE_HOME/config.toml
```

Agent quick-start: [`docs/packs.md`](../../docs/packs.md). Install and lockdown: [`docs/install.md`](../../docs/install.md).

Verify adoption:

```bash
python scripts/smoke_packs.py
python scripts/demo_pack_workflows.py
```
