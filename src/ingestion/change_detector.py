from __future__ import annotations

from typing import Any, Mapping

from .hashing import payload_hash


def asset_key(payload: Mapping[str, Any], key_fields: tuple[str, ...]) -> str:
    values = [payload.get(field) for field in key_fields]
    if key_fields and all(value is not None for value in values):
        return "|".join(str(value) for value in values)
    return payload_hash(payload)


def detect_change(previous_hash: str | None, current_hash: str) -> str:
    if previous_hash is None:
        return "NEW"
    if previous_hash == current_hash:
        return "UNCHANGED"
    return "UPDATED"