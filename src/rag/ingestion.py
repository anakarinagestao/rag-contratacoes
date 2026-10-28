from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


SUPPORTED_EXTENSIONS = {
    ".json",
    ".jsonl",
    ".pdf",
    ".html",
    ".htm",
    ".txt",
}


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


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError(f"Não foi possível ler o arquivo: {path}")


def _json_to_text(value: Any, prefix: str = "") -> str:
    lines: list[str] = []

    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)

            if isinstance(item, (dict, list)):
                nested = _json_to_text(item, current)

                if nested:
                    lines.append(nested)
            else:
                lines.append(f"{current}: {item}")

    elif isinstance(value, list):
        for index, item in enumerate(value):
            current = f"{prefix}[{index}]"

            if isinstance(item, (dict, list)):
                nested = _json_to_text(item, current)

                if nested:
                    lines.append(nested)
            else:
                lines.append(f"{current}: {item}")

    else:
        lines.append(f"{prefix}: {value}")

    return "\n".join(lines)


def _extract_html(text: str) -> str:
    text = re.sub(
        r"<(script|style|noscript).*?>.*?</\1>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(r"<[^>]+>", " ", text)

    return _clean_text(text)


def _load_pdf(path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError(
            "Para processar PDF, instale a dependência com: pip install pypdf"
        )

    reader = PdfReader(str(path))

    pages = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""

        if text.strip():
            pages.append(
                f"[Página {page_number}]\n{text}"
            )

    return "\n\n".join(pages)


def _document_id(
    source: str,
    text: str,
    metadata: dict[str, Any],
) -> str:
    raw_asset_id = metadata.get("raw_asset_id")

    if raw_asset_id:
        return _sha256(
            f"raw_asset:{raw_asset_id}"
        )

    return _sha256(
        f"{source}\n{text}"
    )


def document_from_raw_asset(row: Any) -> Document | None:
    """
    Converte um registro da tabela raw_asset em um Document.
    """

    try:
        payload = json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError):
        return None

    text = _clean_text(
        _json_to_text(payload)
    )

    if not text:
        return None

    try:
        query_params = json.loads(
            row["query_params"] or "{}"
        )
    except json.JSONDecodeError:
        query_params = {}

    metadata = {
        "raw_asset_id": row["id"],
        "source": row["source"],
        "base_id": row["base_id"],
        "natureza": row["natureza"],
        "resource_type": row["resource_type"],
        "endpoint": row["endpoint"],
        "external_id": row["external_id"],
        "query_params": query_params,
        "payload_hash": row["payload_hash"],
        "source_published_at": row["source_published_at"],
        "source_updated_at": row["source_updated_at"],
        "fetched_at": row["fetched_at"],
        "corpus_version": row["corpus_version"],
        "sensitivity": row["sensitivity"],
        "ingestion_run_id": row["ingestion_run_id"],
        "previous_version_id": row["previous_version_id"],
        "processing_status": row["processing_status"],
    }

    return Document(
        document_id=_document_id(
            row["source"],
            text,
            metadata,
        ),
        text=text,
        source=row["source"],
        content_type="application/json",
        metadata=metadata,
    )


def ingest_raw_assets(
    connection: Any,
    *,
    source: str = "pncp",
    resource_types: Sequence[str] | None = None,
    latest_only: bool = True,
) -> list[Document]:
    """
    Lê os dados existentes em raw_asset.
    """

    params: list[Any] = [source]

    conditions = [
        "source = ?"
    ]

    if resource_types:
        placeholders = ", ".join(
            "?" for _ in resource_types
        )

        conditions.append(
            f"resource_type IN ({placeholders})"
        )

        params.extend(resource_types)

    where = " AND ".join(conditions)

    if latest_only:
        query = f"""
            SELECT asset.*
            FROM raw_asset AS asset
            JOIN (
                SELECT
                    external_id,
                    MAX(rowid) AS latest_rowid
                FROM raw_asset
                WHERE {where}
                GROUP BY external_id
            ) AS latest
            ON latest.latest_rowid = asset.rowid
            ORDER BY asset.rowid
        """
    else:
        query = f"""
            SELECT *
            FROM raw_asset
            WHERE {where}
            ORDER BY rowid
        """

    rows = connection.execute(
        query,
        params,
    ).fetchall()

    documents: list[Document] = []

    for row in rows:
        document = document_from_raw_asset(row)

        if document is not None:
            documents.append(document)

    return documents


def document_from_file(
    path: str | Path,
    metadata: dict[str, Any] | None = None,
) -> list[Document]:
    """
    Processa arquivos externos.
    """

    path = Path(path)

    extension = path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Formato não suportado: {extension}"
        )

    documents: list[Document] = []

    if extension in {".json", ".jsonl"}:

        if extension == ".jsonl":
            records = []

            for line_number, line in enumerate(
                _read_text(path).splitlines(),
                start=1,
            ):
                if not line.strip():
                    continue

                records.append(
                    (
                        f"{path}#line={line_number}",
                        json.loads(line),
                    )
                )

        else:
            records = [
                (
                    str(path),
                    json.loads(
                        _read_text(path)
                    ),
                )
            ]

        for source, payload in records:

            text = _clean_text(
                _json_to_text(payload)
            )

            if not text:
                continue

            file_metadata = {
                **(metadata or {}),
                "file_name": path.name,
                "file_type": extension.lstrip("."),
            }

            documents.append(
                Document(
                    document_id=_document_id(
                        source,
                        text,
                        file_metadata,
                    ),
                    text=text,
                    source=source,
                    content_type="application/json",
                    metadata=file_metadata,
                )
            )

        return documents

    if extension == ".pdf":
        text = _clean_text(
            _load_pdf(path)
        )

        content_type = "application/pdf"

    elif extension in {".html", ".htm"}:
        text = _extract_html(
            _read_text(path)
        )

        content_type = "text/html"

    else:
        text = _clean_text(
            _read_text(path)
        )

        content_type = "text/plain"

    if not text:
        return []

    file_metadata = {
        **(metadata or {}),
        "file_name": path.name,
        "file_type": extension.lstrip("."),
    }

    documents.append(
        Document(
            document_id=_document_id(
                str(path),
                text,
                file_metadata,
            ),
            text=text,
            source=str(path),
            content_type=content_type,
            metadata=file_metadata,
        )
    )

    return documents


