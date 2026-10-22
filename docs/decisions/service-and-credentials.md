# Decision record: one service per user, and credentials by name

**Status:** decided; changes 1, 1a, 2 and 3 of the implementation sequence are implemented (the
bearer token, the stdio transport removed, `trestle service`, and credential identities with the
`encrypted_file` store, grants by digest and `trestle credentials`), and changes 2a, 2b, 2c and 2d
(0.5.1: session ends, the status view and refusal fields, the `exec` plugin, the agent docs); the
rest is not. Where the
code differs from the text below, the "As implemented" subsections after the sequence table say
how and why.
**Date:** 2026-10-19
**Related:** [`security.md`](../security.md) (lockdown, profiles, what the boundary is not),
[`install.md`](../install.md) (host wiring), [`environment.md`](../environment.md) (the demo
credential node and D-9), [`plugins.md`](../plugins.md) (`@trestle(secrets=...)`, snapshot
identity).

Three decisions, taken together because each depends on the one before: Trestle runs as one
per-user service per machine; that service is the only thing that ever holds a credential, and
agents name identities, never values; and the service is the only way an agent reaches Trestle at
all: the stdio transport is removed, and the agent surface is made sufficient so an agent never
needs a shell for work Trestle should do.

---

## Decision 1: one per-user service per machine

Trestle runs as **one service per OS user per machine**: a macOS LaunchAgent in
`~/Library/LaunchAgents`, or a `systemd --user` unit on Linux. Never a root or system daemon.
Never one process per MCP host. Every host on the machine (Cursor, Claude Desktop, Claude Code,
a script) points its MCP config at the service's loopback URL.

### Why

- **Runs must outlive agent sessions.** A stdio server is a child of the host that spawned it.
  When the host exits, the server dies with it and every run it admitted ends `interrupted` at
  the next start. A run that finishes without a person present is the product's first
  priority, and a server whose life is tied to a chat window cannot give that.
- **One declared environment.** Today the child environment is the server's `os.environ`
  copied unchanged (`trestle/common/pyenv.py`, `build_child_env`), so a plugin inherits whatever
  the host that spawned the server inherited. A GUI host carries no shell environment; a
  terminal host carries all of it, tokens included. A service has one environment, written down
  in its unit, and that is the one every plugin sees.
- **One credential holder, one refresh loop, one audit trail.** Decision 2 puts credentials in
  the server. With one server per host there would be one holder per host, each refreshing on
  its own, with the record of who used what split across them. With one service there is one.
- **One profile in force.** `config.toml` is read once at start. With several servers on a home
  there are several readings, and a host started before a config change runs the old profile.
- **The OS user boundary is the only isolation Trestle has.** A plugin runs as the user,
  unsandboxed. A system daemon serving several users would have to invent an isolation Trestle
  does not have, and that would be a different product. One service per user keeps the
  boundary where it already is.

### What stays

- **Stdio does not stay.** An earlier draft of this record kept it as a fallback for a machine
  without the service, with the rule that a stdio server never brokers credentials. Decision 3
  removes the transport instead: the service is the only server an agent reaches, so the only
  process that ever holds a value is the one with a declared environment and a lifetime of its
  own, and there is no second kind of server to write rules for.
- **Many servers on one home** (v0.3.1) keeps working. The service is the recommended way to
  run one; it is not a lock against running others.
- **SIGTERM drain** is already implemented: a server stopped with SIGTERM refuses new runs and
  finishes the runs it admitted (see [`install.md`](../install.md)). The service unit uses it.

### When the agent's session ends while a run is live

**A run belongs to the server, never to the MCP session that sent it.** The session id on the
run's `created` row (`caller_session`) exists for one purpose: under the restricted profile it
scopes `cancel` to the session that received the run (`Project.cancel` in
`trestle/server/project.py`). It is not ownership. **On session loss the run continues.**
Nothing is cancelled on disconnect, because a run finishing without a person present is the
point of the service mode, and a chat window closing is the most ordinary way a person leaves.

What already bounds a run whose session is gone, none of it new:

- **The deadline**, fixed at admission and enforced by the server; a run held on `after=` is
  bounded by `hold_expired` as well.
- **Release on every terminal path**, passed runs included (K-8): the processes Trestle can
  attribute and the containers the run created are ended whether the run succeeded, failed,
  was cancelled or timed out.
- **The recorded answer**, retrievable by a new session through the same idempotency key
  (`query(view="run_by_key")`, `await_runs(keys=[...])`) instead of a duplicate run.
- **Retention**: `metadata_days`, `artifact_days`, `abandoned_hours` and `storage_cap_bytes`
  in `[retention]`, applied by the sweep whether or not anyone asks after the run.

Three gaps to close, each in the implementation sequence:

1. **Orphan visibility.** Nothing records when a streamable-HTTP session ends, so a run whose
   session is gone looks like any other live run. Record the session's end, and let
   `trestle doctor` and the `recent_runs` view mark runs whose session is gone. This is
   information for the operator, not a trigger: marking a run orphaned changes nothing about
   it. A session has ended when the client deleted it (a DELETE answered 2xx), or when it
   expired: no request in flight for the MCP library's 30 minutes, or a request on it answered
   404. A dropped connection is not an end; a killed host is seen about 30 minutes later.
   `server_gone` (the server that opened it is not running) is computed by readers, never
   written. Implemented in 0.5.1 (change 2a).
2. **An operator cancel.** The unscoped path exists: `Project.cancel` with `caller_session`
   None is the operator's CLI or console and is not scoped. No CLI command exposes it today
   (`trestle/cli.py` has `serve`, `ops serve`, `init`, `doctor`, `recover`, `pin` and `unpin`,
   and `trestle ops serve` is read-only). Add `trestle cancel <run_id>`.
