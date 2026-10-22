# Install: one Trestle service per user

Trestle runs as **one service per OS user per machine**: a macOS LaunchAgent or a `systemd --user`
unit, never a root daemon and never a process per MCP host. Every agent host on the machine
(Cursor, Claude Desktop, Claude Code, a script) points at the service's loopback URL. Runs belong to
the service, so they finish when the chat window that sent them closes. Why, and what is still to
come: [`decisions/service-and-credentials.md`](decisions/service-and-credentials.md).

`trestle service` is the only documented way to start and stop Trestle.

## 1. Install and start the service

**One command, install or upgrade.** From a clone of this repository:

```bash
scripts/install.sh            # the newest release; scripts/install.sh v0.4.0 for a given one
```

A first run checks out the release, creates `~/.trestle-venv`, installs Trestle into it, creates
the home and installs and starts the service. Run again after a new release: it stops the service
(draining), checks out the release and runs `trestle service upgrade`. It refuses while a run is
live unless given `--drain`, and refuses a checkout with uncommitted changes. `--no-packs` skips
the packs; `--venv DIR` picks another venv. The steps it runs are below.

By hand, from the Trestle checkout:

```bash
python3 -m venv ~/.trestle-venv
~/.trestle-venv/bin/pip install -e ".[packs]"    # packs optional: Docker / pytest / migration plugins
~/.trestle-venv/bin/trestle init                 # creates ~/.trestle, seeds echo.py, writes agent.token
~/.trestle-venv/bin/trestle service install      # writes and loads the unit; the service starts
```

Run `trestle service install` from the venv you want the service to run: the unit names that
`trestle` binary by its absolute path. Then check it:

```bash
~/.trestle-venv/bin/trestle service status
```

```
installed: yes
loaded: yes
running: yes
pid: 41207
endpoint: http://127.0.0.1:18732/mcp
home: /Users/you/.trestle
installed build: 0.3.1+g9b89c9aa1234
running build: 0.3.1+g9b89c9aa1234
live runs: 0
```

`status` exits 0 when the service is running and 3 when it is not.

What `install` writes, for the current user only:

| Platform | Unit |
|---|---|
| macOS | `~/Library/LaunchAgents/dev.trestle.service.plist` |
| Linux | `~/.config/systemd/user/trestle.service` |

- The command is `<absolute path of trestle> serve --port 18732` (`--port` on `install` changes it).
- The environment is **exactly** `PATH`, `HOME` and `TRESTLE_HOME`. `PATH` is the installing
  shell's, with the binary's directory first and `/usr/bin:/bin:/usr/sbin:/sbin` last. Nothing
  else from your shell reaches the service or its plugins: declare what plugins need in
  `config.toml`'s `[plugins.env]` table, never in `~/.zshrc`:

  ```toml
  [plugins.env]
  # only what your plugins read: e.g. the env pack finds `docker` on the service's PATH unless
  # TRESTLE_DOCKER_PATH names it, which you need when docker lives outside that PATH
  TRESTLE_ENV_COMPOSE_FILE = "/srv/app/compose.yaml"
  ```

  Every plugin run gets these variables over the service's own environment. Only `config.toml`
  sets them, never a request; the table is read at each run's start, so an edit applies to the
  next run without a restart. Values are strings; a malformed table, or one that sets
  `TRESTLE_HOME` or `TRESTLE_RUN_SECRETS`, stops the server from loading its config. Put no
  secret here: a value in this table is not scrubbed from a run's output (declare secrets with
  `@trestle(secrets=...)`).
- It starts at login (`RunAtLoad`, `WantedBy=default.target`) and is restarted after a crash
  (`KeepAlive = {SuccessfulExit = false}`, `Restart=on-failure`). A clean exit is not restarted:
  that is how a drained stop stays stopped.
- Output goes to `$TRESTLE_HOME/service.log`.

