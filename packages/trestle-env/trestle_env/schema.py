"""The reference environment's request schema (MC-B-04 v1; L.RB-1.1; B2-C2 (1), WR-AUTH-3).

The published input schema of the `reference_env` plugin is derived from the plugin's own type
hints (docs/plugins.md: a plugin never authors a schema), following imports statically, so the
types a request may use live here as source:

* `ENV_ARG` names the environment argument; its value is the Compose project name, the opaque
  environment key the host compares as bytes (hld-wr-environment KDD 1);
* `ServiceId`, `TestId` and `OverrideId` are strings, each BOUND to the identifier set the tree
  declares for its argument (`ArgBinding`, `trestle_env.tree`): the schema does not list the catalog
  (no JSON Schema `enum`, so a catalog of 500 services costs the published schema nothing), and a
  list of them is a `set`, published `uniqueItems`. A duplicate is refused by schema validation with
  `admission.invalid_args`; an identifier outside the declared set is refused by admission before
  any run id with `UNKNOWN_IDENTIFIER`, naming it and `valid_listed_at`, where the valid ones are
  listed (the declared identifier set of the plan).

There is no field for a command, a path, a URL, an environment variable, a credential or a Compose
fragment: a request can name only catalog identifiers and the environment key.
"""

from __future__ import annotations

from typing import TypeAlias

ENV_ARG = "env"
"""The request argument that carries the environment key (the Compose project name)."""

SERVICES_ARG = "services"
"""The request argument that names the services to bring up (catalog identifiers)."""

TESTS_ARG = "tests"
"""The request argument that names the catalog tests to run."""

OVERRIDES_ARG = "overrides"
"""The request argument that names the catalog local overrides to apply."""

ServiceId: TypeAlias = str  # noqa: UP040  (the schema reader knows this spelling only)
"""A catalog service identifier, bound to the tree's declared `services` set."""

TestId: TypeAlias = str  # noqa: UP040  (the schema reader knows this spelling only)
"""A catalog test identifier, bound to the tree's declared `tests` set."""

OverrideId: TypeAlias = str  # noqa: UP040  (the schema reader knows this spelling only)
"""A catalog override identifier, bound to the tree's declared `overrides` set."""