3. **`on_session_lost`**, an optional per-plugin declaration with the values `"continue"`
   (the default, and everything above) and `"cancel"`, for a plugin whose run is worthless
   without its caller: one holding an environment lease for an interactive session, for
   instance. It is a literal in the decorator like every other keyword, recorded in the
   manifest, and it needs gap 1 to act on. `describe_plugin` shows it. Still its own change:
   it depends on the idle timeout, which for now is the library's 30 minutes.

**Verified.** A client that drops a `run(completion="terminal")` wait mid-run leaves the run
untouched: `tests/single/control/mcp/test_sever_rejoin.py` (cancel notification, DELETE,
SIGKILL; the run ends by its own deadline and a re-send joins it) and
`tests/single/control/mcp/test_session_loss.py::test_dropped_terminal_wait` (a raw connection
drop, a DELETE and a cancel notification; a run that ends on its own reads `succeeded`, `passed`,
no stop, and its key joins the one run).

**The reverse case is already covered.** When the service dies and agents are alive: its runs
end `interrupted` at the next start, a `repeatable=True` key re-sent by the agent starts a
fresh run (`retry_of` names the interrupted one), a SIGTERM stop drains first, and under the
service mode launchd `KeepAlive` (or `Restart=always`) starts it again.

### The CLI

```
trestle service install     # write and load the LaunchAgent plist / systemd --user unit
trestle service uninstall   # unload and remove it
trestle service status      # loaded, running, pid, endpoint, home, version
trestle service start       # load the unit if needed and start it
trestle service stop        # SIGTERM drain; the unit stays installed, KeepAlive paused
trestle service restart     # stop, then start
trestle service upgrade     # install the new trestle, migrate the home, restart
```

Install, upgrade, start and stop are the operator's whole lifecycle; each is one command with
no manual step, because a service that needs hand-editing after every release will be left
running an old build. Requirements:

- **`install`** is idempotent: run again on an installed service it reports the state and
  changes nothing; run with a newer `trestle` it behaves as `upgrade`.
- **`upgrade`** is `pip install -U` (or the editable checkout's new commit) followed by the
  home migration `init --upgrade` does today, then `restart`, in that order, and it refuses to
  start the new build while a run is live unless `--drain` is given (wait for the runs to end,
  then restart). `status` shows the running build's version beside the installed one so a
  stale service is visible.
- **`start` and `stop`** are the only ways documented for starting and stopping Trestle. `stop`
  drains like `restart` does, then unloads so `KeepAlive` does not bring it back; `start`
  undoes that. Both report in one line.
- **One page.** `install.md` and the README's Install section are rewritten so a fresh machine
  reaches a running, upgradable service from four commands; the "Several servers on one home"
  and stdio material goes. `agents.md` and the skills say an agent that finds the service down
  reports it and stops; none of them runs `trestle` itself.

`install` generates the unit for the current user with:

- the absolute path of the `trestle` binary that ran `install`, serving
  `--transport streamable-http` on the loopback port;
- an **EnvironmentVariables set of exactly `PATH`, `HOME` and `TRESTLE_HOME`**, nothing else.
  Everything a plugin needs beyond that is declared in `config.toml` or supplied by the
  operator through the home, never inherited from a shell;
- `KeepAlive` and `RunAtLoad` (and their systemd equivalents, `Restart=always` and
  `WantedBy=default.target`), so the service is up after login and comes back after a crash;
- stop by SIGTERM, which drains.

The unit is a file the operator can read and edit. `install` refuses to overwrite a unit it did
not write.

### Bearer token on the loopback HTTP transport

Today any local process can reach `127.0.0.1:18732`, and [`security.md`](../security.md) says
so and tells you to prefer stdio. With stdio removed (Decision 3) every agent is on that port,
and once the service holds credentials "any local process" is no longer acceptable: "any local process" includes every program the user ever ran, and the MCP surface
admits runs that can use a granted identity.

So the streamable HTTP MCP transport gains a **required bearer token**:

- `trestle init` (and `trestle service install`, when the file is missing) generates
  `$TRESTLE_HOME/agent.token`, a random token in a file with mode `0600`.
- The server reads it once at start and refuses every MCP request that does not carry it as
  `Authorization: Bearer <token>`. There is no flag to turn the check off on HTTP.
- Hosts send it through their MCP config's `headers` field, expanded from an environment
  variable (`${TRESTLE_AGENT_TOKEN}` in hosts that expand) or pasted. [`install.md`](../install.md)
  will show both.
- There is no stdio transport left to exempt (Decision 3).
- The **ops port stays read-only** and stays without a token: it exposes nothing that admits a
  run or reaches a credential.

The token is possession of the file, which the OS already limits to the user. It does not make
the service multi-user and it is not a login; it makes "a process that can read the user's
home" the bound instead of "a process on the machine".

---

## Decision 2: agents name identities, never values

**No credential value crosses any agent-facing surface.** Not in run args, not in an answer,
not in an artifact, not in the console, not through `fetch`. An agent names an identity; the
service decides whether that plugin may use it, resolves the value, and hands it to the run on
a path the agent cannot read.

### What exists today, and what is wrong with it

`@trestle(secrets=[...])` (MC-CORE-13, `trestle/common/redact.py`) names secret **arguments**.
The agent supplies the values in `run` args; admission redacts `spec.json`, passes the real
values to the child in `TRESTLE_RUN_SECRETS`, which the child pops before importing plugin
code, and every write path scrubs the values. The scrub is sound and is kept. The flaw is
upstream of it: **the agent already had the value, which is the thing to avoid.** Whatever
reaches an agent's context reaches its transcript, its host's logs and whatever the model
emits. The `secrets=` form is therefore **deprecated**: it keeps working, `describe_plugin`
marks it deprecated, and new plugins declare identities instead.

The grant pack (`packages/trestle-packs/trestle_packs/grant`) already has the shape of the
right thing, as a demo: an identity is a name, an expiry and a generation; the value is never in
a return value, log or record (WR-EVID-12); delivery is a run-scoped channel directory with a
`credentials` file refreshed in place, mounted read-only into a container or named to a local
process by `TRESTLE_CHANNEL_DIR`; a human-needed sign-in ends the run `blocked` with
`execution.credential_interactive` naming the one action; a lifetime shorter than the run
deadline plus the finalization margin is `execution.credential_lifetime_insufficient`
(WR-ENV-14). All of it is proven against a stub issuer and nothing real (D-9). This decision
makes that shape the product's credential model and leaves D-9 in force.

### (a) Configuration: `[credentials.<name>]`

`config.toml` gains one table per identity. It is read once at start like the profile, so
nothing an agent sends can add, change or widen one.

```toml
[credentials.ci-reader]
kind = "aws_assume_role"
role_arn = "arn:aws:iam::000000000000:role/ci-reader"   # placeholder; never a real account here
grants = ["repo_tests", "fixture_refresh"]

[credentials.release-bot]
kind = "github_app"
app_id = "0"
installation_id = "0"
repositories = ["example/repo"]
permissions = { contents = "write", pull_requests = "write" }
grants = ["release"]

[credentials.gh]
kind = "cli_passthrough"
tool = "gh"
grants = ["release"]
```

**Kinds.** Each selects a store backend (see "The credential SDK" below); each is an adapter behind the existing grant ports (see (e)).

| `kind` | What it resolves | Where the value lives |
|---|---|---|
| `keychain` | a static secret read from the OS keychain (macOS Keychain, Secret Service on Linux) by item name | in the keychain; in memory on the `WorkOrder` for the run |
| `onepassword` | a static secret read from 1Password through `op` with a service account, by item reference | in 1Password; in memory for the run |
| `encrypted_file` | a static secret from an encrypted `0600` file under `$TRESTLE_HOME` | in that file; in memory for the run |
| `aws_assume_role` | STS `AssumeRole` with **session name = the run id** and **duration bounded by the run deadline plus the finalization margin** | in STS; in memory for the run; refreshed in place for a long run |
| `github_app` | an installation access token minted from the app's private key (keychain), **scoped to the listed repositories and permissions** | in GitHub; in memory for the run |
| `cli_passthrough` | nothing: the plugin's `gh` or `aws` subprocess uses the tool's own keychain or SSO cache | **never materialized** by Trestle; the grant only says the plugin may invoke the tool as this identity |

**Grants are bound to the plugin's recorded code digests.** `grants` names plugins, and a
grant is recorded against the plugin's `source_sha256` **and its `package_digests`** (both
recorded at publication, see [`plugins.md`](../plugins.md)). The entry file alone is not
enough (amended 2026-10-19, G2): a pack's plugin is a thin wrapper over a declared package, and
a republish after a changed package (`provenance_mismatch`) keeps the wrapper's `source_sha256`
while the code that would hold the credential has changed. A republished plugin of the same name
whose entry file **or any declared package** differs **loses the grant** until the operator
re-pins it: `trestle credentials grant <name> <plugin>` records the current digests, and
`config.toml` carries them as `grants = [{ plugin = "repo_tests", source_sha256 = "...",
package_digests = { trestle_packs = "..." } }]` (the bare-string form above is sugar that `grant`
rewrites). The pin is not the `snapshot_id`: that also covers the runtime version, so every
Trestle upgrade would drop every grant. **This settles open question F-13(d) for credentials:
agent-published plugins never receive credentials**, because an agent's `publish_plugin`
produces a digest no operator has pinned.

