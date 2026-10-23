# Writing Trestle plugins

A plugin is one file with one `@trestle` function. A pack is a library plus a folder of plugin
files that call it. Read one topic when you need it. A pack's source is open to read and adapt;
Trestle's own source and home are not.

Rules:
1. Hint every parameter and the return (`dict[str, str]`, not `dict`).
2. Run commands with `ctx.exec`, never `subprocess`.
3. Raise `Blocked` when a person must act.
4. Loop on `ctx.cancelled`; re-attach one status artifact.
5. Declare non-stdlib imports in `packages=`; test with `trestle.plugin.testing`.
6. Several steps are one plugin; raise `Failed` when the check fails.

Topics, `trestle://guide/plugins/{topic}`:
- `shape-and-keywords`: writing a plugin
- `context`: logs, progress, events
- `exec`: running a command
- `credentials`: tokens by name
- `failed`: when the check fails
- `blocked`: a person must act
- `artifacts`: files and status
- `stopping`: cancel and deadlines
- `testing`: tests, no server
- `imports`: what may be imported
- `types`: a type was refused
- `publishing`: getting it to run
- `refusals`: a publication code
- `workflows`: several steps in one plugin
- `packs`: a library of plugins
- `callbacks`: a POST when a run ends

Names: `trestle://guide/api`. Examples: `trestle://guide/examples/{name}` ({examples}).