def ingest_path(
    path: str | Path,
    metadata: dict[str, Any] | None = None,
) -> list[Document]:
    """
    Processa um arquivo ou todos os arquivos suportados
    dentro de um diretório.
    """

    path = Path(path)

    if path.is_file():
        return document_from_file(
            path,
            metadata,
        )

    if not path.is_dir():
        raise FileNotFoundError(path)

    documents: list[Document] = []

    for file_path in sorted(
        path.rglob("*")
    ):
        if not file_path.is_file():
            continue

        if (
            file_path.suffix.lower()
            not in SUPPORTED_EXTENSIONS
        ):
            continue

        try:
            documents.extend(
                document_from_file(
                    file_path,
                    metadata,
                )
            )

        except Exception as exc:
            print(
                f"[ERRO] {file_path}: {exc}"
            )

    return documents


def chunk_text(
    text: str,
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> list[str]:
    """
    Divide o texto em chunks.

    Valores iniciais:
        chunk_size = 1200
        chunk_overlap = 200
    """

    if chunk_size <= 0:
        raise ValueError(
            "chunk_size deve ser maior que zero"
        )

    if (
        chunk_overlap < 0
        or chunk_overlap >= chunk_size
    ):
        raise ValueError(
            "chunk_overlap deve ser >= 0 "
            "e menor que chunk_size"
        )

    text = _clean_text(text)

    if not text:
        return []

    chunks: list[str] = []

    start = 0

    while start < len(text):

        end = min(
            start + chunk_size,
            len(text),
        )

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = end - chunk_overlap

    return chunks


def chunk_documents(
    documents: Iterable[Document],
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> list[Chunk]:
    """
    Cria chunks preservando os metadados do documento.
    """

    chunks: list[Chunk] = []

    for document in documents:

        parts = chunk_text(
            document.text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        for index, text in enumerate(parts):

            chunk_id = _sha256(
                f"{document.document_id}:"
                f"{index}:"
                f"{text}"
            )

            metadata = dict(
                document.metadata
            )

            metadata["document_id"] = (
                document.document_id
            )

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


def save_chunks_jsonl(
    chunks: Iterable[Chunk],
    output_path: str | Path,
) -> None:

    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:

        for chunk in chunks:

            file.write(
                json.dumps(
                    asdict(chunk),
                    ensure_ascii=False,
                )
                + "\n"
            )


def ingest_raw_to_jsonl(
    connection: Any,
    output_path: str | Path,
    *,
    source: str = "pncp",
    resource_types: Sequence[str] | None = None,
    latest_only: bool = True,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> tuple[int, int]:

    documents = ingest_raw_assets(
        connection,
        source=source,
        resource_types=resource_types,
        latest_only=latest_only,
    )

    chunks = chunk_documents(
        documents,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    save_chunks_jsonl(
        chunks,
        output_path,
    )

    return (
        len(documents),
        len(chunks),
    )