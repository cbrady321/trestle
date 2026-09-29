"""The pure plan compiler (MC-23; L.SV-3.1).

`compile(declared, request, limits)` turns a `DeclaredTree` (MC-34) and the request's already
schema-validated arguments into an `AdmittedPlan` (MC-20) or one `Refusal(code, identifier)`. It
performs the plan-side checks of B2-C2 in B2-C2's order and does no I/O and no clock read, so the
same inputs always give the same plan or the same refusal, and it never raises on a well-formed
`DeclaredTree`.

Order of the checks (the first that fails is the refusal):

* tree integrity of the declaration (V-11 names them; A-2 reaches them at admission): a reference
  to a node the declaration does not resolve, a `needs` / `gates` / `fallback` naming nothing
  (`UNIT_UNRESOLVED`), one logical name bound to two nodes in one composite
  (`DECLARATION_CONFLICT`), a containment or dependency cycle (`DEPENDENCY_CYCLE`);
* the selected scope (the argument-filtered children plus their needs closure, and the eligible
  alternatives of each choice) is computed with request values the identifier check has not yet
  vouched for ignored;
* (0) `|selected_scope|` <= `vertex_max` (V-13 `VERTEX_MAX`) -> `BOUND_EXCEEDED`;
* (1) every value at every `ArgBinding.arg` is a member of its identifier set ->
  `UNKNOWN_IDENTIFIER` naming the value and where the valid ones are listed;
* (2) every `CHOICE` in scope has a non-empty eligible set (`select_arg` restricts eligibility)
  -> `ROUTE_UNSUPPORTED` (V-11: "no eligible alternative");
* (3) route feasibility: for every needs edge into a `CHOICE`, an eligible alternative declares
  the dependent's vantage (every forced one when `select_arg` forced the set) ->
  `ROUTE_UNSUPPORTED`;
* (4)-(5) budgets and the finalization margin are L.SV-3.2's carve;
* (6) an `env_key_field` on the root has a request value, and every node in scope that declares
  an environment projects to the same key -> `LEASE_SET_UNDECIDABLE`;
* (7) after argument references in bound parameters are resolved against the request, no two
  references to one logical node carry different parameters -> `DECLARATION_CONFLICT`.

Then the plan's release ranks (V-4.4) and precedence ordinals (design S-8) are computed; a needs
cycle only visible once subtrees are expanded is a `DEPENDENCY_CYCLE` too.

Imports the standard library and `trestle.common.plan` only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, final

from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.declared import ROOT_PATH, DeclaredTree, canonical_json

VERTEX_MAX = 1024  # V-13 (provisional): |PlanAccepted.selected_scope|; owner B2-C2
REFUSAL_TEXT_MAX = 200  # V-13: `PlanRefused.subject` / `message` / `valid_listed_at`
PLAN_FORMAT = 1

_ROOT_NAME = "<root>"


@final
@dataclass(frozen=True, slots=True)
class CompileLimits:
    """The operator limits `compile` reads: today only the V-13 vertex bound."""

    vertex_max: int = VERTEX_MAX


@final
@dataclass(frozen=True, slots=True)
class Refusal:
    """`PlanRefused`: a `vocabulary` plan code, the identifier it names, and where the valid
    identifiers are listed (for `UNKNOWN_IDENTIFIER`)."""

    code: str
    identifier: str
    valid_listed_at: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", self.identifier[:REFUSAL_TEXT_MAX])
        object.__setattr__(self, "message", self.message[:REFUSAL_TEXT_MAX])
        if self.valid_listed_at is not None:
            object.__setattr__(self, "valid_listed_at", self.valid_listed_at[:REFUSAL_TEXT_MAX])


@final
@dataclass(frozen=True, slots=True)
class Vertex:
    """One logical node of the admitted plan (V-1.2), at its canonical path.

    `children` are the selected children in declaration order (a `choice` vertex: its eligible
    alternatives); `needs` are the canonical paths of the sibling nodes this vertex depends on;
    `create_run` lists the CREATE + RUN effects a leaf declares as `(effect, release_timeout_s)`
    (one sweep target each, B2-C2 (5)).
    """

    path: str
    unit: str
    compose: str
    budget_s: float | None
    children: tuple[str, ...]
    needs: tuple[str, ...]
    concurrency: int
    create_run: tuple[tuple[str, float | None], ...]
    vantage: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "unit": self.unit,
            "compose": self.compose,
            "budget_s": self.budget_s,
            "children": list(self.children),
            "needs": list(self.needs),
            "concurrency": self.concurrency,
            "create_run": [[effect, timeout] for effect, timeout in self.create_run],
            "vantage": self.vantage,
        }


@final
@dataclass(frozen=True, slots=True)
class AdmittedPlan:
    """MC-20: the compiled plan.

    `vertices` is the selected scope in canonical (first-occurrence depth-first) order, root
    first; `edges` are `(dependency, dependent)` path pairs; `eligible` maps each `choice`
    vertex to its eligible alternatives; `slices` and `release_slice` are attached by the carve
    (L.SV-3.2 / L.SV-3.4; empty and 0.0 as `compile` returns them); `release_rank` and
    `precedence_ordinal` are per vertex; `lease_set` is `()` (the root declares no environment)
    or the one canonical-JSON environment key; `declaration_digest` is the `DeclaredTree.digest`
    compiled from; `plan_digest` is the sha256 of the canonical JSON of every other field.
    """

    format_version: int
    declaration_digest: str | None
    root: str
    vertices: tuple[Vertex, ...]
    edges: tuple[tuple[str, str], ...]
    eligible: Mapping[str, tuple[str, ...]]
    slices: Mapping[str, float]
    release_slice: float
    lease_set: tuple[str, ...]
    release_rank: Mapping[str, int]
    precedence_ordinal: Mapping[str, int]
    plan_digest: str

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(v.path for v in self.vertices)

    def vertex(self, path: str) -> Vertex:
        for v in self.vertices:
            if v.path == path:
                return v
        raise KeyError(path)

    def body(self) -> dict[str, Any]:
        """Every field but the digest, as canonical-JSON-able data."""
        return {
            "format_version": self.format_version,
            "declaration_digest": self.declaration_digest,
            "root": self.root,
            "vertices": [v.to_dict() for v in self.vertices],
            "edges": [list(e) for e in self.edges],
            "eligible": {p: list(alts) for p, alts in self.eligible.items()},
            "slices": dict(self.slices),
            "release_slice": self.release_slice,
            "lease_set": list(self.lease_set),
            "release_rank": dict(self.release_rank),
            "precedence_ordinal": dict(self.precedence_ordinal),
        }


def _digest_of(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


# ---- request access

_ABSENT: Any = object()


def _lookup(request: Mapping[str, Any], dotted: str) -> Any:
    """The value at a dotted path into the request arguments, or `_ABSENT`."""
    cur: Any = request
    for part in dotted.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return _ABSENT
    return cur


def _values(value: Any) -> list[Any]:
    """The identifiers a request value names: one string, or each element of a list."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _text(path: str) -> str:
    return path if path != ROOT_PATH else _ROOT_NAME


