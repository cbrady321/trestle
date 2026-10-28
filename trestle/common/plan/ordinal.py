"""Precedence ordinal, release rank and not-started closure over a plan's vertices (L.SV-3.3).

Pure functions over paths, children and dependency edges (stdlib only; `compiler` builds on them).
Three definitions live here, once:

* **Canonical occurrence.** Both walks run depth-first in declaration order and place a vertex at
  its first occurrence only, skipping later references to a node shared by two parents.
* **Precedence ordinal** (design S-8; the key B4-C4 relies on). A leaf's *leaf key* is its position
  among the leaves the pre-order walk places; a composite's is the number of leaves placed before
  it is entered, which is the leaf key of the first leaf the walk places inside its subtree (a
  shared leaf already placed does not count) or, with none, of the next leaf placed after it (one
  past the last if none follows). Vertices are ordered by (leaf key, post-order position) and
  numbered densely from 0, so on an equal leaf key a descendant ranks before its composite and no
  two vertices tie. Wrapping a node in a composite that keeps its position and edges adds no leaf
  and no earlier post-order exit, so no existing vertex's relative order changes.
* **Release rank** (V-4.4). A vertex's rank is the length of the longest chain of vertices that
  must precede it: an edge `dependency -> dependent` between siblings applies to every vertex of
  the two subtrees (`dependent`'s whole subtree waits for `dependency`'s). Releasers go in
  descending rank; a leaf root has rank 0.

**Not-started closure** (B1-O7, OQ-33): the vertices that can never start because a node in the
seed set did not converge are exactly the seeds' dependents (transitively, subtrees included). It
never contains a seed's dependencies, ancestors, descendants or independent siblings.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field


class Cycle(Exception):
    """A containment cycle: `path` is listed as its own descendant."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


@dataclass(slots=True)
class Placement:
    """One canonical-occurrence depth-first walk."""

    order: list[str] = field(default_factory=list)  # pre-order
    post: dict[str, int] = field(default_factory=dict)  # post-order position
    entry: dict[str, int] = field(default_factory=dict)  # leaves placed before entering (leaf key)


def place(
    root: str, kids: Callable[[str], Iterable[str]], is_leaf: Callable[[str], bool]
) -> Placement:
    """Walk from `root` over canonical occurrences. Raises `Cycle` on a containment cycle."""
    walk = Placement()
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
                raise Cycle(kid)
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


def precedence_ordinals(walk: Placement) -> dict[str, int]:
    """One dense integer per placed vertex, ordered by (leaf key, post-order position)."""
    keyed = sorted(walk.order, key=lambda p: (walk.entry[p], walk.post[p]))
    return {path: i for i, path in enumerate(keyed)}


def toposort(
    nodes: Sequence[str], edges: Iterable[tuple[str, str]]
) -> tuple[dict[str, int], str | None]:
    """Longest-chain depth of every node over `(before, after)` edges, or a node on a cycle."""
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


def subtrees(walk: Placement, kids: Callable[[str], Iterable[str]]) -> dict[str, set[str]]:
    """Every vertex of each vertex's subtree (itself included), shared nodes under each parent."""
    out: dict[str, set[str]] = {}
    for path in sorted(walk.order, key=lambda p: walk.post[p]):  # children exit first
        acc = {path}
        for kid in kids(path):
            acc |= out[kid]
        out[path] = acc
    return out


def expand(edges: Iterable[tuple[str, str]], sub: Mapping[str, set[str]]) -> set[tuple[str, str]]:
    """Sibling edges lifted to vertices: every vertex of the dependent's subtree waits for every
    vertex of the dependency's subtree."""
    return {
        (u, v) for dependency, dependent in edges for u in sub[dependency] for v in sub[dependent]
    }


def release_ranks(
    walk: Placement,
    kids: Callable[[str], Iterable[str]],
    edges: Iterable[tuple[str, str]],
) -> tuple[dict[str, int], str | None]:
    """Release rank per placed vertex, or a vertex on a dependency cycle (then the ranks are
    meaningless)."""
    lifted = expand(edges, subtrees(walk, kids))
    return toposort(walk.order, lifted)


def not_started_closure(
    root: str,
    kids: Mapping[str, Sequence[str]],
    edges: Iterable[tuple[str, str]],
    seeds: Iterable[str],
) -> frozenset[str]:
    """The seeds' transitive dependents (subtrees included), the seeds themselves excluded."""
    walk = place(root, lambda p: kids[p], lambda p: not kids[p])
    lifted = expand(edges, subtrees(walk, lambda p: kids[p]))
    succ: dict[str, set[str]] = {}
    for u, v in lifted:
        succ.setdefault(u, set()).add(v)
    seed_set = set(seeds)
    reached: set[str] = set()
    queue = deque(seed_set)
    while queue:
        for nxt in succ.get(queue.popleft(), ()):
            if nxt not in reached:
                reached.add(nxt)
                queue.append(nxt)
    return frozenset(reached - seed_set)
