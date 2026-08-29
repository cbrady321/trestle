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

See [`hld/design-plugin-packs.md`](../../hld/design-plugin-packs.md) for the full design. Agent quick-start: [`docs/packs.md`](../../docs/packs.md).
