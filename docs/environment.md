# Reference environment — operator and agent guide

**Audience:** operators who run the reference environment-and-test workflow, and agents that call it  
**Agent workflow:** [`docs/agents.md`](agents.md) · **Plugins:** [`plugins.md`](plugins.md) · **Packs:** [`packs.md`](packs.md)

The `reference_env` plugin brings up the reference environment in one call: a root declaration over
its services, each ready only when an authenticated call says so, every container it creates
released with the run and never a Docker volume. It ships in `packages/trestle-env`; the tree it
declares is data (`trestle_env/tree.py`) and the loop that walks it belongs to the workflow package.

---

## Operator

### What the operator provides

The plugin reaches the machine only through the operator's environment, never through a request:

| Variable | Meaning |
|----------|---------|
| `TRESTLE_DOCKER_PATH` | Absolute path of the `docker` executable. Unset: `docker` on the operator's own `PATH`, resolved once to an absolute path. Never a relative name. |
| `TRESTLE_DOCKER_ENDPOINT` | The engine endpoint, passed to every docker command as `--host` (the `default` context's socket may be absent; use the active context's). |
| `TRESTLE_IMAGE_<ROLE>` | The digest-pinned image of each role (`POSTGRES`, `HTTP_SUPPORT`), as `<repo>@sha256:<hex>`. Images are named by role, never pulled. |
| `TRESTLE_ENV_COMPOSE_FILE` | Absolute path of the Compose definition the dependency closure is derived from. |
| `TRESTLE_ENV_RECORD_STORE` | Optional: the container name of the environment's own Postgres that holds the durable fixture record. Unset: the run's `backend.postgres` container, which goes with the run. |

`python -m tests.proof.host.docker_gate run` exports the image pins and the endpoint for the proof
gate; an operator sets them once for a real deployment.

### The release-executable allowlist

When a run ends, the host releases what it created from the run record alone, with no plugin code.
A container is released by running `docker stop` then `docker rm` (never a volume flag), and the host
runs that command only when its executable is on the operator's allowlist
(`OperatorLimits.release_executables`). **The library default is empty**: a release whose executable
is not listed is reported `unknown` and never run.

The reference operator configuration, `packages/trestle-env/tests/fixtures/operator/config.toml`, is
the only place the docker path is listed. It is a Trestle home's `config.toml`:

```toml
[plugins]
paths = ["<the reference plugin directory>"]

[operator]
release_executables = ["<the resolved absolute docker path>"]
```

`trestle_env.operator.load_operator_config` fills the two placeholders from the values you give it
(a relative docker path is refused, not resolved) and returns the plugin directories and the
allowlist; build the server's `OperatorLimits(release_executables=...)` from the latter.

### What the operator can rely on

Every container the plugin creates is named for the run (`trwr-<run id>-<node path>`), created from a
pinned image without a pull, keeps its data on tmpfs (a Docker volume is never created) and is
stopped and removed with the run. A service is treated as ready only when its declared check
passes, never after a sleep or when its port is open: the supporting service
(`backend.http_support`) when `GET /health` answers `200` with exactly `ok`, and the backend
(`backend.postgres`, which starts only after the supporting service is ready) when an authenticated
`SELECT 1` succeeds over TCP.

### Reusing what is already running

A container already running under a service's own name (a Postgres you started yourself) is reused,
not replaced, when the run can prove it is that service's: its role and database are the reference
identity, it runs the pinned Postgres major version, and the authenticated `SELECT 1` succeeds. All
three are read from inside it. It is reported as `reused`, and it is never created over, stopped,
restarted, adopted or removed, whatever happens to the run (passed, failed or cancelled): only the
containers the run created are released.

### When a stage fails

A failure names the stage and the service. A service that never becomes ready ends when its declared
wait elapses: the answer is `blocked`, `primary.code` is `execution.postcondition_timeout`, and
`primary.path` is the node (`backend.postgres` is the readiness stage of the `postgres` service),
with the human action and the re-send advice V-11 gives that code. An unknown identifier fails the
catalog stage (before any run id); a Compose closure that cannot be derived fails the closure stage
before anything starts. Whatever the run did not create (a container you started yourself, a
service found already running) is never stopped, repaired or removed by a failure.