# ---- references (one per child binding / alternative)


@dataclass(frozen=True, slots=True)
class _Ref:
    parent: str
    name: str
    unit: str
    path: str | None
    params: Mapping[str, Any]
    needs: tuple[str, ...]
    vantage: str


def _refs(path: str, node: Mapping[str, Any]) -> list[_Ref]:
    compose = node["compose"]
    if compose == "all":
        out = []
        for child in node["children"]:
            binding = child["binding"]
            out.append(
                _Ref(
                    path,
                    child["name"],
                    binding["unit"],
                    child["path"],
                    binding.get("params", {}),
                    tuple(binding.get("needs", ())),
                    binding.get("vantage", "host"),
                )
            )
        return out
    if compose == "choice":
        return [
            _Ref(path, alt["unit"], alt["unit"], alt["path"], {}, (), "host")
            for alt in node["choice"]["alternatives"]
        ]
    return []


class _Cycle(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


@dataclass(slots=True)
class _Walk:
    order: list[str] = field(default_factory=list)
    post: dict[str, int] = field(default_factory=dict)
    entry: dict[str, int] = field(default_factory=dict)  # leaves placed before entering


def _walk(root: str, kids: Callable[[str], Iterable[str]], is_leaf: Callable[[str], bool]) -> _Walk:
    """Depth-first over canonical occurrences only: a node reached again through a second parent
    is skipped (its position is its first occurrence). Raises `_Cycle` on a containment cycle."""
    walk = _Walk()
    leaves = 0
    posn = 0
    entered = {root}
    on_stack = {root}
    walk.order.append(root)
    walk.entry[root] = leaves
    if is_leaf(root):
        leaves += 1
    stack = [(root, iter(kids(root)))]
    while stack:
        path, it = stack[-1]
        for kid in it:
            if kid in on_stack:
                raise _Cycle(kid)
            if kid in entered:
                continue
            entered.add(kid)
            on_stack.add(kid)
            walk.order.append(kid)
            walk.entry[kid] = leaves
            if is_leaf(kid):
                leaves += 1
            stack.append((kid, iter(kids(kid))))
            break
        else:
            stack.pop()
            on_stack.discard(path)
            walk.post[path] = posn
            posn += 1
    return walk


def _toposort(
    nodes: Sequence[str], edges: Iterable[tuple[str, str]]
) -> tuple[dict[str, int], str | None]:
    """Longest-path depth of every node over `(before, after)` edges, or a node on a cycle."""
    succ: dict[str, set[str]] = {n: set() for n in nodes}
    pred: dict[str, set[str]] = {n: set() for n in nodes}
    for a, b in edges:
        succ[a].add(b)
        pred[b].add(a)
    indeg = {n: len(pred[n]) for n in nodes}
    depth = {n: 0 for n in nodes}
    ready = sorted(n for n in nodes if indeg[n] == 0)
    done = 0
    while ready:
        node = ready.pop()
        done += 1
        for nxt in sorted(succ[node]):
            depth[nxt] = max(depth[nxt], depth[node] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    if done == len(nodes):
        return depth, None
    remaining = {n for n in nodes if indeg[n] > 0}
    node = min(remaining)
    seen: set[str] = set()
    while node not in seen:
        seen.add(node)
        node = min(p for p in pred[node] if p in remaining)
    return depth, node


# ---- tree integrity


def _integrity(nodes: Mapping[str, Mapping[str, Any]]) -> Refusal | None:
    """Refusals the declaration alone decides (no request involved)."""

    def kids(path: str) -> list[str]:
        return [r.path for r in _refs(path, nodes[path]) if r.path is not None]

    def is_leaf(path: str) -> bool:
        return bool(nodes[path]["compose"] == "leaf")

    try:
        walk = _walk(ROOT_PATH, kids, is_leaf)
    except _Cycle as cycle:
        return Refusal(vocab.DEPENDENCY_CYCLE, _text(cycle.path), message="containment cycle")
    edges: list[tuple[str, str]] = []
    for path in walk.order:
        node = nodes[path]
        refs = _refs(path, node)
        by_name: dict[str, _Ref] = {}
        for ref in refs:
            where = f"{_text(path)}/{ref.name}"
            if ref.path is None:
                return Refusal(vocab.UNIT_UNRESOLVED, where, message="child is not resolved")
            prior = by_name.get(ref.name)
            if prior is not None and prior.path != ref.path:
                return Refusal(vocab.DECLARATION_CONFLICT, where, message="name bound twice")
            by_name[ref.name] = ref
        if node["compose"] == "all":
            for ref in refs:
                if ref.path is None:
                    continue  # refused above
                for need in ref.needs:
                    sibling = by_name.get(need)
                    if sibling is None or sibling.path is None:
                        return Refusal(
                            vocab.UNIT_UNRESOLVED,
                            f"{_text(path)}/{need}",
                            message=f"{ref.name} needs a node its composite does not declare",
                        )
                    edges.append((sibling.path, ref.path))
            for gate in node["gates"]:
                if gate not in by_name:
                    return Refusal(
                        vocab.UNIT_UNRESOLVED,
                        f"{_text(path)}/{gate}",
                        message="a gates entry names none of the composite's children",
                    )
        elif node["compose"] == "choice":
            fallback = node["choice"]["fallback"]
            if fallback is not None and fallback not in {r.unit for r in refs}:
                return Refusal(
                    vocab.UNIT_UNRESOLVED,
                    f"{_text(path)}/{fallback}",
                    message="fallback is not one of the choice's alternatives",
                )
    _, cyclic = _toposort(walk.order, edges)
    if cyclic is not None:
        return Refusal(vocab.DEPENDENCY_CYCLE, _text(cyclic), message="needs cycle")
    return None


# ---- scope selection


def _select_refs(path: str, node: Mapping[str, Any], request: Mapping[str, Any]) -> list[_Ref]:
    """The selected references of one composite (B2-C2 (2))."""
    refs = _refs(path, node)
    if node["compose"] == "choice":
        select_arg = node["choice"]["select_arg"]
        if select_arg is None:
            return refs
        value = _lookup(request, select_arg)
        if value is _ABSENT:
            return refs
        named = set(_values(value))
        return [r for r in refs if r.unit in named]
    if node["compose"] != "all":
        return refs
    named_ids: set[Any] | None = None
    for binding in node["arg_bindings"]:
        if not binding["filters_children"]:
            continue
        value = _lookup(request, binding["arg"])
        if value is _ABSENT:
            continue
        named_ids = (named_ids or set()) | {v for v in _values(value) if isinstance(v, str)}
    if named_ids is None:
        return refs
    by_name: dict[str, _Ref] = {}
    for ref in refs:
        by_name.setdefault(ref.name, ref)
    chosen = {n for n in by_name if n in named_ids}
    work = list(chosen)
    while work:
        for need in by_name[work.pop()].needs:
            if need in by_name and need not in chosen:
                chosen.add(need)
                work.append(need)
    return [r for r in refs if r.name in chosen]


def _resolved_params(params: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    """Bound parameters with argument references replaced by the request's value. A string that
    is a dotted path present in the request is a reference; every other value is a literal."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str):
            found = _lookup(request, value)
            out[key] = value if found is _ABSENT else found
        else:
            out[key] = value
    return out


def compile(  # noqa: A001  (MC-23 names the entry point `compile`)
    declared: DeclaredTree,
    request: Mapping[str, Any],
    limits: CompileLimits | None = None,
) -> AdmittedPlan | Refusal:
    """Compile `declared` against `request`, or refuse (see the module docstring for the order)."""
    limits = limits or CompileLimits()
    nodes = declared.nodes
    refusal = _integrity(nodes)
    if refusal is not None:
        return refusal

    # -- scope: selected references per composite, over the occurrences reachable from the root
    selected: dict[str, list[_Ref]] = {}
    pending = [ROOT_PATH]
    while pending:
        path = pending.pop()
        if path in selected:
            continue
        refs = _select_refs(path, nodes[path], request)
        selected[path] = refs
        pending.extend(r.path for r in refs if r.path is not None and r.path not in selected)

    def kids(path: str) -> list[str]:
        return [r.path for r in selected[path] if r.path is not None]

    def is_leaf(path: str) -> bool:
        return bool(nodes[path]["compose"] == "leaf")

    walk = _walk(ROOT_PATH, kids, is_leaf)  # integrity already ruled cycles out
    scope = walk.order

    # (0)
    if len(scope) > limits.vertex_max:
        return Refusal(
            vocab.BOUND_EXCEEDED,
            "selected_scope",
            message=f"{len(scope)} vertices exceed {limits.vertex_max}",
        )

    # (1)
    for path in scope:
        node = nodes[path]
        if node["compose"] != "all":
            continue
        for binding in node["arg_bindings"]:
            value = _lookup(request, binding["arg"])
            if value is _ABSENT:
                continue
            members = set(node["identifier_sets"].get(binding["identifier_set"], ()))
            for item in _values(value):
                if not isinstance(item, str) or item not in members:
                    where = "identifier_sets." + binding["identifier_set"]
                    if path != ROOT_PATH:
                        where = f"{path}/{where}"
                    return Refusal(
                        vocab.UNKNOWN_IDENTIFIER,
                        item if isinstance(item, str) else repr(item),
                        valid_listed_at=where,
                        message=f"{binding['arg']} names an identifier outside the declaration",
                    )

    # (2)
    eligible: dict[str, tuple[str, ...]] = {}
    for path in scope:
        if nodes[path]["compose"] == "choice":
            alts = tuple(dict.fromkeys(k for k in kids(path)))
            if not alts:
                return Refusal(
                    vocab.ROUTE_UNSUPPORTED, _text(path), message="no eligible alternative"
                )
            eligible[path] = alts

    # (3)
    for path in scope:
        if nodes[path]["compose"] != "all":
            continue
        by_name = {r.name: r for r in selected[path]}
        for ref in selected[path]:
            for need in ref.needs:
                sibling = by_name.get(need)
                if sibling is None or sibling.path is None or sibling.path not in eligible:
                    continue
                choice = nodes[sibling.path]["choice"]
                declares = {
                    alt["path"]: ref.vantage in alt["reachable_from"]
                    for alt in choice["alternatives"]
                    if alt["path"] in eligible[sibling.path]
                }
                forced = choice["select_arg"] is not None and (
                    _lookup(request, choice["select_arg"]) is not _ABSENT
                )
                ok = all(declares.values()) if forced else any(declares.values())
                if not ok:
                    return Refusal(
                        vocab.ROUTE_UNSUPPORTED,
                        f"{_text(path)}/{ref.name}",
                        message=f"no eligible alternative of {need} is reachable from "
                        f"{ref.vantage}",
                    )

    # (6)
    lease_set: tuple[str, ...] = ()
    root_field = nodes[ROOT_PATH]["env_key_field"]
    if root_field is not None:
        root_value = _lookup(request, root_field)
        if root_value is _ABSENT or root_value is None:
            return Refusal(
                vocab.LEASE_SET_UNDECIDABLE, root_field, message="the request gives no value"
            )
        key = canonical_json(root_value)
        for path in scope:
            node_field = nodes[path]["env_key_field"]
            if node_field is None or node_field == root_field:
                continue
            node_value = _lookup(request, node_field)
            if node_value is _ABSENT or canonical_json(node_value) != key:
                return Refusal(
                    vocab.LEASE_SET_UNDECIDABLE,
                    _text(path),
                    message="declares an environment that does not project to the root's key",
                )
        lease_set = (key,)

    # (7)
    bound: dict[str, str] = {}
    for path in scope:
        for ref in selected[path]:
            if ref.path is None:
                continue
            params = canonical_json(_resolved_params(ref.params, request))
            prior = bound.setdefault(ref.path, params)
            if prior != params:
                return Refusal(
                    vocab.DECLARATION_CONFLICT,
                    _text(ref.path),
                    message="two references carry different parameters",
                )

    # -- edges, ranks, ordinals
    edges: set[tuple[str, str]] = set()
    needs_of: dict[str, set[str]] = {p: set() for p in scope}
    vantage_of: dict[str, str] = {ROOT_PATH: "host"}
    for path in scope:
        refs_here = selected[path]
        by_name = {r.name: r for r in refs_here}
        for ref in refs_here:
            if ref.path is None:
                continue
            vantage_of.setdefault(ref.path, ref.vantage)
            for need in ref.needs:
                sibling = by_name.get(need)
                if sibling is not None and sibling.path is not None:
                    edges.add((sibling.path, ref.path))
                    needs_of[ref.path].add(sibling.path)

    by_post = sorted(scope, key=lambda p: walk.post[p])
    subtree: dict[str, set[str]] = {}
    for path in by_post:
        acc = {path}
        for kid in kids(path):
            acc |= subtree[kid]
        subtree[path] = acc
    expanded: set[tuple[str, str]] = set()
    for dependency, dependent in edges:
        for before in subtree[dependency]:
            for after in subtree[dependent]:
                expanded.add((before, after))
    rank, cyclic = _toposort(scope, expanded)
    if cyclic is not None:
        return Refusal(vocab.DEPENDENCY_CYCLE, _text(cyclic), message="needs cycle in scope")

    keyed = sorted(scope, key=lambda p: (walk.entry[p], walk.post[p]))
    ordinal = {path: i for i, path in enumerate(keyed)}

    vertices = []
    for path in scope:
        node = nodes[path]
        effects: list[tuple[str, float | None]] = []
        if node["compose"] == "leaf":
            for effect in node["effects"]:
                if effect["facet"] == "create" and effect["lifetime"] == "run":
                    effects.append((effect["effect"], effect["release_timeout"]))
        vertices.append(
            Vertex(
                path=path,
                unit=node["unit"],
                compose=node["compose"],
                budget_s=node["budget"],
                children=tuple(dict.fromkeys(kids(path))),
                needs=tuple(sorted(needs_of[path])),
                concurrency=int(node["concurrency"]) if node["compose"] == "all" else 1,
                create_run=tuple(effects),
                vantage=vantage_of.get(path, "host"),
            )
        )
    sealed = AdmittedPlan(
        format_version=PLAN_FORMAT,
        declaration_digest=declared.digest,
        root=declared.root,
        vertices=tuple(vertices),
        edges=tuple(sorted(edges)),
        eligible=eligible,
        slices={},
        release_slice=0.0,
        lease_set=lease_set,
        release_rank={p: rank[p] for p in scope},
        precedence_ordinal=ordinal,
        plan_digest="",
    )
    return replace(sealed, plan_digest=_digest_of(sealed.body()))
