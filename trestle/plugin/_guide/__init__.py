"""Package data for the authoring resources (`trestle://guide/plugins`, `trestle://guide/examples`).

`guide.md` and `examples/*.py` are read as files, never imported: `examples/` is not a package,
and no plugin folder lists it, so its files are published only by an agent that sends one."""
