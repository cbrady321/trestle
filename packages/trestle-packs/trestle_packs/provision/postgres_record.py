"""Provisioning adapter over a Postgres record store (L.RB-6.1; B3-C1, B3-C3, B3-C4, B3-C21, V-5.1,
V-7, V-10.2, WR-ENV-4, WR-PROOF-4).

Provisioning is no port of its own (B3-C21): a submit is `ResourceCreate.create` with
`spec.realization` `PROVISIONED`, and the probe is `ResourceReads.observe`, the authoritative read
of the environment's store. `ProvisionPort(docker, store)` is both, over the operator's docker CLI
(`DockerCli`, so every call is an `ExecutionPort.run`: stdin closed, environment built from empty,
every process attributable to the run):

- **the store** is a Postgres container, named by `RecordStore.container(lineage)`, holding one
  table of records `(key, system, payload)`. The record a run submits has the run-scoped selector
  `trwr-<root run_id>-<path>` as its `key` and `spec.logical_system` as its `system`, so
  submission is idempotent per selector (`INSERT ... ON CONFLICT (key) DO NOTHING`) and a record of
  the same system under another key (another root's, or one planted) is a found record, never the
  selector's (WR-OWN-7).
- **every statement is run by `docker exec` inside the container** as `psql` over TCP to the
  container's own non-loopback address with the fixture password from the exec environment
  (`PGPASSWORD`): never `127.0.0.1`, `localhost` or a socket, which the official image trusts
  without a password, and never `pg_isready`, which does not authenticate. Whether a password is
  really demanded is proven at HOST (L.RB-0.4's wrong-password case); this adapter's shape is what
  CI checks. Identifiers are validated, string literals are quoted, and role, database and SQL
  travel as positional arguments of the `sh -c` script, never interpolated into it.
- **`observe` is read-only** (one `SELECT`) and authoritative: `selector_present` iff a row with
  this root's key exists; other rows of the system are `found`. A store that cannot be read (the
  CLI missing, the engine, container or store not answering, a wrong password) is V-3.8's
  could-not-observe shape with a code, never a partial or an absent answer; a missing table is
  observed absent (nothing was ever submitted). `check("recorded")` is the convenience read: the
  same store, addressed by the ref's key. A real Postgres primary does not lag; the fake models a
  lagging read (`FakeProvision(lag=...)`) so a node can be shown never to resubmit on one.
- **the submit** is `Durable(ENVIRONMENT)` for a `DURABLE` call (B3-C3, assumed pending F-B3-2,
  which stays gated); a `RUN` call has no `RUN` form, so `release_descriptor` and `create` refuse
  it (V-10.2, V-10.3) before any machine call. It creates the table if absent and inserts the
  record in one `psql -c`. `NOT_APPLIED` only when the adapter can prove nothing changed (nothing
  could be read, or a failed submit left the key absent); a call that may have committed and cannot
  be confirmed is `UNKNOWN`.
- a provisioned record has no route: `endpoint` is `RouteRefused(ROUTE_UNSUPPORTED)`.

Imports: the standard library, `trestle.workflow` and `trestle_packs.container` (its `DockerCli`,
the one place `docker` is started, and the run-scoped selector, so the two adapters cannot
disagree).

No password is ever in a result or a detail (the excerpt of a failed call is not passed on: only a
fixed text and the codes). The password IS in the docker argv the port runs; that argv is the
fixture's, never recorded by the port.

Executor-chosen values (the contract names none): the table `trestle_provisioned` and its three
columns; `FOUND_KEEP = 4` found rows read, each key cut at `KEY_MAX = 100` characters (a probe's
output must fit the execution port's 512-byte excerpt: five 100-character lines and their
newlines); the codes for an unreadable store (`DOCKER_ENGINE_UNREACHABLE`: no V-11 code names a
store that did not answer) and for an output that is not the expected shape
(`TOOLCHAIN_INTERFACE_DRIFT`); the `ExecutionPolicy` of a submit (no launch happens).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from trestle.workflow.declarations import CheckRef, EffectId, Lifetime, RealizationKind, Vantage
from trestle.workflow.ports import (
    CatalogEntry,
    Durable,
    DurableOwner,
    EffectCall,
    Endpoint,
    ExecutionPolicy,
    Helpers,
    ReleaseDescriptor,
    ResourceObservation,
    ResourceSpec,
    RouteRefused,
    SelfProvisioning,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    CheckResult,
    Confirmation,
    ConfirmationStatus,
    FoundRef,
    Lineage,
    SelectorRef,
)

from trestle_packs.container.engine import (
    DOCKER_CLI_MISSING,
    DOCKER_ENGINE_UNREACHABLE,
    TOOLCHAIN_INTERFACE_DRIFT,
    Call,
    DockerCli,
)
from trestle_packs.container.reads import SELECTOR_PREFIX, selector_name

RECORD_KIND: Final = "postgres_record"  # the resource kind of a found record
RECORDED: Final = "recorded"  # the convenience read's check id
ROUTE_UNSUPPORTED: Final = "admission.route_unsupported"
TABLE: Final = "trestle_provisioned"
FOUND_KEEP: Final = 4  # found rows read (the rest are not named)
KEY_MAX: Final = 100  # characters of a found key that are kept

# psql over TCP to the container's own address (never a loopback or a socket); role, database and
# SQL are positional arguments ($1 $2 $3). `-X` reads no psqlrc; ON_ERROR_STOP makes a failed
# statement a non-zero exit.
PSQL_SCRIPT: Final = (
    "exec psql -X -v ON_ERROR_STOP=1 -h \"$(hostname -i | cut -d' ' -f1)\" "
    '-U "$1" -d "$2" -tAc "$3"'
)
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def literal(text: str) -> str:
    """A SQL string literal: single quotes doubled (standard_conforming_strings is on), and no
    NUL, which no text value may hold."""
    if "\0" in text:
        raise ValueError("a SQL literal cannot hold NUL")
    return "'" + text.replace("'", "''") + "'"


def _identifier(text: str, what: str) -> str:
    if not _IDENTIFIER.match(text):
        raise ValueError(f"{what} {text!r} is not a plain lowercase identifier")
    return text


@dataclass(frozen=True, slots=True)
class RecordStore:
    """Where the records go and how to read them: the container running the store for the run of a
    lineage (the composition root knows which node it is), the role, database and fixture password
    (handed to `psql` through the exec environment only), the table, and the payload each catalog
    entry submits."""

    container: Callable[[Lineage], str]
    role: str
    database: str
    password: str
    payloads: Mapping[CatalogEntry, str] = field(default_factory=dict)
    table: str = TABLE

    def __post_init__(self) -> None:
        _identifier(self.role, "role")
        _identifier(self.database, "database")
        _identifier(self.table, "table")


def probe_sql(store: RecordStore, selector: str, system: str) -> str:
    """The authoritative read: this root's row first (`S`), then other rows of the system
    (`F<key>`)."""
    key, sys_ = literal(selector), literal(system)
    return (
        f"SELECT CASE WHEN key = {key} THEN 'S' ELSE 'F' || substr(key, 1, {KEY_MAX}) END "
        f"FROM {store.table} WHERE system = {sys_} "
        f"ORDER BY (key = {key}) DESC, key LIMIT {FOUND_KEEP + 1}"
    )


def recorded_sql(store: RecordStore, selector: str) -> str:
    return f"SELECT 1 FROM {store.table} WHERE key = {literal(selector)}"


def submit_sql(store: RecordStore, selector: str, system: str, payload: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {store.table} "
        "(key TEXT PRIMARY KEY, system TEXT NOT NULL, payload TEXT NOT NULL); "
        f"INSERT INTO {store.table} (key, system, payload) "
        f"VALUES ({literal(selector)}, {literal(system)}, {literal(payload)}) "
        "ON CONFLICT (key) DO NOTHING"
    )


def psql_args(store: RecordStore, container: str, sql: str) -> tuple[str, ...]:
    """The `docker` arguments that run `sql` in `container`: `exec -e PGPASSWORD=... <container>
    sh -c <script> sh <role> <database> <sql>`."""
    return (
        "exec",
        "-e",
        f"PGPASSWORD={store.password}",
        container,
        "sh",
        "-c",
        PSQL_SCRIPT,
        "sh",
        store.role,
        store.database,
        sql,
    )


def _applied(identity: str) -> Confirmation:
    return Confirmation(ConfirmationStatus.APPLIED, None, identity)


def _not_applied(code: str | None = None) -> Confirmation:
    return Confirmation(ConfirmationStatus.NOT_APPLIED, code, None)


def _unknown(identity: str | None = None) -> Confirmation:
    return Confirmation(ConfirmationStatus.UNKNOWN, None, identity)


class ProvisionPort:
    """`ResourceReads` + `ResourceCreate` for `PROVISIONED` records in a Postgres store."""

    def __init__(self, docker: DockerCli, store: RecordStore) -> None:
        self._docker = docker
        self._store = store

    # ------------------------------------------------------------------ ResourceReads

    def observe(
        self, spec: ResourceSpec, lineage: Lineage, effect: EffectId | None
    ) -> ResourceObservation:
        selector = "" if effect is None else selector_name(lineage)
        call = self._exec(lineage, probe_sql(self._store, selector, spec.logical_system))
        down = self._unreadable(call)
        if down == "absent":
            return ResourceObservation(False, None, False, False, (), (), None)
        if down is not None:
            return ResourceObservation(False, None, False, False, (), (), down)
        lines = [line for line in call.output.splitlines() if line.strip()]
        present = bool(lines) and lines[0].strip() == "S"
        rows = lines[1:] if present else lines
        if any(not row.startswith("F") for row in rows):
            return ResourceObservation(False, None, False, False, (), (), TOOLCHAIN_INTERFACE_DRIFT)
        now = datetime.now(UTC)
        found = tuple(FoundRef(RECORD_KIND, row[1:], now) for row in rows[:FOUND_KEEP])
        ref = (
            SelectorRef(lineage, effect, selector, now) if present and effect is not None else None
        )
        return ResourceObservation(present, ref, present, present, (), found, None)

    def check(self, check: CheckRef, target: object) -> CheckResult:
        """The convenience read: is this key's record in the store? (`recorded`)."""
        lineage = getattr(target, "lineage", None)
        selector = getattr(target, "selector", "")
        if not isinstance(lineage, Lineage) or not selector.startswith(SELECTOR_PREFIX):
            return CheckResult(False, None, "not a record this port submitted")
        if check != RECORDED:
            return CheckResult(False, None, f"{check} is not a check this adapter knows")
        call = self._exec(lineage, recorded_sql(self._store, selector))
        down = self._unreadable(call)
        if down == "absent":
            return CheckResult(False, None, "the record store holds no such record")
        if down is not None:
            return CheckResult(False, down, "the record store could not be read")
        if call.output.strip() == "1":
            return CheckResult(True, None, "the record is in the store")
        return CheckResult(False, None, "the record is not in the store yet")

    def endpoint(self, target: object, vantage: Vantage) -> Endpoint | RouteRefused:
        return RouteRefused(
            ROUTE_UNSUPPORTED,
            "A provisioned record has no address to route to: read it through the "
            "authoritative probe, not an endpoint.",
        )

    # ------------------------------------------------------------------ ResourceCreate

    def launch_policy(self, spec: ResourceSpec) -> ExecutionPolicy:
        """Pure (B3-C4): a submit launches nothing, so no self-provisioning and no helper."""
        return ExecutionPolicy(SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.PREVENTED, None)

    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor:
        """`Durable(ENVIRONMENT)` for a `DURABLE` submit (B3-C3, assumed pending F-B3-2); a `RUN`
        call has no `RUN` form and is refused (V-10.2, V-10.3)."""
        spec = call.arguments["spec"]
        if not isinstance(spec, ResourceSpec) or spec.realization is not (
            RealizationKind.PROVISIONED
        ):
            raise ValueError("a provisioning port creates PROVISIONED resources only (B3-C4)")
        if call.lifetime is not Lifetime.DURABLE:
            raise ValueError("a provisioned record has no RUN form: it is created DURABLE (V-10.2)")
        return Durable(DurableOwner.ENVIRONMENT)

    def create(self, spec: ResourceSpec, ticket: AttemptTicket) -> Confirmation:
        if spec.realization is not RealizationKind.PROVISIONED:
            raise ValueError("a provisioning port creates PROVISIONED resources only (B3-C4)")
        if ticket.lifetime is not Lifetime.DURABLE:
            raise ValueError("a provisioned record has no RUN form: it is created DURABLE (V-10.2)")
        payload = self._store.payloads.get(spec.entry)
        if payload is None:
            raise ValueError(f"no record definition for catalog entry {spec.entry!r}")
        lineage = ticket.lineage
        selector = selector_name(lineage)
        present = self._present(lineage, selector, ticket)
        if isinstance(present, Confirmation):
            return present  # the store could not be read: nothing changed
        if present:  # an earlier attempt landed unconfirmed: converge on its record (B3-C4)
            return _applied(selector)
        sql = submit_sql(self._store, selector, spec.logical_system, payload)
        made = self._exec(lineage, sql, ticket)
        if not made.started:
            return _not_applied(DOCKER_CLI_MISSING)
        if made.interrupted is None and made.exit_status == 0:
            return _applied(selector)
        after = self._present(lineage, selector, ticket)  # what did the failed call leave?
        if isinstance(after, Confirmation) or made.interrupted is not None:
            return _unknown(selector)
        return _applied(selector) if after else _not_applied()

    # ------------------------------------------------------------------ internals

    def _exec(self, lineage: Lineage, sql: str, ticket: AttemptTicket | None = None) -> Call:
        container = self._store.container(lineage)
        return self._docker.call(psql_args(self._store, container, sql), ticket)

    def _unreadable(self, call: Call) -> str | None:
        """None when the store answered; `"absent"` when its table does not exist yet (nothing was
        ever submitted); otherwise the could-not-observe code (V-3.8)."""
        if not call.started:
            return DOCKER_CLI_MISSING
        if call.interrupted is not None:
            return call.interrupted
        if call.exit_status == 0:
            return None
        if f'relation "{self._store.table}" does not exist' in call.output:
            return "absent"
        return DOCKER_ENGINE_UNREACHABLE

    def _present(
        self, lineage: Lineage, selector: str, ticket: AttemptTicket
    ) -> bool | Confirmation:
        """Whether this key's record exists, or `NOT_APPLIED(code)` when the store could not be
        read (so the caller may claim nothing changed)."""
        call = self._exec(lineage, recorded_sql(self._store, selector), ticket)
        down = self._unreadable(call)
        if down == "absent":
            return False
        if down is not None:
            return _not_applied(down)
        return call.output.strip() == "1"
