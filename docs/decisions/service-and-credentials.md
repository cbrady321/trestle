# Decision record: one service per user, and credentials by name

**Status:** decided, not implemented. Nothing in this record exists in code yet except where it
says so ("already implemented"). The implementation sequence at the end says what lands first.
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
   it.
2. **An operator cancel.** The unscoped path exists: `Project.cancel` with `caller_session`
   None is the operator's CLI or console and is not scoped. No CLI command exposes it today
   (`trestle/cli.py` has `serve`, `ops serve`, `init`, `doctor`, `recover`, `pin` and `unpin`,
   and `trestle ops serve` is read-only). Add `trestle cancel <run_id>`.
3. **`on_session_lost`**, an optional per-plugin declaration with the values `"continue"`
   (the default, and everything above) and `"cancel"`, for a plugin whose run is worthless
   without its caller: one holding an environment lease for an interactive session, for
   instance. It is a literal in the decorator like every other keyword, recorded in the
   manifest, and it needs gap 1 to act on. `describe_plugin` shows it.

**Unverified.** What the server does when a client drops a `run(completion="terminal")` wait
mid-run. The wait is a projection (`await_terminal_async` polls the ledger and holds nothing
the run needs), so the run should be untouched; a test must show it before the service mode
is documented as recommended.

**The reverse case is already covered.** When the service dies and agents are alive: its runs
end `interrupted` at the next start, a `repeatable=True` key re-sent by the agent starts a
fresh run (`retry_of` names the interrupted one), a SIGTERM stop drains first, and under the
service mode launchd `KeepAlive` (or `Restart=always`) starts it again.

### The CLI

```
trestle service install     # write and load the LaunchAgent plist / systemd --user unit
trestle service uninstall   # unload and remove it
trestle service status      # loaded, running, pid, endpoint, home
trestle service restart     # SIGTERM drain, then start
```

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
- Stdio is unchanged: the spawning host is the only client and there is nothing to authenticate.
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

**Grants are bound to the plugin's recorded source digest.** `grants` names plugins, and a
grant is recorded against the plugin's `source_sha256` (the snapshot identity already recorded
at publication, see [`plugins.md`](../plugins.md)). A republished plugin of the same name has a
new digest and **loses the grant** until the operator re-pins it: `trestle credentials grant
<name> <plugin>` records the current digest, and `config.toml` carries it as
`grants = [{ plugin = "repo_tests", source_sha256 = "..." }]` (the bare-string form above is
sugar that `grant` rewrites). **This settles open question F-13(d) for credentials:
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
| `admission.queue_full`, `admission.home_busy`, `admission.service_draining` | `retryable: true`, no timing | `retry_after_s` on every retryable refusal. Under one service, draining means a restart is coming and "wait" is the whole answer. |
| `admission.not_allowlisted` | the message | `list_plugins` under the restricted profile marks each plugin `allowlisted`, so an agent stops trying the rest. |
| `admission.budget_does_not_fit`, `admission.ttl_out_of_range` | the message; the ceilings live in docs | the `status` view carries `deadline_ceiling_s` and `max_ttl_s`. |
| What only `trestle doctor` shows | CLI only | `query(view="status")`, no params: profile mode, plugin dirs and the plugins that failed to load, slots in use and free, the deadline ceiling, live servers, whether this one is draining, home format, version. |
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
| 2 | **`trestle service` commands** (`install`, `uninstall`, `status`, `restart`) generating the LaunchAgent plist / `systemd --user` unit as in Decision 1, and a rewrite of `install.md` making the service the only mode. | A fresh machine reaches a running service from `install.md` alone; `status` reports it; `restart` drains. |
| 2a | **Orphan visibility and cancel.** Record the end of a streamable-HTTP session; `trestle doctor` and `recent_runs` mark runs whose session is gone; a run whose session has ended is cancellable by any session of the same user, under either profile; `trestle cancel <run_id>` on the unscoped `Project.cancel` path; the dropped-`terminal`-wait test. | A run outlives its session untouched and is marked; another agent session or the operator can end it; `on_session_lost="cancel"` is possible to build on it (its own small change, after this one). |
| 2b | **The `status` view and the refusal fields** (Decision 3). `query(view="status")` with no params; `held_by` and `free_at` on `environment_busy`; `retry_after_s` on every retryable refusal; `allowlisted` on `list_plugins` under the restricted profile. | Every row of the gap table above that names a view or a field has it, and a test reads each one through MCP alone. |
| 2c | **The `exec` plugin** (Decision 3): operator-published, argv list, no shell, cwd under declared roots, bounded summary, full output as an artifact, never a credential grant. | A one-off command runs through `run(plugin="exec")` with a bounded answer; a credential declaration on it is refused at publication. |
| 2d | **Skill and agent docs rewrite.** "Fall back to running the command by hand" becomes "report it and stop"; `agents.md` and the skills name the `status` view, the refusal fields and `exec`; nothing agent-facing mentions the CLI. | An agent following the skill alone never runs a shell command for work Trestle does. |
| 3 | **`[credentials.<name>]` tables, grants by digest, admission refusals, `@trestle(credentials=...)`, and the first Context shapes.** Resolution onto the `WorkOrder` secrets path; `ctx.credential_env` and `ctx.credential_file` (the channel directory, moved up from the pack); `secrets=` marked deprecated. One store backend is enough to prove the path: the encrypted `0600` file, with a stub keychain in tests. | `credential_not_granted` and `credential_unknown` are refused before a run id; a republished plugin loses its grant; the scrub covers values from both shapes. |
| 4 | **Store backends and native providers behind the grant ports, with fakes.** macOS Keychain (`security`), 1Password (`op`), `aws_assume_role` against a fake STS, `github_app` against a fake App endpoint, `cli_passthrough`; `credential_interactive` and lifetime handling through the existing codes. | Each backend is proven in CI against its fake or stub; nothing real is configured. |
| 5 | **`ctx.exec` and `trestle cred-helper`** (`aws` credential_process, `git` credential helper) with run-scoped tickets answered only for a live run holding the grant; `ctx.exec(argv, credentials=[...])` as the documented first choice; `credential_env` documented as the fallback. | A helper call after the run is terminal gets nothing; a plugin without the grant gets nothing; `ctx.exec` runs a tool with a granted identity while the plugin holds no value; the container channel is unchanged. |

## What this record does not decide

- Variable names each kind sets through `ctx.credential_env` (change 3).
- Whether the service should refuse to start when a second server is live on the home
  (today it must not; v0.3.1 sharing stays).
- A Windows service. With stdio gone there is no Windows path until a service exists there;
  this record does not design one.
- Whether the `exec` plugin is allowlisted by default under the restricted profile, or only
  when the operator lists it.
