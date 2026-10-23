# Company-specific behaviour in bundled packs: hooks, policy and composition

2026-10-22. Analysis only; nothing here is built. Inputs:

- `2026-10-22-trestle-pack-catalog-design.md` (packs, sources, tiers, owner decisions 1–6) and
  the two 2026-10-19 pack documents;
- `docs/decisions/service-and-credentials.md` (grants pinned to `source_sha256` +
  `package_digests`, §2a lines 243–256; `[plugins.env]` from config only);
- core: `trestle/plugin/surface.py`, `trestle/plugin/testing.py`, `trestle/server/snapshots.py`,
  `trestle/server/registry.py`, `trestle/server/plugin_schema.py`, `trestle/child/validate.py`,
  `trestle/child/main.py`, `trestle/server/config.py`, `trestle/server/conductor.py`;
- the zip's `github_pr` package (cited `zip:` with paths under `trestle_packs/github_pr/`), read
  with `cat`, `sed` and `grep` only; nothing in it was run or imported;
- hyper-gremlin's `docs/PLUGIN_AUTHORING.md` and `PLUGIN_SECURITY.md`.

A parallel analysis covers pack organisation (bundled vs foundational vs company, granular packs,
the kit's name). Assumed here: the bundled `github` pack keeps its own library
(`trestle_pack_github`), wrappers declare it with `packages=`, and company packs arrive through
pack sources at the `company` tier with a per-pack `env` table, as the catalog design says.

## Headline

Nearly everything company-specific in `github_pr` is **data**: bot names, a commit message, a
remote name, template paths, a host regex, keyword lists for triage and a verdict rule. None of it
needs a code hook. The one thing that is code-shaped (what happens around a push) is already a
Python protocol in the zip (`OpenBackend`, `zip:open.py:42-53`), and Trestle already lets a company
pack supply one by wrapping the library in its own `@trestle` file, with full digest coverage.

The recommendation is therefore layered, and the first two layers need no new core mechanism:

1. **A per-pack policy file** (`github-policy.toml`), named by the pack's `env` table, validated by
   a dataclass in the library, echoed into every run's result and attached as an artifact. This
   covers the whole list in §1.
2. **Library seams, documented and versioned**, so a company pack composes or replaces a step by
   writing its own wrapper that declares both libraries in `packages=`. Grants, digests and
   `provenance_mismatch` cover the company code with no change to core.
3. **Declared hook bindings** (typed protocols published in `pack.toml`, bound in `config.toml`)
   are specified here as a general feature, but **deferred**: they cost a new core concept
   (hook packages folded into the snapshot identity) and no bundled pack needs them yet. The stack
   pack's "before containers" case is tree composition, not a hook.

Config-named code is code execution; the deferred design keeps it operator-named, pack-scoped and
digest-pinned, which is where hyper-gremlin's `className` falls short.

## 1. What a GitHub PR pack varies per company

Everything below is read from the zip. "Observed" is what the file says; the right-hand column is
the customization it implies.

| Concern | Observed in `zip:` | Shape of the variation |
|---|---|---|
| Bot markers | `comments.py:14` `_BOT_MARKERS = ("qodo", "copilot", "github-actions", "dependabot", "codecov", "sonar")`; `_author_is_bot` (`comments.py:277-282`) also honours `isBot` and a `[bot]` suffix. The fixture at `tests/test_github_pr_suite.py:259` is `qodo[bot]` | A list of strings |
| Comment triage | `_suggest_action` (`comments.py:293-304`): a `?` from a human is `clarify`; any bot comment is `code_change`; `nit`, `optional`, `style` are `not_actionable`; else `reply_only`. English keywords, fixed order | Keyword lists per class, and an order. Data, unless a company wants a classifier |
| Commit message | `open.py:96` `git commit -m "chore: ship via github_pr_suite"`, fixed | A template string |
| Remote and branch | `open.py:98` `git push -u origin HEAD`; the branch is whatever `HEAD` is, checked only afterwards (`open.py:108-119`); no branch-name rule anywhere | Remote name, a branch regex, a refuse-list (default branch) |
| PR title and body | `--fill` when no title (`open.py:151`), so the fixed commit message becomes the title; body from `_TEMPLATE_CANDIDATES` (`open.py:13-17`: three paths) with `Ticket: <url>` appended (`open.py:149-150`) | Template paths, a title pattern, a ticket line format |
| Labels, reviewers, assignees | None. No `--label`, `--reviewer` or `--assignee` appears in the package (grep over `*.py`) | Lists of strings, or "from CODEOWNERS" (generic `gh` behaviour) |
| Required checks and verdicts | `monitor.py:10` `FAILING_CONCLUSIONS`; `monitor.py:14-20` bucket map; `_rollup` sets `required_checks_met = None if failure else True` (`monitor.py:194`), so every check is required and branch protection is never read; no checks at all is `PASS` once `checks_grace_s` (90 s, `manifest.py:66`) passes (`monitor.py:138-139`) | A list of required check names, or "branch protection"; whether "no checks" passes; the grace |
| CI log reading | `ci_report.py:43-63` reads check-runs through `gh api`; the token regex (`ci_report.py:12`) knows only `ghp_` and `Bearer` | Extra redaction patterns; the proposal already notes the annotations call is wrong |
| Host and identity | `target.py:8` `_PR_URL` matches `https://github\.com/` only and `target.py:19` builds the same host; `transport.py:27` `gh_bin = "gh"`; `auth.py:7-11` runs `gh auth status` for whatever identity `gh` holds. No `GH_HOST` handling | A host name (`GH_HOST`), and an identity name (`credentials=["gh"]`, Decision 2) |
| Pre-push checks | None. `stage_and_commit` (`open.py:85-99`) stages, commits and pushes with nothing in between | A list of argv commands to run before the push, or code |
| Presets and timing | `manifest.py:7-13`, `preset_ops` (`manifest.py:72-83`); defaults concurrency 4, timeout 1800 s, poll 15 s, review-quiet 90 s (`manifest.py:63-68`) | Named presets with default timings |
| Employer traces | `run_m1_suite` alias (`batch.py:59`); internal spec ids `REQ-X-040` (`open.py:91`), `REQ-OPEN-005` (`batch.py:209`), `REQ-X-011` (`target.py:69`), "M1" (`target.py:35`); `o/hibob` in `tests/test_github_pr_batch_reporting.py:79` | Delete |
| Company plumbing | `auth.py:3` and `batch.py:12` import `GateFailed` and `check_live` from `trestle_packs.chain`, a package the owner excluded | Replace with `Blocked` (`surface.py:20-40`) and `ctx.cancelled` |

Observation: the zip already has three injection seams, all passed in code, never from config:
`GhTransport` (`transport.py:11-22`, with `FakeGhTransport`), `OpenBackend` (`open.py:42-53`, with
`FakeOpenBackend` at `open.py:56-78`) and `_Clock` (`batch.py:53-56`), wired through
`run_github_pr_suite(ctx, manifest, transport, *, clock, open_backend)` (`batch.py:78-86`).

Inference: of the twelve rows, eleven are data. Only "pre-push checks" and "what happens around a
push" can need code, and an argv list covers most pre-push cases.

## 2. The design space

How each option fares against the brief's criteria. Facts about core are cited; the rest is
judgement.

### (a) Data-only configuration

Two sources: operator config (`config.toml`), or a policy file in the company's source that the
operator points at. Values reach the run through the pack's `env` table, which is the mechanism
the catalog design already adds (per-pack `env` merged over `[plugins.env]`; the G1 rules at
`config.py:207-232` and `conductor.py:205` apply).

- **Security:** no code is named. A configured argv list (pre-push checks) is executed by
  `ctx.exec` in the child; that is operator-authored execution of the same trust as
  `[plugins.env]`, not agent-reachable. Note it in the policy schema as such.
- **Provenance:** a policy file is not a `.py` file, so `package_digest` (`validate.py:249-266`)
  never covers it, and no grant pins it. A policy change is therefore invisible to
  `provenance_mismatch`. Mitigation at the pack level: the plugin attaches the effective policy and
  returns its sha256; optionally core records a digest (see §3).
- **Runs in the child:** yes, the library parses the file.
- **Versioning:** a `policy_version` field; unknown keys refused.
- **FakeContext:** trivial (`FakeContext`, `testing.py:84-222`; the policy is a dataclass).
- **Discoverability:** `describe_plugin` (`registry.py:339-377`) shows nothing about env today; the
  pack can expose a read-only `github_policy` plugin that returns the effective policy.
- **Schemas and identity:** untouched unless a field is also offered as an argument.
- **Tier precedence:** none; one file per pack per service.
- **Failure:** an invalid file ends the run `Blocked("github.policy_invalid", "fix <path>: ...",
  resend="succeeds_after_action")`; the agent reads `human_action` and stops.
- **Limit:** cannot express logic.

### (b) A company pack that wraps or subclasses the bundled library

Nothing new. The company pack's `pyproject.toml` depends on `trestle-pack-github`; its wrapper
imports both libraries and declares `@trestle(packages=["trestle_pack_github", "acme_github"])`.

- **Security:** the wrapper is code in an installed `company` pack: the trust of `pip install`.
- **Provenance:** complete. Both packages are digested at publication (`snapshots.py:139-152`),
  checked at every run start (`child/main.py:66`), enter the snapshot id (`ids.py:57-82`) and the
  grant pin (Decision 2, §2a). A changed hook drops the grant, as it should.
- **Runs in the child:** yes.
- **Versioning:** the library's Python API is the contract; it needs semver and a changelog. The
  pack version equals the library version (`pack.toml` `version`).
- **FakeContext:** as today.
- **Discoverability:** the company wrapper is an ordinary plugin with its own schema and docstring.
- **Shadowing:** if the company reuses the name `github_pr_open`, tier precedence makes it win and
  `shadows` reports it (catalog design, owner decision 3). If it uses `acme_pr_open`, both exist.
- **Failure isolation:** a broken company library makes the company wrapper `valid: false`
  (`registry.py:236-244`); the bundled one is untouched unless shadowed.
- **`Blocked`:** the company code raises it; the answer carries its code and `human_action`.
- **Costs:** one wrapper file per customised plugin (five for github), and the fragile-base-class
  problem: a library upgrade can break a subclass. A `local` or `user` customization on top of a
  `company` one means subclassing the subclass.

### (c) Declared extension points with bindings in config

The bundled pack publishes typed protocols in `pack.toml`; a company pack ships implementations;
the operator binds them in `config.toml`. This is the "general solution" the owner asked about.

- **Security:** a binding is config-named code: exactly hyper-gremlin's `className`, which its
  own security doc calls "code execution capability" (`PLUGIN_SECURITY.md`, trust-boundary table).
  Acceptable only if a binding may name an object of an **installed pack in the lock** (never a
  bare dotted path), and only `config.toml` sets it (like `[plugins.env]`).
- **Provenance:** the hard part. A bundled wrapper cannot list a company module in `packages=`,
  so today the hook would be undigested, unpinned and republish-blind. Core would have to treat
  bound hook packages as part of the wrapper's **effective** `packages=`: appended at publication
  (`materialize_snapshot`, `snapshots.py:139-141`, passes `--package` to the validator), digested,
  entered into the snapshot id and the manifest `declared.package_digests`
  (`types.py:189-191`), and checked at run start (`child/main.py:66`, already generic over the
  recorded map). Then a hook change or re-binding moves the snapshot id and drops the grant.
- **Runs in the child:** the library reads the binding from a run environment variable the
  conductor sets (`TRESTLE_PACK_HOOKS`, JSON) and imports it; the host imports nothing.
- **Versioning:** the plugin-surface major (`surface = "1"`) says nothing about a pack's hook
  protocol. Each pack needs its own `hooks_api` major; an implementation declares which it
  implements; publication refuses a mismatch.
- **FakeContext:** the hook is a plain class; the pack ships a conformance test for it.
- **Discoverability:** `describe_plugin` gains `hooks: {"<name>": "<source>:<pack>/<object>"}`,
  and the `packs` guide topic explains it.
- **Schemas and identity:** schemas unchanged; identity changes with the binding.
- **Tier precedence:** none needed; a binding is explicit. Two packs offering the same hook do not
  collide, the operator picks.
- **Failure isolation:** the validator must import the hook at publication (a new `--hook`
  argument to `validate.py`) so a broken hook makes the wrapper `valid: false` with a reason naming
  the hook, rather than failing every run at import.
- **`Blocked`:** the library catches a hook's `Blocked` and re-raises it with the hook's name
  prefixed to the code (`plugin.acme_policy.jira_missing`); any other exception becomes
  `Blocked("github.hook_failed", "fix hook <name>: <type>: <message>", "will_not_succeed")`,
  because a company bug is not something the agent can retry past.
- **Cost:** a new concept in config, lock, publication, snapshot identity, describe and the guide:
  about the size of the catalog design's `trestle/packs/` module again. It also invites hook
  sprawl (§4).

### (d) Entry-point or registry discovery

`[project.entry-points."trestle_pack_github.hooks"]`, or a registry the library fills on import.
Install becomes enable, which T5 rejected for packs (catalog design, line 76) and which Decision 2
cannot live with: the hook set is unknown until runtime, so no digest or grant can pin it. The
host would also need `importlib.metadata`, which the catalog design keeps out of the host. Reject.

### (e) Composition at the tree level

A company workflow plugin declares a tree whose children come from both libraries: a company
leaf (`acme_bootstrap`) before the bundled units, a company leaf after. Trestle has this today
(`docs/plugins.md` "Composite workflows"): `needs` orders the steps, the answer names the node
that failed or blocked (`answer.primary.path`), `declared_codes` shows the codes before the run,
and the wrapper's `packages=` covers both libraries.

- It is before/after only: it cannot change the commit message inside `open`.
- One process: a company leaf that raises ends the whole tree `execution.unit_raised`.
- It requires the bundled library to expose its operations as workflow units, which the catalog
  design does not plan for github (its plugins are plain). For `stack`, the units exist.

### Summary

| Option | Code named in config | Covered by digests and grants | Core change | Fits github | Fits stack "before containers" |
|---|---|---|---|---|---|
| (a) policy data | no (argv lists: operator-authored) | no, unless core records it | none to S | 11 of 12 rows | partly (compose files, waves) |
| (b) wrap | no | yes, today | none | the 12th row | yes |
| (c) bound hooks | yes, pack-scoped | only with the effective-`packages=` change | M | over-built | over-built |
| (d) entry points | n/a | no | M | reject | reject |
| (e) tree | no | yes, today | none | partly (needs units) | yes |

## 3. Recommendation

**Layer 1: a per-pack policy file.** The library gains `trestle_pack_github/policy.py` with a
frozen dataclass `GithubPolicy` and `load_policy(path | None) -> GithubPolicy`. Fields, each with
the zip's value as its default (so an unconfigured install behaves like today, minus the employer
traces):