The unit is a file you can read and edit; it carries a line saying `trestle service install` wrote
it. `install` refuses to overwrite a unit without that line. Run again on an installed service,
`install` reports the state and changes nothing; run after a newer `trestle` was installed into the
venv, it restarts the service onto it, as `upgrade` does.

Keep the home private: `chmod 700 ~/.trestle`.

## 2. Wire every agent host

The endpoint is `http://127.0.0.1:18732/mcp`, and every request must carry the **agent token**:
`Authorization: Bearer <contents of ~/.trestle/agent.token>`. A request without it is answered
401. The token file is mode `0600`; possessing it is what lets a process use Trestle. See
[`security.md`](security.md).

Hosts send the token through their config's `headers`, either expanded from an environment variable
or pasted. To use the variable form, export it where the host is started:

```bash
export TRESTLE_AGENT_TOKEN="$(cat ~/.trestle/agent.token)"
```

### Claude Code

```bash
claude mcp add --scope user --transport http trestle http://127.0.0.1:18732/mcp \
  --header "Authorization: Bearer $(cat ~/.trestle/agent.token)"
```

Or in a project `.mcp.json` (Claude Code expands `${VAR}`):

```json
{
  "mcpServers": {
    "trestle": {
      "type": "http",
      "url": "http://127.0.0.1:18732/mcp",
      "headers": { "Authorization": "Bearer ${TRESTLE_AGENT_TOKEN}" }
    }
  }
}
```

### Cursor

User config `~/.cursor/mcp.json` (all workspaces), or the project override this repo ships,
[`.cursor/mcp.json`](../.cursor/mcp.json). Cursor expands `${env:VAR}`:

```json
{
  "mcpServers": {
    "trestle": {
      "url": "http://127.0.0.1:18732/mcp",
      "headers": { "Authorization": "Bearer ${env:TRESTLE_AGENT_TOKEN}" }
    }
  }
}
```

**Skill (optional):** copy [`skills/trestle/`](../skills/trestle/) to
`~/.cursor/skills/trestle/` (or `~/.claude/skills/trestle/` for Claude Code) so agents in every
workspace use Trestle well: one call per objective, chained runs, bounded retrieval.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`: merge the same `url` and
`headers` entry as Claude Code's `.mcp.json`, with the token pasted in place of the variable.
Restart Claude Desktop. (Unverified here: whether your Desktop version accepts a `url` entry in
this file. If it does not, it cannot reach Trestle until it does: there is no `command` form.)

### Keep agents out of the checkout and the home

Agents reach Trestle only over MCP. They learn to write plugins from the server's resources
(all under `trestle://guide`, read one small resource at a time), not from
Trestle's source or the home's files, and they extend it by writing plugins and packs, never by
editing Trestle. Hold them to that in the host's settings by denying its file tools read and
write access to the Trestle checkout and to `~/.trestle`. The host itself still reads the token
for its MCP config. For Claude Code, in `~/.claude/settings.json` (absolute paths start with `//`):

```json
{
  "permissions": {
    "deny": [
      "Read(~/.trestle/**)",
      "Edit(~/.trestle/**)",
      "Read(//ABS/PATH/TO/trestle/**)",
      "Edit(//ABS/PATH/TO/trestle/**)"
    ]
  }
}
```

In Cursor, list the same two folders in its ignore settings: the global ignore list, or a
`.cursorignore` in each workspace. These rules cover the host's own file tools, not a shell the agent runs; deny the agent's
shell access to those folders too, or deny its shell. Leave the rules out where you work on
Trestle itself.

### Check

- The host lists **ten** tools: `run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`, `unpin`,
  `list_plugins`, `describe_plugin`, `publish_plugin`.
- `list_plugins` returns at least `echo`.
- `run(plugin="echo", args={"message": "hi"}, wait_ms=5000)` returns a terminal `RunView`, and with
  `completion="terminal"` it can never return a `running` frame.

The ops port (`trestle ops serve`, `127.0.0.1:18733`) is read-only and takes no token.

## 3. Plugins the service watches

