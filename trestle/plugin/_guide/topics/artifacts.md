# Artifacts

- Files under `ctx.outputs` become artifacts when the run ends.
- `ctx.attach(path, name=...)` copies a file from the run's working area into the evidence now
  and returns its handle. The artifact is listed and fetchable while the run is still going.
- `ctx.artifact(name)` returns a staging path. A staged file that is never attached is promoted
  under `name` when the run ends.
- **Re-attaching under the same name replaces.** The listing shows the newest file per name,
  and earlier handles still fetch. A replacement takes no new artifact slot, so rewrite one
  `status.md` each round of a long job. Its bytes still count.
- Limits: 1 000 artifacts and 2 GiB per run. An artifact over a limit is not stored, and
  `limits_exceeded` names the limit. A refused `attach` returns the empty string.
- Declared secret values are scrubbed from every artifact. A binary artifact that holds one is
  refused.