**Engine remediation is stub-proven only (D-8).** A catalog `remediation` pair declares a bounded
answer to a code (attempts and a total budget), and the runtime records each attempt. The bounded,
recorded safe start of the container engine is proven only against a stub engine starter that never
invokes the real docker binary (`WR-ENV-6:remediation-bounded-recorded`,
`WR-REMEDY-4:b-engine-safe-start-stub`). Starting the real engine, and the host's own start command,
is unverified and deferred (D-8); the reference tree declares no engine remedy, so an unreachable
engine ends the node `blocked` with its code and human action.

### Toolchain and project tasks

The catalog (`TRESTLE_ENV_CATALOG`, an absolute path to a catalog file; the reference catalog when
unset) lists projects, each with a toolchain pin and allowlisted task argv entries, and tests, each
naming a project task. Every test the catalog lists is a node, `test.<test id>`, that runs its
project's task once when the request names the test (`tests: ["<test id>"]`) and does nothing
otherwise. Nothing of a request but the test identifier reaches a command.

A task runs only as the catalog wrote it: its first entry is a bare tool name, resolved to an
absolute executable by the toolchain manager (`TRESTLE_MISE_PATH`, an absolute path, with
`TRESTLE_ENV_PROJECTS_DIR` holding one directory per project) to EXACTLY the pinned version, never a
tool found on `PATH` and never through `mise exec`, `mise run` or a shim. The toolchain identity (the
executable and the version it reports) is recorded in the run's evidence as `toolchain.task_start`
before the task starts. If the pin cannot be resolved the node ends `blocked` with
`execution.toolchain_missing`, naming the tool, before any task-start record or process exists;
Trestle installs, refreshes and downloads nothing.

