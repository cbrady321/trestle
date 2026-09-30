"""L.RB-6.1: the provisioning adapter's own facts, over the real `CommandPort` and the psql double
(no engine): the argv shape (psql over TCP to the container's own address, the password through the
exec environment, role, database and SQL as positional arguments), statement quoting, the
confirmation rules of a submit, and the import boundary. The family cases
(`test_conformance.py`) run the same port through the one suite.
"""

from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.proof.suites.ports.families import LINEAGE, effect_call, ticket
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime, RealizationKind
from trestle.workflow.values import (
    ConfirmationStatus,
    FoundRef,
    Lineage,
    NodePath,
    SelectorRef,
    StopCause,
)

from trestle_packs.container.engine import (
    DOCKER_CLI_MISSING,
    PROVISION_STORE_UNREADABLE,
    DockerCli,
)
from trestle_packs.container.reads import selector_name as container_selector_name
from trestle_packs.process.command import CommandPort
from trestle_packs.provision import (
    TABLE,
    ProvisionPort,
    RecordStore,
    probe_sql,
    psql_args,
    submit_sql,
)
from trestle_packs.provision import postgres_record as module

from . import rig

STATUS = ConfirmationStatus


def create_ticket(attempt: int = 1) -> object:
    return ticket("up", EffectFacetClass.CREATE, Lifetime.DURABLE, attempt)


@pytest.fixture
def shim(tmp_path: Path) -> rig.Shim:
    return rig.Shim(tmp_path / "engine")


def test_every_statement_runs_psql_over_tcp_with_the_password_in_the_exec_environment(
    shim: rig.Shim,
) -> None:
    port = rig.port_over(shim)
    port.observe(rig.SPEC, LINEAGE, "up")
    assert port.create(rig.SPEC, create_ticket()).status is STATUS.APPLIED
    calls = shim.calls()
    assert len(calls) >= 3  # the probe, the presence read, the submit
    for call in calls:
        argv = call["argv"]
        assert argv[:2] == ["--host", rig.ENDPOINT] and argv[2] == "exec"
        assert argv[3:5] == ["-e", f"PGPASSWORD={rig.PASSWORD}"]
        assert argv[5] == rig.STORE_CONTAINER
        assert argv[6:8] == ["sh", "-c"] and argv[9] == "sh"
        script, role, database = argv[8], argv[10], argv[11]
        assert (role, database) == (rig.ROLE, rig.DATABASE)
        assert "hostname -i | cut -d' ' -f1" in script and "exec psql" in script
        for trusted in ("127.0.0.1", "localhost", "::1", "/var/run", ".s.PGSQL", "pg_isready"):
            assert trusted not in script, trusted  # the image trusts those without a password
        assert "$1" in script and "$2" in script and "$3" in script  # positional, not interpolated
        assert rig.ROLE not in script and rig.DATABASE not in script and TABLE not in script
        assert call["pgpassword_set"] is True
    assert all(call["argv"][0] != "psql" for call in calls)  # never run outside the container


def test_a_script_that_connects_over_a_trusted_path_is_refused_by_the_double(
    shim: rig.Shim, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "PSQL_SCRIPT", module.PSQL_SCRIPT.replace("-h ", "-h 127.0.0.1 "))
    seen = rig.port_over(shim).observe(rig.SPEC, LINEAGE, "up")
    assert seen.code == PROVISION_STORE_UNREADABLE  # the double's exit 97: a read that failed


def test_a_wrong_password_is_never_read_as_absent_and_a_submit_changes_nothing(
    shim: rig.Shim,
) -> None:
    port = rig.port_over(shim, rig.WRONG_PASSWORD)
    seen = port.observe(rig.SPEC, LINEAGE, "up")
    assert seen.code == PROVISION_STORE_UNREADABLE and seen.selector_present is False
    refused = port.create(rig.SPEC, create_ticket())
    assert refused.status is STATUS.NOT_APPLIED and refused.code == PROVISION_STORE_UNREADABLE
    assert shim.keys() == frozenset()


def test_a_missing_table_is_observed_absent_and_the_submit_creates_it(shim: rig.Shim) -> None:
    port = rig.port_over(shim)
    seen = port.observe(rig.SPEC, LINEAGE, "up")
    assert seen.code is None and seen.selector_present is False and seen.found == ()
    assert port.create(rig.SPEC, create_ticket()).status is STATUS.APPLIED
    assert shim.keys() == {rig.selector_of(LINEAGE)}


