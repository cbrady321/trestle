# Decision record: one service per user, and credentials by name

**Status:** decided, not implemented. Nothing in this record exists in code yet except where it
says so ("already implemented"). The implementation sequence at the end says what lands first.
**Date:** 2026-10-19
**Related:** [`security.md`](../security.md) (lockdown, profiles, what the boundary is not),
[`install.md`](../install.md) (host wiring), [`environment.md`](../environment.md) (the demo
credential node and D-9), [`plugins.md`](../plugins.md) (`@trestle(secrets=...)`, snapshot
identity).

Two decisions, taken together because the second depends on the first: Trestle runs as one
per-user service per machine, and that service is the only thing that ever holds a credential.
Agents name identities; they never see values.

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

- **Stdio stays as the fallback** for a machine without the service. It behaves as it does
  today: process-local, dies with its host, and its runs end `interrupted` when it does. A
  **stdio server never brokers credentials**: a plugin that declares a credential identity
  (Decision 2) is refused admission on a stdio server, so the only process that ever holds a
  value is the one with a declared environment and a lifetime of its own.
- **Many servers on one home** (v0.3.1) keeps working. The service is the recommended way to
  run one; it is not a lock against running others.
- **SIGTERM drain** is already implemented: a server stopped with SIGTERM refuses new runs and
  finishes the runs it admitted (see [`install.md`](../install.md)). The service unit uses it.

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
so and tells you to prefer stdio. Once the service holds credentials that is no longer
acceptable: "any local process" includes every program the user ever ran, and the MCP surface
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

**Kinds.** Each is an adapter behind the existing grant ports (see (e)).

| `kind` | What it resolves | Where the value lives |
|---|---|---|
| `keychain` | a static secret read from the OS keychain (macOS Keychain, Secret Service on Linux) by item name | in the keychain; in memory on the `WorkOrder` for the run |
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
- A stdio server refuses any plugin that declares a credential, `admission.credential_not_granted`
  with a message saying the server is not the service (Decision 1).
- A provider that needs a human (`aws sso login`, `gh auth login`, an MFA prompt) ends the run
  **`blocked` with the existing `execution.credential_interactive`**, naming the identity and
  the one action that unblocks it. The run never waits on a prompt nobody will answer.
- `execution.credential_lifetime_insufficient` and `execution.credential_stale` keep their
  meanings from the grant pack.

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
  token exported in `~/.zshrc` is in every terminal host's environment and in every stdio
  server those hosts spawn. The keychain, the SSO cache and the `[credentials]` tables are
  where identities live; the shell is not.

---

## Implementation sequence

Each is its own change with its own tests and docs; none depends on a later one.

| # | Change | Done when |
|---|---|---|
| 1 | **Bearer token on HTTP MCP.** `agent.token` from `init` and `service install`; the HTTP transport refuses requests without it; `install.md` shows the `headers` form. | A request without the header is refused; stdio is unchanged; the ops port is unchanged. |
| 2 | **`trestle service` commands** (`install`, `uninstall`, `status`, `restart`) generating the LaunchAgent plist / `systemd --user` unit as in Decision 1, and a rewrite of `install.md` making the service the recommended mode with stdio as the fallback. | A fresh machine reaches a running service from `install.md` alone; `status` reports it; `restart` drains. |
| 3 | **`[credentials.<name>]` tables, grants by digest, admission refusals, `@trestle(credentials=...)`.** Resolution onto the `WorkOrder` secrets path; `secrets=` marked deprecated. No real provider yet: the `keychain` kind against a stub keychain is enough to prove the path. | `credential_not_granted` and `credential_unknown` are refused before a run id; a republished plugin loses its grant; a stdio server refuses a declaring plugin; the scrub covers resolved values. |
| 4 | **Providers behind the grant ports with fakes.** `aws_assume_role` against a fake STS, `github_app` against a fake App endpoint, `cli_passthrough`; `credential_interactive` and lifetime handling through the existing codes. | Each adapter is proven in CI against its fake; nothing real is configured. |
| 5 | **`trestle cred-helper`** (`aws` credential_process, `git` credential helper) with run-scoped tickets answered only for a live run holding the grant; env injection becomes the fallback. | A helper call after the run is terminal gets nothing; a plugin without the grant gets nothing; the container channel is unchanged. |

## What this record does not decide

- Variable names each kind sets in the child (change 3).
- Whether the service should refuse to start when a second server is live on the home
  (today it must not; v0.3.1 sharing stays).
- A Windows service. Stdio remains the Windows path until someone needs more.
