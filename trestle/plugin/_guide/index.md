# Writing Trestle plugins

A plugin is one file with one `@trestle` function. A pack is a library plus a folder of plugin
files that call it. Read one topic when you need it; do not read Trestle's source or home.

Rules:
1. Hint every parameter and the return (`dict[str, str]`, not `dict`).
2. Run commands with `ctx.exec`, never `subprocess`.
3. Raise `Blocked` when a person must act.
4. Loop on `ctx.cancelled`; re-attach one status artifact.
5. Declare non-stdlib imports in `packages=`; test with `trestle.plugin.testing`.

Topics, `trestle://guide/plugins/{topic}`:
- `shape-and-keywords`: writing a plugin
- `context`: logs, progress, events
- `exec`: running a command
- `blocked`: a person must act
- `artifacts`: files and status
- `stopping`: cancel and deadlines
- `testing`: tests, no server
- `imports`: what may be imported
- `types`: a type was refused
- `publishing`: getting it to run
- `refusals`: a publication code
- `workflows`: several steps as a tree
- `packs`: a library of plugins

Names: `trestle://guide/api`. Examples: `trestle://guide/examples/{name}` ({examples}).