def test_a_missing_docker_cli_is_its_own_code_for_reads_and_submits(tmp_path: Path) -> None:
    absent = ProvisionPort(
        DockerCli(str(tmp_path / "no-such-docker"), rig.ENDPOINT, CommandPort()), rig.store()
    )
    assert absent.observe(rig.SPEC, LINEAGE, "up").code == DOCKER_CLI_MISSING
    assert absent.check("recorded", _ref(LINEAGE)).code == DOCKER_CLI_MISSING
    refused = absent.create(rig.SPEC, create_ticket())
    assert refused.status is STATUS.NOT_APPLIED and refused.code == DOCKER_CLI_MISSING


def _ref(lineage: Lineage) -> object:
    return SelectorRef(lineage, "up", container_selector_name(lineage), datetime.now(UTC))


def test_a_submit_that_failed_before_any_change_is_not_applied(shim: rig.Shim) -> None:
    shim.write(fail_submit=True)
    confirmation = rig.port_over(shim).create(rig.SPEC, create_ticket())
    assert confirmation.status is STATUS.NOT_APPLIED and confirmation.identity is None
    assert shim.keys() == frozenset()


def test_a_submit_that_committed_and_then_lost_its_connection_is_applied(shim: rig.Shim) -> None:
    shim.write(apply_then_fail=True)
    confirmation = rig.port_over(shim).create(rig.SPEC, create_ticket())
    assert confirmation.status is STATUS.APPLIED  # the failed call is followed by a read
    assert confirmation.identity == rig.selector_of(LINEAGE)
    assert shim.keys() == {rig.selector_of(LINEAGE)}


class SubmitStartedCancel:
    """A root cancel that goes up once the double has logged the submit: the child has started
    (and, under `hang`, will never finish by itself)."""

    def __init__(self, shim: rig.Shim) -> None:
        self._shim = shim

    @property
    def requested(self) -> bool:
        return any("CREATE TABLE" in call["argv"][-1] for call in self._shim.calls())

    def cause(self) -> StopCause:
        return StopCause.CANCEL

    def wait(self, timeout: timedelta) -> bool:
        return self.requested


def test_a_submit_ended_by_the_root_cancel_is_unknown_never_not_applied(shim: rig.Shim) -> None:
    shim.write(hang=True)
    port = rig.port_over(shim, cancel=SubmitStartedCancel(shim))
    confirmation = port.create(rig.SPEC, create_ticket())
    assert confirmation.status is STATUS.UNKNOWN  # it may have committed and cannot be confirmed
    assert confirmation.identity == rig.selector_of(LINEAGE)


def test_a_repeated_submit_writes_once_and_the_second_call_never_reaches_the_store(
    shim: rig.Shim,
) -> None:
    port = rig.port_over(shim)
    port.create(rig.SPEC, create_ticket(1))
    writes = [c for c in shim.calls() if "CREATE TABLE" in c["argv"][-1]]
    port.create(rig.SPEC, create_ticket(2))
    assert [c for c in shim.calls() if "CREATE TABLE" in c["argv"][-1]] == writes  # still one


def test_a_run_lifetime_is_refused_before_any_docker_call(shim: rig.Shim) -> None:
    port = rig.port_over(shim)
    with pytest.raises(ValueError, match="RUN"):
        port.create(rig.SPEC, ticket("up", EffectFacetClass.CREATE, Lifetime.RUN))
    with pytest.raises(ValueError, match="RUN"):
        port.release_descriptor(effect_call("create", {"spec": rig.SPEC}, Lifetime.RUN))
    assert shim.calls() == []
    assert port.release_descriptor(
        effect_call("create", {"spec": rig.SPEC}, Lifetime.DURABLE)
    ) == ports.Durable(ports.DurableOwner.ENVIRONMENT)


