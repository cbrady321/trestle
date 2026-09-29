"""Conformance suite core for the B3 port families (L.SV-5.15; B3-C17, B3-C20, V-5.1, DM-09).

One suite per port family, run UNMODIFIED against the fake and every real implementation
(WR-PROOF-4): a family is registered once with its cases (`register_family`) and run against any
implementation factory (`run_family`). The suite file's sha256 is recorded per run, and a run
refuses to start if that file's bytes differ from the ones registered (the suite was modified).

The read-facet watcher (`watch`) wraps every case that exercises a read operation: a filesystem,
process and network diff around the call, plus the port's own reach where B3-C17 names one, and
any write outside that operation's `INCIDENTAL_WRITES` entry (V-5.1 (c)) fails the case. The
reach a port needs is supplied by the implementation factory (`Implementation.reach`), never
chosen by the suite:

- `ResourceReads`: the engine inventory of containers, images, volumes and networks;
- `ToolchainResolver`, `HostScopeReads`, `ComposeResolver`: a hash of the envelope tree outside
  the operation's `INCIDENTAL_WRITES`;
- `GrantReads`: the stub issuer's generation.

A port whose required reach is not supplied fails its cases (`ReachMissing`): a read facet with
no reach would pass vacuously.

Executor-chosen values (the contract names none): the network diff records `bind`, `listen` and
`connect` on this process's sockets; a bind or a listen is a mutation, a connect fails only to a
non-loopback, non-unix address; the process diff is this process's ppid-descendants (MC-13
ancestry snapshot) alive after the call and not before; the filesystem diff hashes file contents.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import socket
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.proof import ancestry
from trestle.workflow import ports

ENVELOPE = "${ENVELOPE}"
CONSUMER_EPHEMERAL = "${CONSUMER_EPHEMERAL}"

# Read protocols and the reach B3-C17 names for them (item (3), "Watcher reach per port").
REACH_KINDS: Mapping[str, str] = {
    "ResourceReads": "engine_inventory",
    "ToolchainResolver": "envelope",
    "HostScopeReads": "envelope",
    "ComposeResolver": "envelope",
    "GrantReads": "issuer_generation",
}


class SuiteFailure(AssertionError):
    """One or more cases failed (each named, with its cause)."""


class ReadMutation(AssertionError):
    """A read call changed something outside V-5.1's permitted set."""


class ReachMissing(AssertionError):
    """The implementation supplied no reach for a port whose reach B3-C17 names."""


class SuiteModified(AssertionError):
    """The suite file's bytes differ from the ones registered: it is no longer the same suite."""


# ------------------------------------------------------------------------------- shapes


@dataclass(frozen=True)
class Reach:
    """What the watcher may look at around a read call, supplied by the implementation."""

    fs_roots: tuple[Path, ...] = ()
    envelope: Path | None = None  # ${ENVELOPE}: tree hashed outside INCIDENTAL_WRITES
    consumer_ephemeral: Path | None = None  # ${CONSUMER_EPHEMERAL}
    engine_inventory: Callable[[], Mapping[str, frozenset[str]]] | None = None
    issuer_generation: Callable[[], str] | None = None

    def has(self, kind: str) -> bool:
        return getattr(self, kind) is not None


@dataclass(frozen=True)
class Implementation:
    """What an implementation factory returns. `extras` is the family's fixture contract: the
    named commands, specs and hooks a family's cases ask for (see `families.py`); a real adapter
    supplies the same names with real commands and real instances."""

    impl: Any
    reach: Reach = field(default_factory=Reach)
    name: str = ""
    extras: Mapping[str, Any] = field(default_factory=dict)
    close: Callable[[], None] | None = None


@dataclass(frozen=True)
class Case:
    """One check. `operation` is the read operation it exercises (`"ResourceReads.observe"`) and
    is empty for every other kind of case; only a read operation is watched."""

    name: str
    body: Callable[[Implementation], None]
    operation: str = ""


@dataclass(frozen=True)
class Family:
    name: str
    cases: tuple[Case, ...]
    suite_file: Path
    suite_sha256: str


