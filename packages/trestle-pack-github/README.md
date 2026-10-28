# The `github` pack

A bundled Trestle pack (owner `trestle`): many GitHub pull requests in one run, through the
operator's own `gh` and `git`.

| Plugin | What it does | Changes GitHub |
|---|---|---|
| `github_policy` | The effective policy, its file and sha256, and the host | No |
| `github_pr_status` | Per PR: state, review decision, and a verdict on the checks that count; with `wait`, polls until they settle | No |
| `github_pr_comments` | Per PR: unresolved review threads, comments and review bodies, each with a suggested next step | No |
| `github_pr_open` | Commits the listed paths, pushes the branch and opens its PR (or, when asked, updates its own open PR), behind guards | Yes |

Each wrapper in `plugins/` carries its recipe in its docstring and body: the commands in order,
each as a visible argv, and the verdict. The library `trestle_pack_github` holds the mechanism
behind its `__all__`. To change a recipe, copy the wrapper and publish it under a new name.

## Install (the operator)

```bash
trestle pack install github
gh auth login --hostname github.com     # as the user the Trestle service runs as
```

`trestle pack install` checks that `gh` and `git` are on the service's PATH. If they live
elsewhere, set `TRESTLE_GH_PATH` and `TRESTLE_GIT_PATH` under `[plugins.env]`. For GitHub
Enterprise, set `GH_HOST` there too.

The plugins act as gh's own login: they declare no credential, and no token passes through
Trestle or an argument. Without a login, a run ends `Blocked("github.gh_auth")` with the
`gh auth login` line to run. `git push` uses the checkout's own remote and credential helper.

## Policy: tuning the pack without forking it

A company changes the pack's behaviour with a settings file, never by editing the pack. Name the
file with one line in `$TRESTLE_HOME/config.toml`:

```toml
[plugins.env]
TRESTLE_GITHUB_POLICY = "/abs/path/github-policy.toml"
```

The file holds only the keys it changes; everything else keeps the default shown here:

```toml
# github-policy.toml
policy_version = 1

[bots]                              # always: author type Bot, or a login ending in [bot]
logins = []                         # more bot logins (case-insensitive)
patterns = []                       # regular expressions matched against the whole login

[triage]                            # github_pr_comments' suggested next step
not_actionable = ["nit", "nitpick", "optional"]
clarify_on_question = true          # a human's question is "clarify"
code_change = ["bug", "error", "fix", "must", "should"]
bot_comments = "triage"             # or "code_change", or "ignore"

[checks]                            # what github_pr_status counts
required = "branch_protection"      # gh pr checks --required; "all" counts every check
add = []                            # check names that count as well
drop = []                           # check names that never count
no_checks = "report"                # its own verdict, never passed; "pass" opts in
grace_s = 90                        # time for checks to appear, when waiting
poll_s = 15                         # how often a waiting run asks again (at least 5)

[open]                              # github_pr_open
remote = "origin"
branch_pattern = ""                 # the head branch must match, when set
title_pattern = ""                  # the title must match, when set
body_templates = [".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md", "docs/pull_request_template.md"]
labels = []                         # always added
reviewers = []                      # always requested
draft = false                       # when the request does not say
edit_any_pr = false                 # true: update_existing may edit a PR someone else opened
```

- **Strict:** an unknown table or key, a wrong type, a bad regular expression or a
  `policy_version` other than 1 ends the run `Blocked("github.policy_invalid")`, naming the file
  and the key.
- **Visible:** every answer carries `policy: {source, sha256}` and attaches the effective policy
  as `github-policy.json`. The host also records the file's digest in each run's provenance
  (`pack.toml [config] files`). Two runs with the same snapshot and the same digest ran the same
  code with the same settings.
- **Not arguments:** policy keys never become plugin arguments, so schemas and snapshot ids do
  not depend on them.

### Defaults (P-21)

- **Bots:** only GitHub's own signal. No vendor names are built in.
- **Which checks count:** the branch's required checks (`gh pr checks --required`). When branch
  protection requires none, every reported check counts, so an unprotected branch is never
  passed by default. A skipped check passes and a cancelled one fails, as in branch protection.
