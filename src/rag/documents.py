from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    document_id: str
    text: str
    source: str
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)