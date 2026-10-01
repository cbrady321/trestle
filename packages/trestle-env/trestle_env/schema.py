"""The reference environment's request schema, v0 (MC-B-04 v0; L.RB-0.2; B2-C2 (1), WR-AUTH-3).

The published input schema of the `reference_env` plugin is derived from the plugin's own type
hints (docs/plugins.md: a plugin never authors a schema), following imports statically, so the
closed types a request may use live here as source, not as runtime data:

* `ENV_ARG` names the environment argument; its value is the Compose project name, the opaque
  environment key the host compares as bytes (hld-wr-environment KDD 1);
* `ServiceName` is the closed set of services a v0 request may select, spelled from `CATALOG_V0`
  (a test pins the two together). A request can name only these, never a command, path, URL or
  free-form Compose field: there is no such field in the schema (WR-AUTH-3).

v0 enumerates its one service. L.RB-1.1 replaces the enumeration with a string bound to the
declared identifier set (`ArgBinding`), so an unknown identifier is refused by admission before a
run id and the schema no longer lists the catalog.
"""

from __future__ import annotations

from enum import StrEnum

ENV_ARG = "env"
"""The request argument that carries the environment key (the Compose project name)."""

SERVICES_ARG = "services"
"""The request argument that names the services to bring up (identifiers, never content)."""


class ServiceName(StrEnum):
    """The closed identifiers of tree v0 (`catalog_v0.CATALOG_V0`)."""

    POSTGRES = "postgres"