```toml
# github-policy.toml, kept in the company's pack source or anywhere the operator chooses
policy_version = 1

[host]
name = "github.com"                 # also sets GH_HOST for gh
identity = "gh"                     # the credential name the wrappers declare

[bots]
markers = ["github-actions", "dependabot", "codecov", "copilot"]   # "qodo", "sonar" are the company's

[triage]
clarify_on_question = true
code_change = ["fix", "consider", "should", "must", "error", "bug"]
not_actionable = ["nit", "optional", "style"]

[open]
remote = "origin"
branch_pattern = "^(?!main$|master$).+"         # refuse pushing a default branch
commit_message = "{title}"                       # placeholders: title, ticket, branch
title_pattern = ".*"
body_templates = [".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md"]
ticket_line = "Ticket: {ticket}"
labels = []
reviewers = []
draft_default = false
pre_push = []                                    # argv lists, run by ctx.exec in git_root

[checks]
required = "all"                                 # "all", "branch_protection", or a list of names
none_scheduled = "pass"                          # or "block"
grace_s = 90
redact_patterns = ["ghp_[A-Za-z0-9]{20,}", "github_pat_[A-Za-z0-9_]{20,}"]
```

The operator names it in the pack's env table, the mechanism the catalog design already adds:

```toml
[packs."trestle:github".env]
TRESTLE_GITHUB_POLICY = "/abs/path/github-policy.toml"
```

