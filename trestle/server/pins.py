"""Pin store — $TRESTLE_HOME/pins.json (R-ART-14)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from trestle.common.fsutil import atomic_write_json


@dataclass
class PinStore:
    home: Path
    pins: set[str] = field(default_factory=set)

    @classmethod
    def open(cls, home: Path) -> PinStore:
        path = home / "pins.json"
        if not path.exists():
            return cls(home=home, pins=set())
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("pins", [])
        return cls(home=home, pins=set(str(item) for item in raw))

    def save(self) -> None:
        atomic_write_json(self.home / "pins.json", {"pins": sorted(self.pins)})

    def pin(self, target: str) -> bool:
        before = len(self.pins)
        self.pins.add(target)
        if len(self.pins) != before:
            self.save()
        return True

    def unpin(self, target: str) -> bool:
        if target in self.pins:
            self.pins.remove(target)
            self.save()
        return True

    def is_pinned(self, target: str) -> bool:
        return target in self.pins
