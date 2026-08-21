"""Result index metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_INDEX_BYTES = 64 * 1024


@dataclass
class Index:
    root_type: str
    byte_length: int
    fields: list[dict[str, Any]] | None = None
    array_count: int | None = None
    array_element_ranges: list[dict[str, Any]] | None = None
    index_truncated: bool = False
    field_count: int | None = None

    def to_json(self) -> bytes:
        payload: dict[str, Any] = {
            "root_type": self.root_type,
            "byte_length": self.byte_length,
            "index_truncated": self.index_truncated,
        }
        if self.fields is not None:
            payload["fields"] = self.fields
        if self.array_count is not None:
            payload["array_count"] = self.array_count
        if self.array_element_ranges is not None:
            payload["array_element_ranges"] = self.array_element_ranges
        if self.field_count is not None:
            payload["field_count"] = self.field_count
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> Index:
        obj = json.loads(data.decode("utf-8"))
        return cls(
            root_type=obj["root_type"],
            byte_length=obj["byte_length"],
            fields=obj.get("fields"),
            array_count=obj.get("array_count"),
            array_element_ranges=obj.get("array_element_ranges"),
            index_truncated=obj.get("index_truncated", False),
            field_count=obj.get("field_count"),
        )
