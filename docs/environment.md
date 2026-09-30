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
other) and `services` selects services from a closed list of identifiers.

```json
{"env": "checkout-dev", "services": ["postgres"]}
```

An identifier outside the list is refused before a run id exists, with the identifier and where the
valid ones are listed.
