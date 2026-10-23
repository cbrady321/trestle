# Credentials: tokens by name

Never take a token as an argument (there is no `secrets=` keyword: it was removed). Declare the
identity and read it at run time:

```python
@trestle(credentials=["gh"])
def open_pr(ctx: Context, repo: str) -> dict[str, str]:
    env = ctx.credential_env("gh")  # {"GH_TOKEN": "..."}
    result = ctx.exec(["gh", "pr", "create", "--fill", "-R", repo], env=env)
    return {"ok": str(result.ok)}
```

- `ctx.credential_env(name)` returns `{ENV_NAME: value}`. Nothing is put into the plugin's
  environment: pass it to `ctx.exec(env=...)` for the command that needs it. Other processes of
  the user can read a command's environment, so prefer `credential_file` when the tool can.
- `ctx.credential_file(name)` writes the value to a `0600` file of the run and returns its path,
  for a tool that reads a credentials file. The file is removed when the run ends.
- A name the plugin did not declare raises `LookupError`.
- Every value is scrubbed: logged, printed, returned, raised or attached, it reads `<redacted>`.
- `ctx.exec(credentials=...)` is reserved and raises `NotImplementedError`.

Only the operator configures and grants an identity. A run is refused before it has a run id:

- `admission.credential_unknown`: no identity of that name is configured.
- `admission.credential_not_granted`: the identity is not granted to this plugin as now
  published. A grant is pinned to the plugin's source and its declared packages, so a republish
  loses it, and a plugin published with `publish_plugin` is never granted.
- `admission.credential_unavailable`: the operator's store could not give the value.

Report the refusal's message, which names the operator's command, and stop.

Test with fake values: `FakeContext(tmp_path, credentials={"gh": {"GH_TOKEN": "fake"}})`.