### (b) Plugin surface

- `@trestle(credentials=["ci-reader", "gh"])` declares the identity names the plugin uses. It
  is a literal, read statically at publication like every other keyword, and it is recorded in
  the manifest and counts toward the snapshot identity.
- `describe_plugin` lists the declared identities by name and nothing else about them.
- **`admission.credential_not_granted`**: the plugin declares an identity whose grants do not
  cover this plugin at this digest. Refused before a run id, run directory or process exists.
- **`admission.credential_unknown`**: the plugin declares an identity no `[credentials.<name>]`
  table defines. Refused the same way.
- A provider that needs a human (`aws sso login`, `gh auth login`, an MFA prompt) ends the run
  **`blocked` with the existing `execution.credential_interactive`**, naming the identity and
  the one action that unblocks it. The run never waits on a prompt nobody will answer.
- `execution.credential_lifetime_insufficient` and `execution.credential_stale` keep their
  meanings from the grant pack.

### The credential SDK

Three faces, and no new MCP tool: the surface stays at ten.

**Declaration, static at publication.** `@trestle(credentials=...)` maps a store entry name
to the delivery shape the plugin will use. It is a literal, read from the source at
publication like every other keyword (K-5), so it is recorded and part of the snapshot
identity. Admission checks the grants by source digest and refuses
`admission.credential_not_granted` or `admission.credential_unknown` before a run id exists.
`describe_plugin` lists names and shapes, never values.

**Use at run time, through the plugin `Context`** (`trestle/plugin/surface.py`, today `log`,
`progress`, `artifact`, `attach` and `event`). The shapes, strongest first, so that the value
rarely exists as a string in plugin code at all:

| Shape | What the plugin gets | Where the value is |
|---|---|---|
| `ctx.exec(argv, credentials=[...], cwd=...)` | the subprocess result | Trestle spawns the process and injects the credential; the plugin never touches it. **This is the shape to steer authors toward.** |
| `trestle cred-helper` (git credential helper, AWS `credential_process`) | nothing; the tool asks the helper | answered by the service only for a live run holding the grant (see (d)) |
| `ctx.credential_file(name)` | a run-scoped `0600` path | a file refreshed in place; the grant pack's channel directory (`packages/trestle-packs/trestle_packs/grant/delivery.py`) is already this |
| `ctx.credential_env(name)` | a `dict` of variable names to values | in the plugin's memory. The weakest shape, kept for tools with no helper protocol |

Every shape registers its values in the run's scrub set, so whichever one a plugin uses, a
value that reaches a log, a return, a raise or an artifact is `<redacted>` on disk and in every
answer.

**The store, behind the service.** A provider interface in the server with backends:

- macOS Keychain, through `security`;
- 1Password, through `op` with a service account;
- an encrypted `0600` file under `$TRESTLE_HOME`;
- native backends that mint short-lived, scoped credentials: STS `AssumeRole` with session
  name = the run id and duration bounded by the deadline plus the finalization margin, and
  GitHub App installation tokens scoped to repositories and permissions.

`config.toml` names store entries (the `[credentials.<name>]` tables above; `kind` selects the
backend). Nothing on the agent surface lists or reads them: `list_plugins` and
`describe_plugin` show names a plugin declared, and nothing shows the store.

**The honest limit.** The agent's own shell runs as the same user as the service. "Agents
cannot see the store" holds inside Trestle. Outside it, it holds only when the host sandboxes
the agent's shell, or when the store needs an unlock the agent cannot perform: a biometric
prompt, or a Keychain ACL bound to the service binary. The ACL is weaker than it looks: a
Python interpreter's code signature covers every script it runs, so an ACL granted to the
interpreter is granted to any Python the agent starts. A separately signed service binary,
or 1Password's own prompt, is what makes the unlock the agent's problem rather than a
formality.

### (c) Resolution reuses the secrets path

Admission resolves each granted identity to its value(s) and puts them on the run's
`WorkOrder` secrets, the same place `secrets=` values go today. From there nothing changes:
`TRESTLE_RUN_SECRETS` carries them to the child, the child pops the variable before plugin
code is imported, and every write path scrubs the values. **The scrub pipeline is reused
unchanged.** A plugin that prints a token still produces `<redacted>` on disk and in every
answer.

What the plugin sees depends on the kind: an `aws_assume_role` identity is the three STS values
under their standard variable names; a `github_app` identity is `GITHUB_TOKEN`; a `keychain`
identity is whatever variable name its table declares; a `cli_passthrough` identity sets nothing.
The exact names are part of change 3 in the sequence, not of this record.

### (d) Delivery: on demand, not env injection

Env injection is the fallback, not the preference, because a value in the environment is
inherited by every subprocess and lives as long as the run. The preferred delivery is a
**helper the plugin's subprocess calls when it needs the value**, answered by the service only
for a live run that holds the grant:

- `trestle cred-helper aws` is an AWS `credential_process`: the run's `AWS_CONFIG_FILE` names
  a profile whose `credential_process` is this helper with a **run-scoped ticket**. The SDK
  calls it when it needs credentials, the helper presents the ticket to the service over
  loopback, and the service answers with a credential whose expiry is bounded as in (a). A
  ticket for a run that has ended, or for a plugin without the grant, is answered with nothing.
- `trestle cred-helper git` is a git credential helper configured the same way for the run, so
  `git push` by a `github_app` identity never sees a token in its environment.
- The ticket is random, run-scoped, and placed in the run's environment; it is not the
  credential and it buys nothing once the run is terminal.
- **Containers keep the existing run-scoped channel directory** with in-place refresh
  (`GrantDelivery`, `ChannelDelivery`): mounted read-only, refreshed atomically, gone with the
  run.

### (e) D-9 is unchanged

Real providers are **adapters behind the existing `GrantReads`, `GrantRefresh` and
`GrantDelivery` ports**. CI proves them against fakes: a fake STS endpoint, a fake GitHub App
endpoint, a stub keychain. **No real AWS user, role, key or credential is ever configured in
this repository**, and no test reaches a real identity provider. The example tables above use
placeholder account ids. What a real provider does with a real identity is unverified here, as
it is for the demo node today, and the adapters will say so in their own docstrings.

### (f) What the boundary is not

Plainly, because the rest of this record could be read as more than it is:

- **A plugin is trusted code running as the user.** A granted plugin can do with the value
  whatever the user could. Trestle scrubs what reaches disk and the agent; it does not stop the
  plugin from using the credential in any way the OS allows.
- **Trestle bounds the agent, not the plugin.** The identity model keeps values out of the
  agent's context and lets the operator say which plugins may use which identity. It is not a
  sandbox, and it does not make a plugin safe.
- **A full-profile agent that can `publish_plugin` can run arbitrary code as the user.** The
  digest pin means that code gets no credential grant, but it still runs as the user with
  everything in the user's environment. That is why grants require operator-published,
  digest-pinned plugins, and why the service's environment is exactly `PATH`, `HOME` and
  `TRESTLE_HOME`.
- **The operator should keep tokens out of the interactive shell environment entirely.** A
  token exported in `~/.zshrc` is in every terminal host's environment, and was in every stdio
  server those hosts spawned while that transport existed. The keychain, the SSO cache and the `[credentials]` tables are
  where identities live; the shell is not.

---

## Decision 3: MCP only, and a surface that is sufficient

Agents reach Trestle through the service's MCP endpoint and nothing else. The stdio transport
(`trestle serve --transport stdio`, the `command` form in an MCP host config) is removed. The
operator CLI stays for humans (`serve` is what the service unit runs; `service`, `init`,
`doctor`, `recover`, `pin`, `unpin`), and nothing agent-facing names it.

### Why

