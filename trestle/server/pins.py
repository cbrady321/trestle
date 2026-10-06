"""Pin store — $TRESTLE_HOME/pins.json (R-ART-14)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
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
        with self._locked():
            before = len(self.pins)
            self.pins.add(target)
            if len(self.pins) != before:
                self.save()
        return True

    def unpin(self, target: str) -> bool:
        with self._locked():
            if target in self.pins:
                self.pins.remove(target)
                self.save()
        return True

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """v0.4: pins.json is changed only under `locks/pins.lock`, re-read inside it, so two
        servers' pins never overwrite each other."""
        from trestle.server.home import PINS_LOCK, file_lock, locks_dir

        with file_lock(locks_dir(self.home) / PINS_LOCK):
            self.pins = PinStore.open(self.home).pins
            yield

    def is_pinned(self, target: str) -> bool:
        return target in self.pins