Rules: unknown keys and a wrong `policy_version` are refused; the run then ends
`Blocked("github.policy_invalid", ...)`. Every writer and reader returns `policy_sha256` and the
policy source in its result, and attaches `policy.json`, so an agent can see that company
behaviour was in force. A read-only `github_policy` plugin returns the effective policy for
discovery. Arguments stay generic (`targets`, `preset`, `update_existing`); nothing in the policy
is an argument, so schemas and snapshot ids do not depend on it.

**Layer 2: seams.** The library's public API is the composition contract, documented in the
pack's README and versioned with the pack:

- `GhTransport` (keep the zip's protocol, re-based on `ctx.exec`);
- `OpenBackend` (keep `zip:open.py:42-53`), the only code-shaped seam github needs;
- `CommentTriage` (`classify(item) -> SuggestedAction`), for a company classifier;
- `run_github_pr_suite(ctx, manifest, transport, *, policy, open_backend=None, triage=None)`.

A company pack customises by wrapping: its `acme_pr_open.py` declares
`packages=["trestle_pack_github", "acme_github"]`, builds `AcmeOpenBackend` and calls the suite.
This is the **minimal hook set for github**: two protocols plus the transport, all exercised
through `FakeContext.exec_handler` (`testing.py:166-222`).

**Layer 3: bound hooks, specified and deferred.** If the owner wants the general mechanism, this
is its shape, so a later decision does not start from zero:

```toml
# pack.toml of the bundled pack
[hooks]
api = "1"
open_backend = { protocol = "trestle_pack_github.open:OpenBackend" }
triage = { protocol = "trestle_pack_github.comments:CommentTriage" }

# pack.toml of the company pack
[provides]
"trestle:github/open_backend@1" = "acme_github.open:AcmeOpenBackend"

# config.toml
[packs."trestle:github".hooks]
open_backend = "acme:github-policy/open_backend"
```

Core changes it would need: `trestle/packs/` (models for `[hooks]` and `[provides]`, lock
entries); `server/config.py` (the `hooks` table, validated like `[plugins.env]`);
`server/snapshots.py` `materialize_snapshot` (append the bound packages to `packages` before
`validate_and_extract`, so `package_digests` and the snapshot id cover them);
`child/validate.py` (`--hook module:object`, imported after the digests so a broken hook is a
publication failure); `server/conductor.py:205` (set `TRESTLE_PACK_HOOKS` beside the env table);
`server/registry.py:339-377` (`hooks` on `describe`); the `packs` guide topic; and `trestle pack
doctor` (binding resolves, `hooks_api` matches). The grant pin needs no change: it already reads
`package_digests`.

**How the github pack splits.** `trestle_pack_github` keeps `batch`, `monitor`, `ci_report`,
`comments`, `open`, `target`, `transport`, `models`, `manifest`, `suite`, `digest`, `envelope`
(with the fixes the catalog design lists) and gains `policy`. `auth.py` goes: the auth probe
becomes `Blocked("gh_auth", ...)` in `batch`. `cli.py` goes. Company specifics leave as policy
defaults; a company pack (`acme:github-policy`) holds `github-policy.toml`, any `OpenBackend`, and
wrappers only if it wraps.

## 4. General implications

**Is this a general feature?** Layers 1 and 2 are conventions, and they generalise at no cost:
every bundled pack may read one policy file named by its env table, must echo the effective
policy, and must document its library seams. The `stack` pack's "before containers" company
bootstrap is **tree composition** (e): a company workflow with an `acme_bootstrap` leaf before the
`stack_up` units, both libraries in `packages=`. That gives the agent a named node in the answer
when the bootstrap blocks, which a hidden hook would not. Layer 3 would become general only if
built, and then every bundled pack would be expected to publish `[hooks]`, which is the sprawl
risk below. Recommendation: not until a second concrete need appears.

**Could hooks change what is bundled?** With a policy mechanism, some excluded packs look
different. From the zip's README: `hibob_project_up` is a generic "project up" engine with company
profiles baked in (`ai-mcp` → `poetry run uvicorn`, port 9010; `hibob` → `./gradlew
runLocalhost`, port 9000; README lines 54–56), and those profiles are policy data.
`local_system_up` chains compose waves with project launches (README lines 37–38), a generic
shape. `bdc_compose_up` is not separable: its prepare step is AWS STS, ECR login, Flyway and
hosts (README line 37), and the owner's rule is that AWS parts are fakes or `Blocked`. The
option, for the owner and not a reversal of decision 5: a generic `project_up` (profiles from
policy, no baked names) could be a candidate for a later bundled or kit addition; `chain`,
`kafka`, `logs`, `http_stub` and `bdc_compose_up` stay excluded. `github` does not depend on this.

**Risks.**

- *Hook sprawl.* Every "could a company want this different?" becomes a protocol, and each
  protocol is a compatibility promise in lockstep with Trestle. The policy-first rule limits it:
  a seam exists only where data cannot express the variation, and the README lists each seam.
- *Hidden behaviour change for agents.* An agent that ran `github_pr_open` yesterday gets a
  different commit message today because the operator edited a file no digest covers. The
  echo rule (`policy_sha256` in every result, `policy.json` attached) makes it visible after the
  fact; `describe_plugin`'s planned pack block could add `policy_sha256` to make it visible
  before. If the owner wants it in provenance proper, core would record the digest of each file
  the pack's env table names under a reserved suffix (`*_POLICY`) in the run's `provenance`
  (`child/main.py:50-70` is where the spec is read); that is a small, optional change.
- *Deprecation.* A policy key or a seam, once published, needs a policy: a deprecated key is
  accepted with a `warnings` entry for one minor version and refused at the next major, in
  lockstep with Trestle; a seam follows the same rule through the library's changelog.
  `policy_version` is the switch that lets a pack read an old file and say what to change.

## 5. Owner decisions needed

1. **Policy-first rule:** every bundled pack customises through one policy file named by its env
   table, with the effective policy echoed in results, before any code seam is considered.
2. **Library seams as the code path:** company packs customise by wrapping, with the library's
   Python API versioned with the pack. Accept the fragile-base-class cost.
3. **Defer bound hooks (layer 3)** until a second bundled pack needs code-shaped variation that
   neither policy nor tree composition covers, or build it now as a general feature.
4. **Provenance of policy files:** echo only (pack-level), or also record the policy digest in
   the run's provenance (small core change).
5. **The github defaults:** which bot markers, triage keywords and check rules ship as defaults,
   and whether `none_scheduled` defaults to `pass` (the zip's behaviour) or `block`.
6. **A generic `project_up`** from the zip's `hibob_project_up` engine, with profiles as policy:
   an option to consider later, not part of the github work.
