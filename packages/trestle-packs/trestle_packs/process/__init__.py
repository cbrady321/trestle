"""Real port adapters for the workflow loop's process-facing families (L.SL-3.2, L.SL-3.3).

`CommandPort` implements B3's `ExecutionPort` (Command Execution) and `LocalProcessPort` the
`ResourceReads` + `ResourceCreate` + `ResourceOwned` protocols over local processes (Local Process
Supervision). They import only the standard library and `trestle.workflow` (BFD-47, N7): the types
they return are the workflow package's own. The same conformance suite that runs against the fakes
in `trestle_packs.fakes` runs against them unmodified (WR-PROOF-4).
"""
