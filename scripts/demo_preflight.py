"""Pré-voo somente leitura para a demonstração local do Adaptive RAG."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from app.core.config import resolver_caminho_indice_faiss, settings
from llm_ecoa import EMBEDDING_MODEL
from src.faiss_errors import FAISS_UNAVAILABLE_ERRORS
from src.index import validar_indice_faiss


NORMALIZED_TABLES = (
    "contratacao",
    "contratacao_item",
    "resultado_item",
    "contrato",
    "ata_registro_preco",
    "pca",
    "pca_item",
    "pca_contratacao_link",
)


def _sqlite_path(database_url: str | None) -> Path:
    if not database_url or not database_url.startswith("sqlite:///"):
        raise ValueError("NORMALIZED_DATABASE_URL SQLite não configurada")
    return Path(database_url.removeprefix("sqlite:///")).expanduser().resolve()


def _http_ok(url: str) -> bool:
    try:
        with urlopen(url, timeout=3) as response:  # noqa: S310 - URL local fixa.
            return response.status == 200
    except (OSError, URLError):
        return False


def collect_preflight() -> tuple[dict[str, object], bool]:
    """Coleta somente metadados técnicos; não imprime segredos nem payloads."""

    report: dict[str, object] = {
        "api": _http_ok("http://127.0.0.1:8000/"),
        "streamlit": _http_ok("http://127.0.0.1:8501/_stcore/health"),
        "providers": {
            "llm": settings.llm_primary_provider,
            "embedding": settings.embedding_provider,
        },
    }
    ready = True
    try:
        normalized_path = _sqlite_path(settings.normalized_database_url)
        connection = sqlite3.connect(
            normalized_path.as_uri() + "?mode=ro", uri=True
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            counts = {
                table: int(
                    connection.execute(
                        f'SELECT count(*) FROM "{table}"'
                    ).fetchone()[0]
                )
                for table in NORMALIZED_TABLES
            }
            report["normalized"] = {
                "ready": True,
                "query_only": bool(
                    connection.execute("PRAGMA query_only").fetchone()[0]
                ),
                "counts": counts,
            }
        finally:
            connection.close()
    except (ValueError, FileNotFoundError, sqlite3.Error) as error:
        ready = False
        report["normalized"] = {
            "ready": False,
            "error": type(error).__name__,
        }

    try:
        audit = validar_indice_faiss(
            resolver_caminho_indice_faiss(),
            modelo_esperado=EMBEDDING_MODEL,
        )
        report["faiss"] = {
            "ready": True,
            "vectors": audit.total_vetores,
            "dimension": audit.dimensao,
            "model": audit.modelo_embedding,
        }
    except FAISS_UNAVAILABLE_ERRORS as error:
        ready = False
        report["faiss"] = {
            "ready": False,
            "error": error.error_code,
        }

    operational_path = Path("app.db").resolve()
    if operational_path.is_file():
        try:
            connection = sqlite3.connect(
                operational_path.as_uri() + "?mode=ro", uri=True
            )
            try:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                report["operational"] = {
                    "documents": (
                        connection.execute("SELECT count(*) FROM documents").fetchone()[0]
                        if "documents" in tables
                        else 0
                    ),
                    "chunks": (
                        connection.execute("SELECT count(*) FROM document_chunks").fetchone()[0]
                        if "document_chunks" in tables
                        else 0
                    ),
                    "catalog_entries": (
                        connection.execute(
                            "SELECT count(*) FROM document_catalog_entries"
                        ).fetchone()[0]
                        if "document_catalog_entries" in tables
                        else 0
                    ),
                }
            finally:
                connection.close()
        except sqlite3.Error as error:
            report["operational"] = {"error": type(error).__name__}

    report["ready"] = ready
    return report, ready


def main() -> int:
    report, ready = collect_preflight()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