- Decision 1 already made the service the one process with a declared environment and a
  lifetime of its own. A second kind of server, process-local and dying with its host, is a
  second set of rules to write and test (the earlier draft had one: "a stdio server never
  brokers credentials"). Removing it leaves one.
- The one `command` config left on this machine is the repo's own `.cursor/mcp.json`; every host
  already uses the URL. The transport has no user.
- The bearer token (Decision 1) is only a boundary when every agent is behind it. With stdio
  gone, it is.

### What "MCP only" means, and does not

**Execution goes through Trestle. Reading does not.** `fetch` is handle-based by design and
stays so. An agent keeps its host's own file tools for reading source. MCP only means an agent
runs no command outside Trestle for work Trestle should do; it does not mean an agent reads
files through Trestle.

**The host, not Trestle, is the limit.** When the tools are missing or the service does not
answer, Trestle cannot answer either. That case is made rare by the service unit's `KeepAlive`
and made honest by the skill: the agent reports it and stops. The agent skill today says
"fall back to running the command by hand"; that line goes.

### The gap table

What pushes an agent to a shell is a situation with no run id and no MCP answer. These are the
situations, what the surface gives today, and what closes each one. Nothing here adds an MCP
tool: the surface stays at ten, and every addition is a query view, a field on an existing
answer, or a plugin.

| Situation with no run id | Today | Closes it |
|---|---|---|
| `admission.environment_busy` | "retry after it ends", naming nothing | `held_by` (the blocking run id) and `free_at` on the refusal, so the agent can `await_runs` it or send `after=`. |
| `admission.queue_full`, `admission.home_busy`, `admission.service_draining` | `retryable: true`, no timing | `retry_after_s` on every retryable refusal. Draining means the service is stopping; it comes back after `restart` or `upgrade`, not after `stop`. Wait `retry_after_s`; if it then does not answer, report it and stop. |
| `admission.not_allowlisted` | the message | `list_plugins` under the restricted profile marks each plugin `allowlisted`, so an agent stops trying the rest. |
| `admission.budget_does_not_fit`, `admission.ttl_out_of_range` | the message; the ceilings live in docs | the `status` view carries `deadline_ceiling_s` and `max_ttl_s`. |
| What only `trestle doctor` shows | CLI only | `query(view="status")`, no params: profile mode, plugin dirs and the plugins that failed to load, slots in use and free, the deadline ceiling, live servers, whether this one is draining, home format, version. Nothing about credentials (identities, grants, the store), though doctor shows them (Decision 2 wins). |
| An orphaned run from a dead session holds an environment | under the restricted profile no session may cancel it | a run whose session has ended is cancellable by any session of the same user (change 2a). |
| No plugin exists for a one-off command | the agent uses its shell | an operator-published `exec` plugin: argv list, no shell, cwd under declared roots, a bounded summary (exit code, tail), the full output as an artifact, and never a credential grant. |
| `admission.plugin_not_found`, `admission.import_failed`, `admission.invalid_args`, `admission.idempotency_key_conflict`, `admission.after_unknown` | covered | `list_plugins` (its `catalog_hint` and failed-plugin reasons), `describe_plugin`, `run_by_key`, `recent_runs`. |
| Tools missing, or the service not answering | host-side | not closable from inside Trestle; see above. |

**The `exec` plugin is a trade, taken knowingly.** It turns Trestle into a shell with a ledger
for the one-off case, which the bounded-answer design exists to avoid. Refusing it sends agents
back to their own shell, where there is no ledger, no scrub and no deadline. Shipping it,
allowlisted and with no credential grant ever, is the lesser cost. It is a plugin like any other:
under the restricted profile the operator lists it or not.

### What is proven, and what is not

None of this is implemented. The counts that shape the work are measured: 39 test files drive
the server through `tests/proof/mcp_host.py`, which spawns `trestle serve` over stdin and stdout,
so moving that one harness to streamable HTTP on `--port 0` (already supported; the server
prints the bound port) covers them; `tests/test_mcp_stdio_smoke.py` is the one test that is
about stdio itself. The refusal messages quoted in the table are the ones in
`trestle/server/admission.py` today.

## Implementation sequence

Each is its own change with its own tests and docs; none depends on a later one.

| # | Change | Done when |
|---|---|---|
| 1 | **Bearer token on HTTP MCP.** `agent.token` from `init` and `service install`; the HTTP transport refuses requests without it; `install.md` shows the `headers` form. | A request without the header is refused; the proof harness and every host config send it; the ops port is unchanged. |
| 1a | **Remove the stdio transport** (Decision 3). `serve` loses `--transport`; `tests/proof/mcp_host.py` drives the server over streamable HTTP on `--port 0` with the token; `test_mcp_stdio_smoke.py` goes; `.cursor/mcp.json` becomes a `url` entry; README, `install.md`, `security.md` and `overview.md` stop naming stdio. | No code path speaks stdio; every test that used the proof host passes over HTTP; no doc tells a host to spawn `trestle serve`. |
| 1b | **`ctx.exec(argv, ...)` and `ctx.remaining()`** (added 2026-10-19, pack review R1), without credentials: argv only, a per-call timeout defaulting to the time left, the whole process tree killed on timeout, output captured and bounded (each stream keeps its first `max_output_bytes`, so a truncated stream's "tail" is the end of the kept head) and the full kept output under one reused artifact name, the child's own environment inherited plus overrides. `credentials=` is reserved and refused until change 5. | A command that outlives its timeout is killed with its children and returns a timed-out result; 10 MB of output stays within the cap; a long poll does not use up the artifact count. |
| 2 | **`trestle service` commands** (`install`, `uninstall`, `status`, `start`, `stop`, `restart`, `upgrade`) generating the LaunchAgent plist / `systemd --user` unit as in Decision 1, and a rewrite of `install.md` and the README making the service the only mode (see "The CLI" in Decision 1). | A fresh machine reaches a running service from `install.md` alone; `status` reports it with both versions; `stop` drains and stays down; `upgrade` on a newer build restarts onto it, and refuses while a run is live unless `--drain`; the project's instructions never tell an agent to run `trestle`. |
| 2a | **Orphan visibility and cancel.** Record the end of a streamable-HTTP session; `trestle doctor` and `recent_runs` mark runs whose session is gone; a run whose session has ended is cancellable by any session of the same user, under either profile; `trestle cancel <run_id>` on the unscoped `Project.cancel` path; the dropped-`terminal`-wait test. | A run outlives its session untouched and is marked; another agent session or the operator can end it; `on_session_lost="cancel"` is possible to build on it (its own small change, after this one). |
| 2b | **The `status` view and the refusal fields** (Decision 3). `query(view="status")` with no params; `held_by` and `free_at` on `environment_busy`; `retry_after_s` on every retryable refusal; `allowlisted` on `list_plugins` under the restricted profile. | Every row of the gap table above that names a view or a field has it, and a test reads each one through MCP alone. |
| 2c | **The `exec` plugin** (Decision 3): a thin wrapper over `ctx.exec` (change 1b), not a second implementation; operator-published, argv list, no shell, cwd under declared roots, bounded summary, full output as an artifact, never a credential grant. | A one-off command runs through `run(plugin="exec")` with a bounded answer; a credential declaration on it is refused at publication. |
| 2d | **Skill and agent docs rewrite.** "Fall back to running the command by hand" becomes "report it and stop"; `agents.md` and the skills name the `status` view, the refusal fields and `exec`; agent-facing text never tells the agent to run a command that belongs to the service or its operator, in its shell, through `exec` or otherwise; such a command is quoted only as `the operator runs: <command>`. | An agent following the skill alone never runs a shell command for work Trestle does. |
| 3 | **`[credentials.<name>]` tables, grants by digest, admission refusals, `@trestle(credentials=...)`, and the first Context shapes.** Resolution onto the `WorkOrder` secrets path; `ctx.credential_env` and `ctx.credential_file` (the channel directory, moved up from the pack); `secrets=` marked deprecated. One store backend is enough to prove the path: the encrypted `0600` file, with a stub keychain in tests. | `credential_not_granted` and `credential_unknown` are refused before a run id; a republished plugin loses its grant; the scrub covers values from both shapes. |
| 4 | **Store backends and native providers behind the grant ports, with fakes.** macOS Keychain (`security`), 1Password (`op`), `aws_assume_role` against a fake STS, `github_app` against a fake App endpoint, `cli_passthrough`; `credential_interactive` and lifetime handling through the existing codes. | Each backend is proven in CI against its fake or stub; nothing real is configured. |
| 5 | **`ctx.exec(credentials=...)` and `trestle cred-helper`** (`aws` credential_process, `git` credential helper) with run-scoped tickets answered only for a live run holding the grant; `ctx.exec` itself exists from change 1b, and this change lifts its `credentials=` refusal; `ctx.exec(argv, credentials=[...])` as the documented first choice; `credential_env` documented as the fallback. | A helper call after the run is terminal gets nothing; a plugin without the grant gets nothing; `ctx.exec` runs a tool with a granted identity while the plugin holds no value; the container channel is unchanged. |

### As implemented (changes 1, 1a, 2)

Where the code differs from the text above, and why:

- **A drained server exits.** SIGTERM drains as before (new runs refused, admitted runs started
  and finished), and the server now exits 0 once it owns no live run, where it used to wait to be
  killed. That is what lets a service manager's stop, and `trestle service stop`, wait out the
  runs instead of interrupting them.
- **`KeepAlive = {SuccessfulExit = false}` and `Restart=on-failure`**, not a bare `KeepAlive` and
  `Restart=always`: a crash restarts the service, a drained exit 0 does not, so `stop` cannot race
  the manager bringing it back. `KillMode=mixed` and `TimeoutStopSec=infinity` keep a systemd stop
  from signalling the runs' processes or cutting the drain short.
- **`stop` also disables the unit** (`launchctl disable`, `systemctl --user disable`), so a stopped
  service stays down across logins until `start`, which enables it again.
- **`upgrade` stops first.** The order is stop (draining), pip, the home migration run by the *new*
  binary (`init --upgrade --no-seed`), start. Installing while the old build runs would replace
  modules under a live process, and `init --upgrade` wants the home's servers stopped. A failed
  pip starts the old build again. The refusal while a run is live is checked before anything
  changes.
- **`PATH`** in the unit is the installing shell's, with the binary's directory first and
  `/usr/bin:/bin:/usr/sbin:/sbin` appended: the server calls `sysctl` and `ps` by name, and a
  minimal `PATH` without `/usr/sbin` broke process attribution in testing.
- **The token** is also created by the server's first start on a home without one, as a fresh
  home is given its format there; `init` and `service install` create it too.
- **Build identity.** `trestle --version` prints the build: the version, plus `+g<commit>` (and
  `.dirty`) for an editable checkout, so a `git pull` shows as a stale service even when the
  version string has not moved. Each server records its build in its lock file.
- **The proof host** (`tests/proof/mcp_host.py`) drives the server over HTTP with the token. Its
  `stdin_close` sever is now `session_close` (HTTP DELETE of the MCP session): the server stays up
  and the run continues, which is the session-loss policy above. The three stdio smoke tests moved
  into `tests/test_mcp_http_smoke.py`; the S0 node-id pin records the move.

### As implemented (change 3)

Where the code differs from the text above, or settles what it left open, and why:

- **The tables live in `$TRESTLE_HOME/credentials.toml`, not `config.toml`.** The CLI writes them,
  and the standard library reads TOML but cannot write it back with the operator's comments and
  layout, so a CLI that edited `config.toml` would destroy the operator's file. A separate file
  that only the CLI writes (mode `0600`, with a header saying so) has no such problem. It is read
  once at server start like `config.toml`, so nothing an agent sends can change it, and a
  malformed file stops the start the same way. It also holds `[store] key_provider`.
- **One kind: `encrypted_file`.** A table names `kind = "encrypted_file"` and `env = "VAR"`, plus
  `grants`. Any other kind from (a) is refused at load with a message saying it is not
  implemented yet (change 4); an unknown kind is refused as unknown.
- **Variable names (left open above).** An `encrypted_file` identity's table declares its one
  variable name (`env`); `ctx.credential_env(name)` returns `{env: value}`. It is returned to the
  plugin and never put into the process environment.
- **Grants are written in the pinned form only.** `grant` (and `add --grant`) writes
  `{ plugin, source_sha256, package_digests }`. A bare string in a hand-edited file is read as an
  unpinned grant that covers nothing until `grant` pins it.
- **A third refusal, `admission.credential_unavailable`.** A granted identity whose value the
  store cannot give (no stored value, the store or its key unreadable, a wrong key) is refused
  before a run id with this code. Folding it into `credential_unknown` would tell the operator to
  add an identity that exists.
- **Refusal text names the identity and the operator's command, nothing else.** Every credential
  refusal reads `the operator runs: trestle ...; credential '<name>' ...`: the instruction first,
  so the 200-character cut can never drop it. It never says where the store or its key is (the
  Keychain service and account, a file path): that goes to the operator's `trestle doctor` and to
  `service.log`, never to an answer an agent reads.
- **Where admission checks.** The grant check (`credential_unknown`, `credential_not_granted`)
  runs right after the plugin and version are found, before import and argument validation: it
  reads only the tables. The store is read after the plan is carved and before the admission lock,
  so every cheaper refusal comes first; the values are read at each admission (one decryption), so
  a value rotated with `add --replace` applies to the next run without a restart.
- **Resolution reuses the scrub, not the environment variable (a change to (c)).** The
  `TRESTLE_RUN_SECRETS` environment variable is gone. A process's initial environment is readable
  by any process of the same user (`ps -E` on macOS, `/proc/<pid>/environ` on Linux), and popping
  a variable from `os.environ` does not remove it from the kernel's copy, so a value in the
  environment was visible for the whole run. The `WorkOrder` now carries the granted identities
  (`credentials`: name to env name and value) in memory, and the conductor hands them to the
  wrapper, and the wrapper to the child, over an inherited pipe: the environment carries only the
  pipe's descriptor number (`TRESTLE_RUN_SECRETS_FD`), the sender writes the values and closes
  it, and the receiver reads to EOF and closes it before any plugin code is imported. No value is
  in any argv or environment Trestle creates. The scrub is unchanged: the values are each process's
  scrub set, as the `secrets=` values were. A run with a credential still records
  `has_secrets: true`, so a reaped run's outputs are not promoted. A test reads the child's and
  the wrapper's environments the way another process would and finds no value. (A plugin that
  passes `credential_env`'s result to `ctx.exec(env=...)` puts the value in that command's
  environment, visible the same way; the docs say so and prefer `credential_file`.)
- **`secrets=` is removed, not deprecated (a change to Decision 2).** Trestle has no consumers
  outside this repository, and every in-repo use was a test of the mechanism itself, each now
  proved over a credential identity. Keeping it would have kept a second path whose whole flaw is
  that the agent holds the value, plus argument redaction in `spec.json` and an argument-restore
  step in the child. `@trestle(secrets=...)` is refused at publication with a message pointing at
  `credentials`; the manifest's `declared` no longer has a `secrets` slot, so every snapshot id
  changed once.
- **The channel directory is the run's own, not the pack's.** `ctx.credential_file(name)` writes
  `<run dir>/channel/<name>` (directory `0700`, file `0600`, replaced whole by an atomic rename) on
  first use. The child removes the directory when the plugin returns, the conductor removes it
  again at finalization, and the reaper removes it from a dead owner's run. The grant pack's
  `ChannelDelivery` (containers, refresh in place) is unchanged and stays the demo; an
  `encrypted_file` value is static, so nothing refreshes it.
- **A name not declared** raises `LookupError` from either shape, naming the declared ones.
- **The publication origin is recorded in the manifest.** `manifest.json` carries `origin`:
  `agent` for a source written by `publish_plugin`, `operator` for anything else in a watched
  folder. The agent path records each source it writes (plugin name and `source_sha256`, under
  `$TRESTLE_HOME/publications/`); a snapshot whose source matches that record is `agent`. `grant`
  refuses an `agent` plugin. The same bytes stay `agent` even if the operator copies them; an
  operator's own edit (any change to the bytes) makes the file the operator's. The origin is not
  part of the snapshot identity. As the honest limit says, an agent with a shell can still write a
  file into a watched folder itself; the record covers the `publish_plugin` path, which is the one
  inside Trestle.
- **The declaration** `credentials=[...]` takes a list (or tuple) of name literals, each lower case
  letters, digits, `-` and `_`, none twice. It is recorded sorted in the manifest's `declared`
  only when present, so the snapshot identity of every plugin that declares none is unchanged.
- **`describe_plugin`** carries `credentials` (the declared names) only when some are declared.
- **The store.** Fernet (AES-128-CBC with an HMAC-SHA256 tag, from `cryptography`, now a declared
  dependency) over one JSON document of all values, in `$TRESTLE_HOME/credentials/store.enc`
  (`0600`, directory `0700`), replaced whole on every write. The key comes from a key provider:
  `keychain` (the default on macOS: a generic password, service `trestle-credentials`, account
  the home's absolute path, through `/usr/bin/security`, created by the first `add`) or `file`
  (the default elsewhere: `credentials/store.key`, `0600`). The file provider is weaker and the
  docs say so: anything that reads the home as the user reads both files. `security
  add-generic-password` takes the password as an argument, so the store's key (never a credential
  value) is in that one command's argv while it runs; `security` has no non-interactive stdin form.
  The item is created without a `-T` list of trusted applications, so any process of the user's
  can read it through `/usr/bin/security` without a prompt: the same exposure as the key file. The
  Keychain keeps the key out of the home and its backups; the boundary is the OS user, as "the
  honest limit" above says.
  The first `add` fixes the provider in `credentials.toml` (`--key-provider` chooses it then).
  Tests use a stub `security` and never the real Keychain.
- **The CLI.** `trestle credentials add NAME --env VAR [--grant PLUGIN ...] [--replace]
  [--key-provider keychain|file]`, `remove`, `list`, `grant`, `revoke`, each with `--home`, and
  `--restart` on those that change a table. The value is read with `getpass` from a terminal or
  from stdin when piped; there is no argument for it. Every grant named on `add` is checked before
  anything is stored. Commands that change `credentials.toml` end by naming `trestle service
  restart`; a value-only rotation says it applies to the next run. The commands take a lock in the
  store directory, so two of them never interleave. `grant` and `list` refresh a registry over the
  home's watched folders to read each plugin's current digests, as a server start would.
- **`trestle doctor`** shows the identities, each grant's status (`current`, `stale`, `plugin
  missing`, `unpinned`) with a warning for each that is not current, and whether the store and its
  key read; an unreadable store makes health `degraded`.
- **`FakeContext(..., credentials={"gh": {"GH_TOKEN": "fake"}})`** gives authors both shapes with
  fake values; `ctx.exec(credentials=...)` stays refused in both contexts until change 5.

### As implemented (changes 2a to 2d)

Design: [`proposals/2026-10-22-agent-surface-2a-2d-design.md`](../proposals/2026-10-22-agent-surface-2a-2d-design.md),
its owner decisions, and its "Record amendments" for the deviations below. Released as 0.5.1.

- **The session watch is Trestle's own ASGI middleware** (`SessionWatch` in
  `trestle/server/mcp_sessions.py`, innermost behind the bearer token). fastmcp 3.4.7 and mcp
  1.30.0 give no hook for a session's end: every path ends in the SDK's private
  `_discard_session`, which Trestle does not patch (a rename would silently disable the
  feature). The middleware mirrors the SDK's idle rule with its public constants
  (`DEFAULT_SESSION_IDLE_TIMEOUT`, `MCP_SESSION_ID_HEADER`) plus a 15 s guard, marks a DELETE
  answered 2xx before the answer is forwarded, and a 404 at once. A pin test fails if fastmcp
  starts passing its own idle timeout or the SDK changes its default.
- **`servers/<server_id>.sessions.json`**, written whole by the server that opened the
  sessions, holds ended session ids only (`{at, reason}`), each kept while an owned live run
  names it and dropped 60 s after its end otherwise; the file goes when empty, with the server's
  lock file (`ServerLock.close`, `live_servers(prune=True)`). No ledger row, nothing written at
  restart. A run's `caller_session` is copied into `evidence/state.json`, never onto the wire.
- **`session_ended`** is true or false for a live run sent over MCP and null otherwise, on the
  `recent_runs` and `run` rows; `runs.session_ended` counts them in `status`; `trestle doctor`
  lists them (`session_ended_runs`, at most 20 lines). Where the reader has the run's directory
  (every reader that ships), whether its server runs is the run's own owner lock, the lock the
  reaper reads; otherwise the owner's server lock.
- **One predicate, `Project._in_scope`,** decides restricted cancel and child-view reads: the
  session that received the run, or any session once the run's own session has ended. A run
  sent outside MCP is never in an MCP session's scope; the full profile and the operator are
  unscoped. The refusal stays `projection.not_owner`, now "run not received in this session, and
  its session has not ended".
- **`trestle cancel <run_id>`** exits 0 accepted (and says so when the run's server is not
  running), 1 refused (unknown, ended, a child handle naming its root), 2 a refused home.
- **`held_by` and `free_at`** on `admission.environment_busy`: `held_by` is the run whose recorded
  deadline frees the environment last (`pool.busy_holder`, ties to the latest arrival; it
  replaced `busy_until`), `free_at` that deadline in ISO 8601 UTC.
- **`retry_after_s`** is on every `retryable: true` outcome, filled in `RequestOutcome.to_dict`
  from `codes.RETRY_AFTER_S` (`queue_full` 5, `home_busy` 2, `service_draining` 10,
  `not_finalized` 1, `cursor_expired` 0, `registry_conflict` 0, default 5);
  `environment_busy` computes its own (at least 1). The credential refusals are not retryable
  and carry none of the three fields.
- **`query(view="status")`**: one row (version, profile, draining, home format, live server
  count, limits, slots, live runs by state, up to 8 environments with `held_by`/`free_at`, plugin
  counts, up to 4 missing plugin folders), within 4 KiB; answered by `ControlSurface` under both
  profiles. Nothing about credentials, server ids, pids, ports, the admission lock, storage,
  the allowlist or session ids.
- **`allowlisted`** on each `list_plugins` item under the restricted profile only.
- **`exec`** is the guide example `trestle://guide/examples/exec`, seeded by `trestle init` when
  missing (never by an upgrade). `cwd` must resolve under `[plugins.env] TRESTLE_EXEC_ROOTS`
  (absolute, joined by `:`); unset roots answer `blocked` (`plugin.exec_roots_unset`). Its
  timeout is the run's deadline less 5 s; each stream keeps its first 16 MiB; the answer carries
  two 1,500-byte tails and the output artifact; errors are returned (`ok: false`, `error`).
  `argv[0]` named `trestle` answers `operator_command`, a steer and not a boundary.
- **`exec` is off under the restricted profile unless listed**, and listing it on a home with a
  credential identity is warned about (doctor, `trestle credentials add` and `grant`), never
  refused.
- **`exec` never carries a credential**: a declaration on a plugin named `exec` is refused at
  publication (`publication.validation_failed`), and `trestle credentials grant`/`add --grant`
  refuse it before anything is written.
- **Operator commands in agent-facing text** appear only as `the operator runs: <command>`; the
  credential refusals keep that clause whole under the 200-character cut, and
  `credential_unavailable` no longer shows the store's detail. A doc test and a message test
  enforce it.
- **`on_session_lost` is not in 0.5.1.** Its seam is `McpSessions.end(sid, reason)`, called
  once per ended session.

## What this record does not decide

- Variable names each kind sets through `ctx.credential_env` (change 3 settled them for
  `encrypted_file`: the table's `env`; the other kinds' names are change 4's).
- Whether the service should refuse to start when a second server is live on the home
  (today it must not; v0.3.1 sharing stays).
- A Windows service. With stdio gone there is no Windows path until a service exists there;
  this record does not design one.
- The session idle timeout (the SDK's 30 minutes is in force; settle it with `on_session_lost`).
- A head-and-tail capture for `ctx.exec` (today each stream keeps its first bytes).
- An unlock agents cannot perform: a separately signed service binary named by the Keychain ACL,
  and handing run secrets to the child by something other than the environment; until both
  exist, any process of the user's can reach the values.
