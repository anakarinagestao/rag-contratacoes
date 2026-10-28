"""Busca lexical persistente com SQLite FTS5 sobre ``document_chunks``.

``document_chunks`` permanece a fonte de verdade. A tabela FTS é de conteúdo
externo e usa ``DocumentChunk.id`` como ``rowid``; triggers mantêm o índice
sincronizado para qualquer base que persista novos chunks.
"""

from __future__ import annotations

import re
from sqlalchemy import Select, column, func, literal_column, select, table, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

NOME_TABELA_FTS = "document_chunks_fts"


def _tabela_existe(conexao: Connection, nome: str) -> bool:
    return bool(
        conexao.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (nome,),
        ).scalar()
    )


def _criar_triggers(conexao: Connection) -> None:
    """Cria triggers idempotentes para inserção, atualização e remoção."""

    conexao.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS document_chunks_fts_ai
        AFTER INSERT ON document_chunks BEGIN
            INSERT INTO document_chunks_fts(rowid, content)
            VALUES (new.id, new.content);
        END;
        """
    )
    conexao.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS document_chunks_fts_ad
        AFTER DELETE ON document_chunks BEGIN
            INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END;
        """
    )
    conexao.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS document_chunks_fts_au
        AFTER UPDATE OF content ON document_chunks BEGIN
            INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
            INSERT INTO document_chunks_fts(rowid, content)
            VALUES (new.id, new.content);
        END;
        """
    )


def inicializar_fts5(engine: Engine) -> None:
    """Cria o FTS5 e faz backfill apenas na primeira inicialização.

    O projeto usa SQLite. Falhar explicitamente em outro dialeto evita que uma
    aplicação pareça ter busca lexical persistente quando o recurso não existe.
    """

    if engine.dialect.name != "sqlite":
        raise RuntimeError("FTS5 requer SQLite neste projeto.")

    with engine.begin() as conexao:
        existente = _tabela_existe(conexao, NOME_TABELA_FTS)
        if not existente:
            conexao.exec_driver_sql(
                """
                CREATE VIRTUAL TABLE document_chunks_fts USING fts5(
                    content,
                    content='document_chunks',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
        _criar_triggers(conexao)
        if not existente:
            # Os triggers só cobrem operações futuras. Este comando indexa os
            # DocumentChunk já presentes, sem duplicar o conteúdo oficial.
            conexao.exec_driver_sql(
                "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild')"
            )


def reconstruir_fts5(engine: Engine) -> int:
    """Reconstrói explicitamente o índice lexical a partir de DocumentChunk."""

    inicializar_fts5(engine)
    with engine.begin() as conexao:
        conexao.exec_driver_sql(
            "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild')"
        )
        total = conexao.exec_driver_sql(
            "SELECT COUNT(*) FROM document_chunks_fts"
        ).scalar_one()
    return int(total)


def preparar_consulta_fts5(pergunta: str) -> str:
    """Converte pergunta livre em termos seguros unidos por OR.

    Não repassamos operadores FTS5 inseridos pelo usuário (AND, NOT, aspas,
    parênteses etc.). O FAISS preserva a recuperação semântica quando nenhum
    termo lexical existir exatamente no corpus.
    """

    termos = re.findall(r"[^\W_]+", pergunta.lower(), flags=re.UNICODE)
    return " OR ".join(f'"{termo.replace(chr(34), chr(34) * 2)}"' for termo in termos)


def buscar_fts5(
    db: Session,
    pergunta: str,
    top_k: int,
    eligible_chunks: Select | None = None,
) -> list[tuple[str, float]]:
    """Retorna ``(chunk_id, score_bm25)`` sem carregar o corpus na memória."""

    if top_k <= 0:
        return []
    consulta = preparar_consulta_fts5(pergunta)
    if not consulta:
        return []
    from app.modules.documents.model import DocumentChunk

    fts = table(NOME_TABELA_FTS, column("rowid"))
    score = func.bm25(literal_column(NOME_TABELA_FTS)).label("score")
    statement = (
        select(DocumentChunk.chunk_id, score)
        .select_from(fts.join(DocumentChunk, DocumentChunk.id == fts.c.rowid))
        .where(literal_column(NOME_TABELA_FTS).match(consulta))
    )
    if eligible_chunks is not None:
        statement = statement.where(
            DocumentChunk.chunk_id.in_(eligible_chunks.correlate(None))
        )
    statement = statement.order_by(score.asc()).limit(top_k)

    linhas = db.execute(statement).all()
    return [(str(linha.chunk_id), float(linha.score)) for linha in linhas]
