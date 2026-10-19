# Decision record: a clean-stop window for plain plugins

**Status:** decided 2026-10-19 (pack review item G3); implemented with the pack-driven core
updates.
**Date:** 2026-10-19
**Related:** [`service-and-credentials.md`](service-and-credentials.md) (the per-user service a
cancel arrives through), [`plugins.md`](../plugins.md) (`@trestle` keywords, `ctx.cancelled`),
B2-C10 (the stop: stop row, release slice, then the group kill).

## The problem

A plain plugin has no time to stop cleanly. Its implicit plan declares no release walk, so its
release slice is 0 (B2-C1): on a cancel, or at its deadline, the conductor records the stop and
kills the whole process tree on its next poll, about 50 ms later. The child has no SIGTERM
handler. A plugin in the middle of writing its status artifact loses it, and its return value is
never written. A pack's hour-long poll loop that checks `ctx.cancelled` never gets to act on it.

## Options considered

1. **A SIGTERM handler in the child** that raises in the main thread, with a fixed grace before
   the SIGKILL. Rejected: the exception lands at an arbitrary point, which is the mid-write
   interruption this is meant to prevent; worker threads are not reached at all; and the kill
   already goes to the whole tree, so a plugin's own subprocesses would die under it before it
   could act.
2. **A grace the plugin declares**, reusing the release slice the conductor already honours for
   workflow roots. Chosen.

## Decision

- **`@trestle(stop_grace=<seconds>)`**: a literal keyword, read statically at publication like
  every other one. It enters the snapshot identity **only when declared** (the `repeatable`
  precedent), so no existing snapshot id or golden changes.
- **It becomes the plain plugin's release slice**, capped at the operator's
  `OperatorLimits.release_slice` (`TRESTLE_RELEASE_SLICE_S`, 10 s by default). The operator, not
  the plugin, bounds how long a stopped run may linger. Nothing else about the stop changes: the
  stop row is recorded, the slice elapses, and the B2-C10 group kill runs as before.
- **The window comes out of the plugin's own deadline**, as a workflow root's does: the release
  point is the deadline minus the slice. The run's deadline, finalization margin and admission
  arithmetic (`lease.would_overrun` already adds the slice) are unchanged.
- **The plugin sees the stop through `ctx.cancelled`**, which is now true once the cancel flag
  **or** the release-point flag exists. A plugin that loops on `ctx.cancelled` and on
  `ctx.remaining()` writes its final artifact and returns. The run still ends with the stop's
  cause (`cancelled` or `timed_out`); what the plugin attached in the window is kept.
- **A workflow may not declare it**: publication refuses `stop_grace` on a workflow entry, whose
  root release slice is the operator's.
- **A plugin that does not declare it** keeps a release slice of 0 and is killed exactly as
  today.

## What this does not change

- The kill still covers every process attributable to the run (B2-C10); a declared grace never
  keeps a process past it.
- A durable lifetime for plain plugins (review R7) stays deferred: the grace is a few seconds of
  clean-up, not a host-kept process.