def test_only_a_provisioned_spec_with_a_declared_record_is_created(shim: rig.Shim) -> None:
    port = rig.port_over(shim)
    docker_spec = ports.ResourceSpec(rig.SYSTEM, RealizationKind.DOCKER_SERVICE, rig.ENTRY, None)
    unknown = ports.ResourceSpec(rig.SYSTEM, RealizationKind.PROVISIONED, "no-such-entry", None)
    for spec in (docker_spec, unknown):
        with pytest.raises(ValueError):
            port.create(spec, create_ticket())
    with pytest.raises(ValueError, match="PROVISIONED"):
        port.release_descriptor(effect_call("create", {"spec": docker_spec}, Lifetime.DURABLE))
    assert shim.calls() == []


def test_statements_quote_their_literals_and_identifiers_are_validated() -> None:
    hostile = "x'; DROP TABLE trestle_provisioned; --"
    store = rig.store()
    for sql in (
        probe_sql(store, hostile, hostile),
        submit_sql(store, hostile, hostile, hostile),
    ):
        assert "'x''; DROP TABLE" in sql and "; --'" in sql  # every occurrence stays in a literal
    with pytest.raises(ValueError, match="identifier"):
        RecordStore(lambda lineage: "c", "role; drop", "db", "pw")
    with pytest.raises(ValueError, match="identifier"):
        RecordStore(lambda lineage: "c", "role", "db", "pw", table="t; x")
    with pytest.raises(ValueError, match="NUL"):
        module.literal("a\0b")


def test_a_hostile_system_name_is_stored_and_read_back_as_data(shim: rig.Shim) -> None:
    hostile = ports.ResourceSpec(
        "sys'; DROP TABLE trestle_provisioned; --", RealizationKind.PROVISIONED, rig.ENTRY, None
    )
    port = rig.port_over(shim)
    assert port.create(hostile, create_ticket()).status is STATUS.APPLIED
    assert port.observe(hostile, LINEAGE, "up").selector_present is True
    assert shim.keys() == {rig.selector_of(LINEAGE)}  # the table survived


def test_found_records_are_bounded_and_their_keys_are_kept_whole(shim: rig.Shim) -> None:
    for _ in range(7):
        shim.plant(rig.SYSTEM)
    port = rig.port_over(shim)
    seen = port.observe(rig.SPEC, LINEAGE, "up")
    assert seen.selector_present is False and len(seen.found) == module.FOUND_KEEP
    assert all(f.selector.startswith("trwr-r_other_root-planted-") for f in seen.found)
    port.create(rig.SPEC, create_ticket())
    again = port.observe(rig.SPEC, LINEAGE, "up")
    assert again.selector_present is True and len(again.found) == module.FOUND_KEEP


def test_the_secret_is_only_in_the_docker_argv_never_in_a_result(shim: rig.Shim) -> None:
    port = rig.port_over(shim)
    made = port.create(rig.SPEC, create_ticket())
    seen = port.observe(rig.SPEC, LINEAGE, "up")
    checked = port.check("recorded", seen.selector_ref)
    assert rig.PASSWORD not in repr((made, seen, checked))
    assert rig.PASSWORD in psql_args(rig.store(), "c", "SELECT 1")[2]  # the exec environment


def test_the_selector_is_the_container_adapters(shim: rig.Shim) -> None:
    for lineage in (
        LINEAGE,
        Lineage("r_abc123", NodePath(("backend", "Provision.Record"))),
        Lineage("r_abc123", NodePath(("a b", "ÄÖ"))),
    ):
        assert module.selector_name(lineage) == container_selector_name(lineage)
    assert rig.selector_of(LINEAGE) == "trwr-r_suite_0001-suite"


def test_only_a_record_this_port_submitted_is_checked(shim: rig.Shim) -> None:
    port = rig.port_over(shim)
    found = port.check("recorded", ports_found())
    assert found.satisfied is False and shim.calls() == []
    ref = _ref(LINEAGE)
    assert port.check("something-else", ref).satisfied is False
    assert port.check("recorded", ref).satisfied is False  # nothing was submitted


def ports_found() -> object:
    return FoundRef("postgres_record", "trwr-r_other-x", datetime.now(UTC))


def test_the_provisioning_module_imports_only_stdlib_workflow_and_the_container_adapter() -> None:
    base = Path(module.__file__).parent
    offenders = []
    for path in sorted(base.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
            for name in names:
                top = name.split(".")[0]
                if top in sys.stdlib_module_names or top == "__future__":
                    continue
                allowed = ("trestle.workflow", "trestle_packs.provision", "trestle_packs.container")
                if not name.startswith(allowed):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