- **No checks:** `no_checks`, which raises `Failed` (`checks_not_passed`). A waiting run first gives checks `grace_s` to
  appear.
- **Push:** never the default branch, the base or a detached HEAD. Only the listed `paths` are
  committed, with the request's `commit_message`; there is no built-in message. Never a force
  push.
- **Existing PRs:** edited only with `update_existing: true`, only when the acting login opened
  them, and never from a fork. Without a new `body`, the PR's body is left as it is.

## Answers and codes

A successful answer has a top-level `ok: true`, counts, one line per PR, and the full data
attached. A check that did not pass raises, so the run's outcome is `failed` or `blocked` and the
recorded result (`ok: false`, the same shape) is in its summary. `github_pr_status` answers PRs
that are only still `pending` (the wait ran out) as `failed` with `plugin.checks_pending`.

| Code | Kind | Meaning |
|---|---|---|
| `plugin.github.gh_missing`, `plugin.github.git_missing` | `Blocked` | The tool is not on the service's PATH or in the usual places |
| `plugin.github.gh_auth` | `Blocked` | gh has no login for the host, or it expired mid-run |
| `plugin.github.policy_invalid` | `Blocked` | The policy file is missing, unreadable or invalid |
| `plugin.<refusal code>` from `github_pr_open` | `Blocked`, `resend: "will_not_succeed"` | A guard said no before anything changed; `human_action` says what to change. The agent fixes the request: `github.default_branch`, `github.detached_head`, `github.head_is_base`, `github.branch_pattern`, `github.title_missing`, `github.title_pattern`, `github.commit_message_missing`, `github.paths_missing`, `github.path_outside_repo`, `github.index_not_clean`, `github.pr_exists`, `github.pr_not_yours`, `github.pr_from_fork`, `github.not_a_checkout` |
| `plugin.open_failed` | `Failed` | A command in `github_pr_open` failed; the detail and the result's `error` say which, and `pushed` says whether the push happened |
| `plugin.checks_not_passed` | `Failed` | `github_pr_status`: a PR's checks failed, there were none, or its status could not be read |
| `plugin.checks_pending` | `Failed` | `github_pr_status`: checks were still pending when the wait ran out |
| `plugin.comments_unread` | `Failed` | `github_pr_comments`: a PR's comments could not be read; the result has each PR's `error` |

A bad target or an unreadable PR is a line with `verdict: "error"` (or `error`), never a
crash.

## Seams for code-shaped changes (P-19)

When data cannot express a change, a company pack wraps the library instead of forking it. Its
wrapper declares both libraries, so digests, `provenance_mismatch` and credential grants cover
the company code:

```python
@trestle(packages=["trestle_pack_github", "acme_github"])
```

- **`GhTransport`**: how `gh` runs (`CtxGh` by default). Pass `env=ctx.credential_env("gh")` to
  `CtxGh` from a wrapper that declares `credentials=["gh"]`, to act as a granted identity instead
  of gh's own login.
- **`OpenBackend`**: `push`, `create` and `edit` (`GhOpenBackend` by default), for example to
  push to a mirror first.
- **`CommentTriage`**: `classify(item, body)`, for a company's own comment classifier
  (`PolicyTriage` by default).

Config-bound hooks are not built (P-19); policy and these seams carry the load.

## Tests

```bash
python -m pytest -q -c pyproject.toml --rootdir . packages/trestle-pack-github/tests
```

Each wrapper has a `FakeContext` test over the fake gh CLI (`trestle_pack_github.fakes.gh_cli`,
standard library only). git is the host's own, on a checkout under the test's temporary folder
whose `origin` is a local bare repository. No test reaches GitHub. Copy a test beside an adapted
wrapper to test it the same way.

The live check `tests/test_live_github.py` is skipped unless `TRESTLE_GITHUB_LIVE_PR` names a
pull request (a URL). It then reads that PR's status and comments with the real `gh`. It only
reads: it never pushes, comments or opens anything.
