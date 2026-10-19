# Import allow-list

A plugin may import the standard library, the packages it declares, and these Trestle modules
only:

- `trestle.plugin` (its exports: `Blocked`, `Context`, `ExecResult`, `trestle`) and
  `trestle.plugin.surface`;
- `trestle.workflow` and its public modules `trestle.workflow.declarations`,
  `trestle.workflow.loop`, `trestle.workflow.ports`, `trestle.workflow.units` and
  `trestle.workflow.values`.

Every other Trestle module, any name starting with `_`, `fastmcp` and `mcp` are refused with
`publication.validation_failed`.
