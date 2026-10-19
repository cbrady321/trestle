# Pack-driven core updates: plan

The scope the owner settled on 2026-10-19, after the pack analysis
(`2026-10-19-packs-analysis.md`) and its review (`2026-10-19-packs-recommendations-review.md`).
Item names (R1, G1, …) are the review's; each item's detail is under the review's heading
`## Per recommendation` or `## Gaps the report missed`.

## Goal

Make Trestle's core give pack authors safe, small building blocks, so packs stop hand-rolling
subprocess, waiting and environment plumbing, and plugins behave correctly under the per-user
service. Do this without breaking the recorded decisions in `docs/decisions/service-and-credentials.md`:
one per-user service, MCP only, agents never need a shell, agents name credentials, never values.

Done means:
- every item under **In scope** is built, tested and documented;
- the full suite is green, apart from records that need a host gate run;
- all local work, from before this plan and from it, is on `origin/master` as **one commit** (see **Delivery**).

## In scope, in order

1. **R7b: thread-safe `RuntimeContext`** (S). Every write path on the plugin context takes one
   lock: log, progress, event, attach (including T3 same-name replace), the artifact and event
   counters, and limit markers. *Acceptance:* a plugin logging and attaching from 8 threads
   leaves an `events.ndjson` in which every line parses, with no lost or merged rows.
2. **R1: `ctx.exec(argv, ...)` and `ctx.remaining()`** (M), reshaped as in the review:
   - a per-call timeout that defaults to the time left;
   - the whole process tree killed on timeout;
   - output captured and bounded, and kept by default in a single artifact, so a long poll doesn't use up the artifact count;
   - the child's own environment inherited;
   - a `credentials=` keyword reserved, but not yet built.

   First amend `docs/decisions/service-and-credentials.md` (`## Implementation sequence`) so that
   `ctx.exec` comes ahead of the `exec` plugin step and that step wraps it. *Acceptance:* a
   command that outlives its timeout is killed with its children and returns a timed-out
   result, and a command with 10 MB of output stays within the cap.
3. **G1: plugin environment under the service** (S/M). Add an operator-owned environment table
   to `config.toml` that the service passes to plugin children. Values come only from operator
   config, never from an agent. *Acceptance:* the shipped env pack's `TRESTLE_DOCKER_PATH`,
   `TRESTLE_IMAGE_*` and `TRESTLE_ENV_COMPOSE_FILE` reach a plugin run under `trestle service`.
4. **R4a: `Blocked` for plain plugins** (S). A plain plugin can raise
   `Blocked(code, human_action, resend)`, and the run's answer reads `blocked` with that human
   action. Plugin codes are namespaced so they cannot collide with Trestle's codes.
   *Acceptance:* a plugin that finds `aws` not logged in ends `blocked` with the login step as
   its human action. Prove it against a stub `aws`, with no real AWS.
5. **G2: credential grants pin more than the entry file** (doc). Amend Decision 2 in the
   decision record so a grant pins the declared packages' digests (or the snapshot id), not only
   the entry file's `source_sha256`. This lands before credential grants are built.
6. **R6a: warn on undeclared imports at publication** (S). Warn, don't refuse, when a plugin
   imports a non-stdlib package that is not listed in `packages=`. Add `packages=` to
   `examples/packs/*.py`. *Acceptance:* publishing the old examples warns, and the fixed ones
   don't.
7. **R3: `FakeContext`** (S). A fake plugin context in `trestle.plugin.testing` that implements
   the same surface, including `exec` and `remaining`, plus a pytest fixture that calls a plugin
   function with it. No new distribution, and no run id. *Acceptance:* a pack's plugin is unit
   tested with no server.
8. **T5: packs ship a wrapper folder** (S, mostly docs). A pack ships a folder of thin
   `@trestle` wrappers, and the operator lists it in `[plugins] paths`; no entry-point loading.
   Make sure `docs/packs.md` and `docs/plugins.md` say so. *Acceptance:* a fresh pack install
   reaches a listed plugin from those docs alone.
9. **G3: a clean-stop window for plain plugins** (M). A plain plugin gets a declared grace
   period after a cancel or deadline before it is killed, through a SIGTERM handler in the child
   or a declared stop grace. Write the decision first, as a short section in the decision record
   or a new `docs/decisions/` file. *Acceptance:* a plugin that checks `ctx.cancelled` in a loop
   writes its final status artifact before it is stopped.
10. **R5 / T7: locks on several keys** (L). Last, and only after 1–9 have landed in the
    integration branch. The detail and the risks (one-key records, `sched.json`, servers of mixed
    versions on one home, key namespacing) are in the review under `### R5 multi-key leases / T7`.
    Confirm with the owner before starting it; it may be split out.

## Out of scope

- **Dropped by the owner:**
  - R4b, the declared `requires_tools` and `env` keywords: a plugin can check its own tools and raise `Blocked`.
  - R9 and G5, recording and showing pack versions: `packages=` digests already catch a changed pack.
- **Deferred by the owner:** G4, packs declaring their own machine-touching modules. If the pack
  forks are merged, extending the hard-coded list in `trestle/child/validate.py` is enough.
- **Dropped by the review:**
  - R2, cancellable waits: core already kills on cancel.
  - R6b, the subprocess and sleep lint.
  - R6c, the effects advisory: it already exists as `PORT_MODULES`.
  - R8, status, attach-text and table: covered by `live-run-visibility`.
- **Deferred by the review:** R7 (a durable lifetime for plain plugins, which contradicts B3-C3/C4),
  R10 (a public conformance kit) and R11 (a tree on-ramp).
- **Rejected earlier:** a shell command to start runs (T4).

## Delivery

All local work goes to `origin/master` as **one commit** on top of `origin/master` (`f97cc1cf`
on 2026-10-19). That covers:
- the 3 local `master` commits;
- `live-run-visibility`;
- `service-mode`;
- every item above.

It is a fast-forward, not a history rewrite, unless the owner says otherwise. Pushing needs the
owner's explicit go-ahead.
