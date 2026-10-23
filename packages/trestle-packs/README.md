# trestle-packs

The workflow foundation that Trestle's composite plugins and the reference environment build on:
the `process`, `container`, `grant`, `testrun`, `toolchain` and `provision` ports, their fakes
(`trestle_packs.fakes`), and the shared `core` helpers (wave planning, readiness polling). It moves
into Trestle core later (P-15); its name is reserved.

The tool plugins `docker_stack`, `pytest_run`, `migrate_apply` and `stack_pipeline` are not here:
they are the bundled packs in `packages/trestle-pack-docker/`, `-pytest/`, `-migrate/` and
`-stack/`, each with its own library, installed with `trestle pack install stack`. See
[`docs/packs.md`](../../docs/packs.md).

```bash
pip install -e ".[dev,packs]"   # from repo root; [all] adds pytest, Alembic, Yoyo for the tests
```
