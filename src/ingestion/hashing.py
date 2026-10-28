from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_payload(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def calculate_payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def payload_hash(payload: Mapping[str, Any]) -> str:
    """Alias de compatibilidade para o hash canônico do payload."""
    return calculate_payload_hash(payload)