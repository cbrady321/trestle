"""A seeded synthetic catalog for the scale case (L.RB-1.3; WR-ENV-12, MC-B-09).

`generate(n, seed)` returns a `Synthetic`: the catalog document (`Catalog.from_data` input) of `n`
services and the JSON-syntax Compose definition (the `FakeComposeResolver` input) that gives them
`depends_on` edges. The same `(n, seed)` always yields byte-identical documents; nothing here reads
a clock or the environment.

Shape: services `svc-0000 ..` in groups of `GROUP`; inside a group each service depends on up to
`FAN_IN` earlier services of the same group, drawn by the seeded generator, so a closure never
leaves the group of what was selected (its size is bounded by the selection, not by `n`).
`select(n, count, seed)` picks `count` services from the first `SELECT_POOL` services only, so a
request made against `n` services is also a valid request against `2 n` (the doubling case keeps
the same request and the same closure and doubles only the catalog).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

GROUP = 10
FAN_IN = 2
SELECT_POOL = 500
PROJECT = "scale"


@dataclass(frozen=True)
class Synthetic:
    n: int
    seed: int
    catalog: dict  # Catalog.from_data input
    compose: dict  # a JSON-syntax Compose definition

    def catalog_text(self) -> str:
        return json.dumps(self.catalog, sort_keys=True)

    def compose_text(self) -> str:
        return json.dumps(self.compose, sort_keys=True)

    def write_compose(self, directory: Path) -> Path:
        path = directory / f"scale-{self.n}-{self.seed}.json"
        path.write_text(self.compose_text(), encoding="utf-8")
        return path


def service_id(index: int) -> str:
    return f"svc-{index:04d}"


def generate(n: int, seed: int) -> Synthetic:
    rng = random.Random(seed)
    services = []
    definition: dict[str, dict] = {}
    for index in range(n):
        name = service_id(index)
        start = index - index % GROUP
        earlier = [service_id(i) for i in range(start, index)]
        deps = sorted(rng.sample(earlier, min(FAN_IN, len(earlier))))
        services.append({"id": name, "selector": name})
        definition[name] = {"image": "${TRESTLE_IMAGE_ALPINE}", "depends_on": deps}
    catalog = {
        "schema": 1,
        "env_key": "env",
        "services": services,
        "projects": [],
        "tests": [],
        "overrides": [],
    }
    return Synthetic(n, seed, catalog, {"name": PROJECT, "services": definition})


def select(count: int, seed: int) -> list[str]:
    """`count` service ids drawn by the seeded generator from the first `SELECT_POOL` services."""
    rng = random.Random(seed)
    return sorted(service_id(i) for i in rng.sample(range(SELECT_POOL), count))