@dataclass(frozen=True)
class FamilyRun:
    """What one `run_family` call proves: the suite that ran and how many cases."""

    family: str
    implementation: str
    suite_sha256: str
    cases_run: tuple[str, ...]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Registry:
    def __init__(self) -> None:
        self.families: dict[str, Family] = {}
        self.runs: list[FamilyRun] = []

    def register_family(
        self, name: str, cases: Sequence[Case], *, suite_file: Path | None = None
    ) -> Family:
        """Register a family once. `suite_file` defaults to the file defining the first case's
        body; its sha256 is fixed here and re-checked at every run."""
        if name in self.families:
            raise ValueError(f"family {name!r} is already registered")
        if not cases:
            raise ValueError(f"family {name!r} has no cases")
        if len({c.name for c in cases}) != len(cases):
            raise ValueError(f"family {name!r} repeats a case name")
        path = suite_file or Path(inspect.getfile(cases[0].body))
        family = Family(name, tuple(cases), path, sha256_of(path))
        self.families[name] = family
        return family

    def run_family(self, name: str, impl_factory: Callable[[], Implementation]) -> FamilyRun:
        """Every case of `name` against a fresh implementation each; read cases under the
        watcher. Raises `SuiteModified` before running if the suite file changed, and
        `SuiteFailure` after running every case if any failed."""
        family = self.families[name]
        current = sha256_of(family.suite_file)
        if current != family.suite_sha256:
            raise SuiteModified(
                f"{family.suite_file} is {current}, registered {family.suite_sha256}"
            )
        failures: list[str] = []
        implementation = ""
        for case in family.cases:
            built = impl_factory()
            implementation = built.name or type(built.impl).__name__
            try:
                if case.operation:
                    with watch(case.operation, built.reach):
                        case.body(built)
                else:
                    case.body(built)
            except (ReachMissing, SuiteModified):
                raise
            except Exception as exc:  # noqa: BLE001 - every failure is reported with its case
                failures.append(f"{case.name}: {type(exc).__name__}: {exc}")
            finally:
                if built.close is not None:
                    built.close()
        run = FamilyRun(
            name, implementation, family.suite_sha256, tuple(c.name for c in family.cases)
        )
        self.runs.append(run)
        if failures:
            raise SuiteFailure(f"{name} against {implementation}: " + "; ".join(failures))
        return run


REGISTRY = Registry()


def register_family(name: str, cases: Sequence[Case], *, suite_file: Path | None = None) -> Family:
    return REGISTRY.register_family(name, cases, suite_file=suite_file)


def run_family(name: str, impl_factory: Callable[[], Implementation]) -> FamilyRun:
    return REGISTRY.run_family(name, impl_factory)


# ------------------------------------------------------------------------------- static checks

_EFFECT_PREFIXES = (
    "set_",
    "put_",
    "write_",
    "delete_",
    "remove_",
    "create_",
    "update_",
    "start_",
    "stop_",
    "restart_",
    "refresh_",
    "install_",
    "deliver_",
    "run_",
)
_EFFECT_VERBS = frozenset(
    {"create", "start", "stop", "restart", "recreate", "refresh", "install", "deliver", "run"}
)


def read_protocol_violations(protocol: type) -> list[str]:
    """Why `protocol` is not a read facet, if it is not (B3-I2): an effect marker in its bases, a
    `release_descriptor`, a `ticket` parameter, or a member named like an effect (`set_*`, ...)."""
    problems: list[str] = []
    if ports.ReadFacet not in protocol.__mro__:
        problems.append("does not derive from ReadFacet")
    for marker in (
        ports.EffectFacet,
        ports.CreateFacet,
        ports.OwnedEffectFacet,
        ports.SafeStartFacet,
        ports.EventFacet,
    ):
        if marker in protocol.__mro__:
            problems.append(f"derives from {marker.__name__}")
    for cls in protocol.__mro__:
        for name, member in vars(cls).items():
            if name.startswith("_") or not callable(member):
                continue
            if name == "release_descriptor":
                problems.append(f"{cls.__name__}.{name} is an effect hook")
            if name in _EFFECT_VERBS or name.startswith(_EFFECT_PREFIXES):
                problems.append(f"{cls.__name__}.{name} is named like an effect")
            if "ticket" in inspect.signature(member).parameters:
                problems.append(f"{cls.__name__}.{name} takes a ticket")
    return problems


# ------------------------------------------------------------------------------- the watcher


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def allowed_patterns(operation: str, reach: Reach) -> tuple[re.Pattern[str], ...]:
    """`INCIDENTAL_WRITES[operation]` expanded against the reach's declared roots; a root the
    implementation did not declare leaves its patterns out (nothing there is allowed)."""
    roots = {ENVELOPE: reach.envelope, CONSUMER_EPHEMERAL: reach.consumer_ephemeral}
    patterns: list[re.Pattern[str]] = []
    for pattern in ports.INCIDENTAL_WRITES.get(operation, frozenset()):
        for token, root in roots.items():
            if pattern.startswith(token) and root is not None:
                patterns.append(_pattern_regex(str(root) + pattern[len(token) :]))
    return tuple(patterns)