The service runs with no `--plugin-dir`. Its watched folders come from `$TRESTLE_HOME/config.toml`
(default: `$TRESTLE_HOME/plugins`):

```toml
[plugins]
paths = ["~/.trestle/plugins", "/ABS/PATH/TO/trestle/examples/packs", "/ABS/PATH/TO/project/tools"]
```

`config.toml` is read once at start: `trestle service restart` after changing it (the
`[plugins.env]` table alone is read again at each run's start). A pack's wrappers are installed
this way too: see [`packs.md`](packs.md#install).

## 4. Start, stop, restart

```bash
trestle service stop       # SIGTERM: drain, then unload; stays down (across logins too) until start
trestle service start      # load the unit if needed and start it
trestle service restart    # stop (draining), then start
```

A stopped-by-SIGTERM server **drains**: it refuses new runs (`admission.service_draining`), starts
and finishes the runs it already admitted, and exits once it owns none. `stop` waits for that, so
it can take as long as the longest live run (each is bounded by its deadline). Each command reports
in one line.

## 5. Upgrade

`scripts/update.sh` fetches the newest release from GitHub and runs that release's own
`scripts/install.sh`, so a release can change how it is installed. It takes `--release vX.Y.Z`,
`--dir DIR` (the checkout; cloned there when missing, default `~/trestle` outside a checkout) and
passes `--drain`, `--no-packs` and `--venv` through. `scripts/install.sh` alone does the same from
the checkout as it is. By hand:

```bash
git -C /ABS/PATH/TO/trestle pull      # an editable install: the checkout's new commit
trestle service upgrade               # refuses while a run is live
trestle service upgrade --drain       # waits for live runs to end instead
```

`upgrade` stops the service (draining), reinstalls into the service's venv (`pip install -e` of the
checkout for an editable install, else `pip install -U` of where it was installed from; `--source`
overrides), migrates the home as `trestle init --upgrade` does, using the new build, then starts it.
While any run is live on the home it refuses and changes nothing, unless `--drain` is given. After
it, `status` shows the same build as installed and running; a service still running an older build
shows `(stale: restart or upgrade)`.

## 6. Remove

```bash
trestle service uninstall   # stop (draining), unload, delete the unit; the home stays
```

## Credentials

A plugin that needs a token declares an identity by name (`@trestle(credentials=["gh"])`, see
[`plugins.md`](plugins.md#credentials)). You, the operator, give the identity its value and say
which plugins may use it. No agent ever sees the value, and nothing an agent sends can add an
identity or widen a grant.

```bash
# store the value: typed at a hidden prompt, or piped in (never a command-line argument)
trestle credentials add gh --env GH_TOKEN
op read "op://ci/github/token" | trestle credentials add gh --env GH_TOKEN

trestle credentials grant gh release     # let the plugin `release`, as published now, use it
trestle credentials list                 # identities, env names, each grant's status; no values
trestle service restart                  # identities and grants are read at start

trestle credentials add gh --replace     # rotate the value: applies to the next run, no restart
trestle credentials revoke gh release    # take the grant away (restart)
trestle credentials remove gh            # delete the identity and its stored value (restart)
```

- **Where things live.** The identities and grants are in `$TRESTLE_HOME/credentials.toml`
  (`0600`), which the commands write; read it, but change it through them. It holds no value. The
  values are in one encrypted file, `$TRESTLE_HOME/credentials/store.enc` (`0600`, directory
  `0700`).
- **The key.** On macOS the store's key is a generic password in your login Keychain (service
  `trestle-credentials`, account the home's path), created by the first `add`. Elsewhere it is
  `$TRESTLE_HOME/credentials/store.key` (`0600`), which is weaker: anything that can read your home
  as you can read the key and the store together, so the encryption only protects a copy of
  `store.enc` taken on its own. `add --key-provider file` picks the key file on macOS too; the
  choice is made by the first `add` and kept.
- **A grant is pinned to the plugin's code**: its `source_sha256` and its declared packages'
  digests as published when you ran `grant`. Republish the plugin with a change to either and the
  grant goes stale (`list` and `trestle doctor` say so, and its runs are refused
  `admission.credential_not_granted`) until you run `grant` again. A plugin an agent published
  with `publish_plugin` cannot be granted; review it, and save it into a watched folder as your
  own edit first.
- **When a change applies.** `credentials.toml` is read once at start, like `config.toml`: every
  command that changes an identity or a grant says so and names `trestle service restart`, or
  restarts for you with `--restart`. A value rotated with `add --replace` applies to the next run
  without a restart, because the service reads values from the store at each admission.
- `trestle doctor` shows the identities, each grant's status and whether the store and its key
  can be read.
- Only `kind = "encrypted_file"` exists in this build. The other kinds in the
  [decision record](decisions/service-and-credentials.md) (Keychain items, 1Password, AWS roles,
  GitHub Apps, a tool's own login) come later; a table naming one stops the service from starting
  with a message saying so.

## The home

The home (`TRESTLE_HOME`, default `~/.trestle`) must be on a local file system: every entry point
refuses an nfs, smbfs, afpfs, webdav or cifs mount. It records its format in `home/format` (2). A
home a v0.3.0 server used is refused until `trestle init --upgrade`, which `trestle service
upgrade` runs for you.

`trestle doctor` reports the home's health, the live servers and the live runs by owner, and, when
an admission lock is stuck, who holds it. `trestle recover` reaps runs whose server is gone and runs
the retention sweep. Idempotency keys live in `home/keys/`; `trestle doctor --rebuild-keys` repairs
them.

<!-- K-14 -->
**Type checking.** `mypy` runs at zero errors and is gated in CI by its own `typecheck` job. The gate needs the workflow packs installed (`pip install -e ".[dev,packs]"`): `trestle_packs` ships a `py.typed` marker so `import trestle_packs` type-checks, and with `.[dev]` alone the package is absent and `mypy` reports `import-not-found`. Run it locally the same way: `pip install -e ".[dev,packs]" && mypy`.
<!-- /K-14 -->

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Host shows no tools / connection refused | `trestle service status`; `trestle service start`; read `$TRESTLE_HOME/service.log` |
| Host gets HTTP 401 | The `Authorization` header is missing or stale: it must be `Bearer ` plus the contents of `$TRESTLE_HOME/agent.token` |
| `running build` differs from `installed build` | `trestle service upgrade` (or `restart` when the new build is already installed) |
| `install` refuses: not written by trestle | A unit you wrote is at that path; move it away, or keep running yours |
| Empty catalog / `admission.plugin_not_found` | `trestle init`; check `[plugins] paths` and `catalog_hint` on `list_plugins` |
| Pack plugins `valid: false` | `pip install -e ".[packs]"` into the service's venv; see [`packs.md`](packs.md) |
| `admission.service_draining` | The service is stopping or upgrading; it comes back with `start` |
| `a v0.3.0 home` | `trestle service upgrade`, or `trestle init --upgrade` with the service stopped |
| `admission.home_busy` | Another process held the home's admission lock over 2 s; retry, and see `trestle doctor` |
| `admission.after_unknown` | `run(after=...)` names a run or key no run used |
| `admission.credential_unknown` | A plugin declares an identity no `trestle credentials add` created |
| `admission.credential_not_granted` | `trestle credentials grant <name> <plugin>`, then `trestle service restart` (the plugin is new to the identity, or was republished) |
| `admission.credential_unavailable` | The store or its key could not be read, or the identity has no value: `trestle doctor` |
| `admission.ttl_out_of_range` | `idempotency_ttl_s` is negative, not whole seconds or above `[keys] max_ttl_s` in `config.toml` (default 604800) |

## Next

- Agents: [`agents.md`](agents.md)
- Lockdown details: [`security.md`](security.md)
- Operators: [`operator-sessions-telemetry.md`](operator-sessions-telemetry.md)