A catalog test marked `provision` needs the environment's fixture record: a `provision.postgres`
node (after `backend.postgres`) submits it to the Postgres store exactly once and the test starts
only after it. The submit is not safe to resubmit: a store whose read lags an accepted submit is
polled, never written twice, and an accepted submit is not reported as the record being there. The
record is durable; no run removes it, and a second equivalent run finds it and submits nothing when
the store outlives the run (`TRESTLE_ENV_RECORD_STORE`; the run's own container does not).

A test node starts only after EVERY readiness pass: it needs both backends (`backend.http_support`
and `backend.postgres`, which are independent of each other), so a test never runs against a service
that is not ready. A catalog task marked `reports_tests` is a pytest selector: its counts come from
the JUnit report it writes, never from console text, and an exit status that disagrees with the
report is a contract violation, not a pass. A suite that only errors (a fixture that fails at
setup, a collection error) never passes: the answer is `failed`, `test_counts.errors` is positive,
and the failing test ids are in the run's evidence (`test.failing`), behind the answer's `detail`.

**What is proven, and what is not.** The toolchain leg is proven against a stand-in for `mise`
(`tests/fixtures/stubs/stub_mise.py`): it is STUB-PROVEN. The real tool's behaviour is unverified
(open question OPEN-MISE-HOST: no real `mise` conformance has been run), and so is Gradle's
(D-19). Whether toolchain state is serialized host-wide across runs is undecided (OQ-26); the leg
assumes the per-environment lease reading.

### Demo credentials (demo only)

**AWS is a DEMO in this workflow and is never real (D-9).** The only credential source the grant
adapter can reach is a demo issuer on loopback (the proof uses the stub
`tests/fixtures/stubs/stub_issuer.py`); it reads no profile, environment variable or credential file
of its own, and nothing here reaches a real identity provider. A credential is exactly three facts:
the identity's name, its expiry and its generation. Those are the only credential values Trestle
reads or records; the secret itself is never captured, in the run directory, the ledger, the
artifacts or the answer.

A tree that includes the demo-credential node (`credential.demo`) makes every dependent wait for it:

- **An identity that needs interactive sign-in** ends the node `blocked` with
  `execution.credential_interactive`, naming the identity and the one action that unblocks it
  (authenticate it with its issuer, then re-send). Nothing that depends on it starts.
- **A lifetime shorter than the root's deadline plus the finalization margin** (never a slice) gets
  one refresh first; if the credential is still too short, the node ends `blocked` with
  `execution.credential_lifetime_insufficient`.
- **A host-wide refresh** (one that changes what every environment sees) is not serialized across
  environments: which reading is right is open (OQ-26); the leg assumes the per-environment lease.

**What is proven, and what is not.** All of it is STUB-PROVEN against the demo issuer (D-9): what a
real identity provider does is unverified and out of scope. The labels are
`WR-ENV-8:mfa-blocked-human-action`, `WR-ENV-14:lifetime-vs-root-deadline-refresh-or-block`,
`WR-VERIFY-6:invalid-credential-not-ready`, `WR-ENV-13:refresh-in-place-container`,
`WR-ENV-13:refresh-in-place-local-app`, `WR-OWN-9:owned-process-restarted`,
`WR-OWN-9:not-owned-blocked-untouched`, `WR-REMEDY-4:b-credential-refresh-safe`,
`WR-PROOF-4:b-grant-suite` and `WR-VERIFY-8:b-stub-read-facets-grant`.

### Containment boundary: build daemons and helpers

A task the toolchain leg runs is contained by the run: a process it starts that stays attributable
to the run (in the run's process group and session) is ended by a stop or cancel within the stop
bound. A
Gradle-shaped build adds a case: Gradle starts a build daemon that outlives the build. That is
**prevented by declared configuration**: the catalog's task argv carries `--no-daemon` and
`-Porg.gradle.java.installations.auto-download=false`, its policy reads `DISABLED_BY_CONFIGURATION`
and helpers `PREVENTED`, and a task that loses either flag blocks before anything starts.

**The boundary.** A helper started outside Gradle's daemon machinery (a language server the tool
launches itself, a detached shell job that double-forks and is reparented) leaves the run's group
and is not contained: nothing that reaches a run's processes by group or by parentage reaches it. A
task that is known to do this declares it, and its policy is `DISCLOSED` with that text: its release
descriptor says `helpers_disclosed`, so the run's group is reported `unknown` rather than clean, and
the answer names the disclosure. It is disclosed as a boundary, unproven pending real Gradle (D-19).
The labels are `WR-CANCEL-7:prevented-by-config`, `WR-CANCEL-7:contained-within-bound` and
`WR-CANCEL-7:disclosed-boundary`, all STUB-PROVEN against `tests/fixtures/stubs/stub_gradle.py`.

### Publishing the plugin

Point the server at `packages/trestle-env/trestle_env/plugins` (the `[plugins] paths` entry above, or
`--plugin-dir`). It holds one plugin, `reference_env`; the modules whose names start with an
underscore are its composition root's helpers and are never published. The plugin declares
`trestle_env` and `trestle_packs` as its packages, so a change to either after publication stops the
run with `execution.provenance_mismatch` until you republish.

---

## Agent

`describe_plugin(plugin_id="reference_env")` gives the input schema. There is no free-form field:
`env` names the environment (the Compose project name; two runs with the same `env` exclude each
other), and `services`, `tests` and `overrides` are sets of catalog identifiers.

```json
{"env": "checkout-dev", "services": ["postgres"]}
```

An identifier the catalog does not hold is refused before a run id exists, with
`admission.unknown_identifier`, the identifier and where the valid ones are listed
(`identifier_sets.services`, `.tests` or `.overrides` of the declared plan). A repeated identifier is
refused by the schema with `admission.invalid_args`. Neither costs an effect: no container is
created, no command is run.

When the operator configures a Compose definition (`TRESTLE_ENV_COMPOSE_FILE`), the plugin derives
the dependency closure of the selection from it before anything starts; a service the catalog does
not hold, or a definition Compose cannot read, ends the run with the resolver's own code and no
container created.