def tree_state(root: Path) -> dict[str, str]:
    """Absolute path -> `dir` or `<size>:<sha256>` for everything under `root`."""
    state: dict[str, str] = {}
    if not root.exists():
        return state
    for base, dirs, files in os.walk(root):
        for name in dirs:
            state[str(Path(base) / name)] = "dir"
        for name in files:
            path = Path(base) / name
            try:
                data = path.read_bytes()
            except OSError:
                state[str(path)] = "unreadable"
                continue
            state[str(path)] = f"{len(data)}:{hashlib.sha256(data).hexdigest()}"
    return state


def _changed(before: Mapping[str, str], after: Mapping[str, str]) -> set[str]:
    return {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}


def _descendants() -> set[tuple[int, int | None]]:
    """This process's ppid-descendants (MC-13 ancestry snapshot), as (pid, start). Not the wider
    attribution by process group or session: a sibling in this shell's session is not the read's."""
    everything = ancestry.snapshot()
    by_parent: dict[int, list[ancestry.ProcInfo]] = {}
    for proc in everything:
        by_parent.setdefault(proc.ppid, []).append(proc)
    found: set[tuple[int, int | None]] = set()
    pending = [os.getpid()]
    while pending:
        for child in by_parent.get(pending.pop(), []):
            # the snapshot's own `ps` child is a descendant while it runs: the watcher, not the read
            if ancestry.PS_FIELDS in child.argv or (child.pid, child.start) in found:
                continue
            found.add((child.pid, child.start))
            pending.append(child.pid)
    return found


class _NetworkLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def violations(self) -> list[str]:
        out: list[str] = []
        for kind, address in self.events:
            if kind in ("bind", "listen"):
                out.append(f"network {kind} {address!r}")
            elif kind == "connect" and not _is_local(address):
                out.append(f"network connect to non-local {address!r}")
        return out


def _is_local(address: Any) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX
        return True
    if isinstance(address, tuple) and address:
        host = str(address[0])
        return host in ("localhost", "::1", "") or host.startswith("127.")
    return True


@contextmanager
def _network_watch() -> Iterator[_NetworkLog]:
    log = _NetworkLog()
    originals = {
        name: getattr(socket.socket, name) for name in ("bind", "listen", "connect", "connect_ex")
    }

    def wrap(kind: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def inner(self: socket.socket, *args: Any, **kwargs: Any) -> Any:
            log.events.append((kind, args[0] if args else None))
            return original(self, *args, **kwargs)

        return inner

    for name, kind in (
        ("bind", "bind"),
        ("listen", "listen"),
        ("connect", "connect"),
        ("connect_ex", "connect"),
    ):
        setattr(socket.socket, name, wrap(kind, originals[name]))
    try:
        yield log
    finally:
        for name, original in originals.items():
            setattr(socket.socket, name, original)


@contextmanager
def watch(operation: str, reach: Reach) -> Iterator[None]:
    """Run a read call under the filesystem, process and network watcher and the port's own reach
    (B3-C17 item (3)). Fails any change outside `INCIDENTAL_WRITES[operation]` (V-5.1 (c))."""
    protocol = operation.split(".", 1)[0]
    needed = REACH_KINDS.get(protocol)
    if needed is not None and not reach.has(needed):
        raise ReachMissing(f"{operation}: the implementation supplied no {needed} (B3-C17)")
    roots = [*reach.fs_roots, *(r for r in (reach.envelope, reach.consumer_ephemeral) if r)]
    fs_before = {str(r): tree_state(r) for r in roots}
    procs_before = _descendants()
    inventory_before = reach.engine_inventory() if reach.engine_inventory else None
    generation_before = reach.issuer_generation() if reach.issuer_generation else None
    with _network_watch() as net:
        yield
    problems: list[str] = []
    allowed = allowed_patterns(operation, reach)
    for root in roots:
        after_state = tree_state(root)
        for path in sorted(_changed(fs_before[str(root)], after_state)):
            is_dir = "dir" in (after_state.get(path), fs_before[str(root)].get(path))
            # a directory on the way into an allowed region (`${ENVELOPE}/cache`) is allowed
            candidates = (path, path + "/") if is_dir else (path,)
            if not any(p.match(c) for p in allowed for c in candidates):
                problems.append(f"filesystem change outside INCIDENTAL_WRITES: {path}")
    for pid, _start in sorted(_descendants() - procs_before, key=lambda k: k[0]):
        problems.append(f"process left running: pid {pid}")
    problems.extend(net.violations())
    if reach.engine_inventory is not None and inventory_before is not None:
        after = reach.engine_inventory()
        for kind in sorted(inventory_before.keys() | after.keys()):
            if inventory_before.get(kind, frozenset()) != after.get(kind, frozenset()):
                problems.append(f"engine inventory changed: {kind}")
    if reach.issuer_generation is not None and reach.issuer_generation() != generation_before:
        problems.append("stub issuer generation changed")
    if problems:
        raise ReadMutation(f"{operation}: " + "; ".join(problems))
