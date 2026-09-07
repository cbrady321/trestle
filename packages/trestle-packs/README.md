# trestle-packs

Pre-built workflow libraries for Trestle plugins: Docker stack orchestration, pytest runs, and database migrations.

Install alongside Trestle:

```bash
pip install -e ".[dev]"                    # from repo root
pip install -e "packages/trestle-packs[all]"
```

Point Trestle at example plugins:

```bash
trestle serve --plugin-dir examples/packs
```

Agent quick-start: [`docs/packs.md`](../../docs/packs.md). Install and lockdown: [`docs/install.md`](../../docs/install.md).

Verify adoption:

```bash
python scripts/smoke_packs.py
python scripts/demo_pack_workflows.py
```
