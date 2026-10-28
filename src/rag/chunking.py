from __future__ import annotations

import re

from .documents import Chunk, Document


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def chunk_text(
    text: str,
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size deve ser maior que zero")

    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError(
            "chunk_overlap deve ser menor que chunk_size"
        )

    text = clean_text(text)

    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = end - chunk_overlap

    return chunks


def chunk_documents(
    documents: list[Document],
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> list[Chunk]:

    chunks = []

    for document in documents:
        parts = chunk_text(
            document.text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        for index, text in enumerate(parts):
            chunk_id = (
                f"{document.document_id}:{index}"
            )

            metadata = dict(document.metadata)
            metadata["document_id"] = document.document_id
            metadata["chunk_index"] = index

            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    text=text,
                    chunk_index=index,
                    metadata=metadata,
                )
            )

    return chunks