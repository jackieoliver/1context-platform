from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from onectx.io_utils import atomic_write_json, exclusive_file_lock, read_json_object


@dataclass
class CursorStore:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)
    dirty_keys: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "CursorStore":
        if not path.exists():
            return cls(path=path, data={"version": "0.1", "cursors": {}})
        payload = read_json_object(path)
        if payload is None:
            return cls(path=path, data={"version": "0.1", "cursors": {}, "corrupt_cursor_path": str(path)})
        payload.setdefault("version", "0.1")
        payload.setdefault("cursors", {})
        return cls(path=path, data=payload)

    def get(self, key: str) -> dict[str, Any]:
        cursors = self.data.setdefault("cursors", {})
        value = cursors.get(key, {})
        return value if isinstance(value, dict) else {}

    def set(self, key: str, value: dict[str, Any]) -> None:
        cursors = self.data.setdefault("cursors", {})
        cursors[key] = value
        self.dirty_keys.add(key)

    def save(self) -> None:
        if not self.dirty_keys:
            return
        with exclusive_file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            merged = read_json_object(self.path) or {"version": "0.1", "cursors": {}}
            merged.setdefault("version", "0.1")
            merged_cursors = merged.setdefault("cursors", {})
            if not isinstance(merged_cursors, dict):
                merged_cursors = {}
                merged["cursors"] = merged_cursors
            current_cursors = self.data.get("cursors", {})
            if isinstance(current_cursors, dict):
                for key in self.dirty_keys:
                    existing = merged_cursors.get(key)
                    value = current_cursors.get(key)
                    if isinstance(value, dict):
                        merged_cursors[key] = merge_cursor_entry(existing, value)
            for key, value in self.data.items():
                if key in {"version", "cursors"}:
                    continue
                merged[key] = value
            self.data = merged
            atomic_write_json(self.path, self.data)
            self.dirty_keys.clear()

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "cursor_count": len(self.data.get("cursors", {})),
        }


def merge_cursor_entry(existing: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, dict):
        return dict(incoming)
    incoming_offset = as_int(incoming.get("offset"))
    existing_offset = as_int(existing.get("offset"))
    incoming_path = str(incoming.get("path") or "")
    existing_path = str(existing.get("path") or "")
    if existing_offset > incoming_offset and (not incoming_path or existing_path == incoming_path):
        incoming_mtime_ns = as_int(incoming.get("mtime_ns"))
        existing_mtime_ns = as_int(existing.get("mtime_ns"))
        if incoming_mtime_ns and (not existing_mtime_ns or incoming_mtime_ns > existing_mtime_ns):
            return dict(incoming)
        if cursor_identity_changed(existing, incoming):
            return dict(incoming)
        return dict(existing)
    return dict(incoming)


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def cursor_identity_changed(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    existing_dev = as_int(existing.get("dev"))
    existing_ino = as_int(existing.get("ino"))
    incoming_dev = as_int(incoming.get("dev"))
    incoming_ino = as_int(incoming.get("ino"))
    if not all((existing_dev, existing_ino, incoming_dev, incoming_ino)):
        return False
    return (existing_dev, existing_ino) != (incoming_dev, incoming_ino)
