from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import replace
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import resolver_caminho_indice_faiss, settings
from app.core.database.readonly import readonly_sqlite_connection
from app.core.logging import perf_stage
from app.modules.documents.services import (
    consultar_catalogo_documentos_pncp,
    solicitar_processamento_catalogo,
)
from app.modules.query.model import Query, Response, ResponseSource
from src.query.adaptive_executor import (
    AdaptiveExecutionResult,
    executar_plano_adaptativo,
)
from src.query.analyzer import (
    analisar_pergunta,
    analisar_pergunta_com_contexto,
    analisar_pergunta_estruturada,
)
from src.query.executor import (
    executar_analise_estruturada,
    executar_consulta_adaptativa,
)
from src.query.normalized_structured_tool import executar_consulta_normalizada
from src.query.schemas import AdaptiveQueryResult, QueryAnalysis, QueryRoute
from src.retrieve import RankedChunk


AnalyzerFn = Callable[[str], QueryAnalysis]


class NormalizedDatabaseUnavailable(RuntimeError):
    """Indica que a camada normalizada não pode atender a consulta."""


def _agendar_processamento_documental(
    db: Session,
    referencias: list[dict[str, object]],
    *,
    limite: int,
) -> list[dict[str, object]]:
    """Cria/reutiliza poucas tarefas elegíveis sem executar o worker.

    Entradas removidas, sem URL ou já processadas não entram na fila. O
    limite acompanha o ``top_k`` pedido e evita uma fan-out acidental de uma
    única pergunta.
    """

    tarefas: list[dict[str, object]] = []
    for referencia in referencias:
        if len(tarefas) >= limite:
            break
        catalog_entry_id = referencia.get("id")
        if (
            not isinstance(catalog_entry_id, int)
            or referencia.get("availabilityStatus") == "REMOVED"
            or referencia.get("statusProcessamento") == "PROCESSADO"
            or not referencia.get("urlDownload")
        ):
            continue
        try:
            tarefa, criada = solicitar_processamento_catalogo(
                db,
                catalog_entry_id,
                motivo="consulta_adaptativa",
            )
        except ValueError:
            # O estado pode ter mudado entre a consulta do catálogo e a
            # tentativa idempotente (por exemplo, o worker terminou o item).
            continue
        tarefas.append(
            {
                "task_id": tarefa.id,
                "catalog_entry_id": tarefa.catalog_entry_id,
                "status": tarefa.status,
                "status_url": f"/documents/processing-tasks/{tarefa.id}",
                "created": criada,
            }
        )
    return tarefas


@contextmanager
def _normalized_connection(
    database_url: str | None = None,
) -> Iterator[sqlite3.Connection]:
    selected_url = database_url or settings.normalized_database_url
    if not selected_url:
        raise NormalizedDatabaseUnavailable(
            "Base normalizada não configurada."
        )
    with ExitStack() as stack:
        # Só a abertura da conexão indica indisponibilidade. Um ValueError
        # levantado dentro do bloco (por exemplo, StructuredQueryError) é um
        # plano inválido e deve chegar à rota como 422, não como 503.
        try:
            connection = stack.enter_context(
                readonly_sqlite_connection(selected_url)
            )
        except (ValueError, FileNotFoundError, sqlite3.Error) as exc:
            raise NormalizedDatabaseUnavailable(
                "Base normalizada indisponível."
            ) from exc
        try:
            yield connection
        except sqlite3.Error as exc:
            raise NormalizedDatabaseUnavailable(
                "Base normalizada indisponível."
            ) from exc


def _executar_analise_normalizada(
    connection: sqlite3.Connection,
    analysis: QueryAnalysis,
) -> AdaptiveQueryResult:
    return executar_analise_estruturada(
        connection,
        analysis,
        structured_tool_fn=executar_consulta_normalizada,
    )


def executar_consulta_estruturada(
    db: Session,
    pergunta: str,
    analyzer_fn: AnalyzerFn | None = None,
    normalized_database_url: str | None = None,
) -> AdaptiveQueryResult:
    """Executa a consulta SQL somente na camada normalizada configurada."""

    del db  # A sessão operacional não pode servir como fallback para a RAW.
    with _normalized_connection(normalized_database_url) as connection:
        return executar_consulta_adaptativa(
            connection,
            pergunta,
            analyzer_fn=analyzer_fn or analisar_pergunta_estruturada,
            structured_tool_fn=executar_consulta_normalizada,
        )


def executar_consulta_adaptativa_api(
    db: Session,
    pergunta: str,
    top_k: int,
    analyzer_fn: AnalyzerFn | None = None,
    rag_executor: Callable[[], object] | None = None,
    hybrid_executor: Callable[[AdaptiveQueryResult], object] | None = None,
    catalog_executor: Callable[[], list[dict[str, object]]] | None = None,
    normalized_database_url: str | None = None,
    previous_analysis: QueryAnalysis | None = None,
) -> AdaptiveExecutionResult:
    """Analisa uma vez a pergunta e executa exclusivamente sua rota planejada."""

    with perf_stage("analyzer"):
        if analyzer_fn is not None:
            analysis = analyzer_fn(pergunta)
        elif previous_analysis is not None:
            analysis = analisar_pergunta_com_contexto(pergunta, previous_analysis)
        else:
            analysis = analisar_pergunta(pergunta)

    if rag_executor is None:
        def rag_executor() -> object:
            # Importação tardia evita ciclo: generate.py já usa este service
            # para persistir Query, Response e ResponseSource.
            from src.generate import gerar_resposta_rag

            return gerar_resposta_rag(
                db=db,
                pergunta=pergunta,
                top_k=top_k,
                cache_dir=resolver_caminho_indice_faiss(),
                persistir=True,
                filters=analysis.filters,
                bases=analysis.bases,
            )

    if hybrid_executor is None:
        def hybrid_executor(structured: AdaptiveQueryResult) -> object:
            # A fonte estruturada é entregue em seção própria; as citações da
            # resposta continuam apontando somente para chunks documentais.
            from src.generate import gerar_resposta_rag

            return gerar_resposta_rag(
                db=db,
                pergunta=pergunta,
                top_k=top_k,
                cache_dir=resolver_caminho_indice_faiss(),
                persistir=True,
                filters=analysis.filters,
                bases=analysis.bases,
                structured_result=structured.structured_result,
            )

    if catalog_executor is None:
        def catalog_executor() -> list[dict[str, object]]:
            tipo_entidade = analysis.filters.tipo_entidade
            return consultar_catalogo_documentos_pncp(
                db,
                numero_controle_pncp=analysis.filters.numero_controle_pncp,
                tipo_entidade=(
                    tipo_entidade.value if tipo_entidade is not None else None
                ),
                tipo_documento=analysis.filters.tipo_documento,
                limite=20,
            )

    needs_normalized = analysis.route in {QueryRoute.STRUCTURED, QueryRoute.HYBRID}
    connection_context = (
        _normalized_connection(normalized_database_url)
        if needs_normalized
        else nullcontext(None)
    )
    with perf_stage("database_connection") if needs_normalized else nullcontext():
        with connection_context as structured_connection:
            resultado = executar_plano_adaptativo(
                analysis,
                structured_connection=structured_connection,
                rag_executor=rag_executor,
                hybrid_executor=hybrid_executor,
                catalog_executor=catalog_executor,
                structured_executor=_executar_analise_normalizada,
            )

    if resultado.plan.route is QueryRoute.CATALOG and resultado.external_documents:
        with perf_stage("document_queue"):
            tarefas = _agendar_processamento_documental(
                db,
                resultado.external_documents,
                limite=min(top_k, 5),
            )
        return replace(resultado, processing_tasks=tarefas)

    # Fallback informativo: se uma pergunta documental por número de controle
    # não encontrou chunks, mostramos os arquivos previamente listados no
    # PNCP. Esta etapa NÃO baixa o documento e não chama a API externa.
    fontes = getattr(resultado.rag_result, "sources", None)
    controle = analysis.filters.numero_controle_pncp
    if resultado.rag_result is not None and not fontes and controle:
        tipo_entidade = (
            analysis.entity.value
            if analysis.entity.value in {"contrato", "contratacao"}
            else None
        )
        with perf_stage("catalog_fallback"):
            referencias = consultar_catalogo_documentos_pncp(
                db,
                numero_controle_pncp=controle,
                tipo_entidade=tipo_entidade,
                tipo_documento=analysis.filters.tipo_documento,
                limite=20,
            )
        with perf_stage("document_queue"):
            tarefas = _agendar_processamento_documental(
                db,
                referencias,
                limite=min(top_k, 5),
            )
        return replace(
            resultado,
            external_documents=referencias,
            processing_tasks=tarefas,
        )
    return resultado


def persistir_resultado_rag(
    db: Session,
    pergunta: str,
    resposta: str,
    sources: list[RankedChunk],
    is_refusal: bool = False,
) -> tuple[Query, Response]:
    query = Query(
        run_id=str(uuid4()),
        text=pergunta,
    )
    db.add(query)
    db.flush()

    response = Response(
        query_id=query.id,
        answer=resposta,
        is_refusal=is_refusal,
    )
    db.add(response)
    db.flush()

    for rank, source in enumerate(sources, start=1):
        document_chunk_id = source.metadata.get("id")

        if not isinstance(document_chunk_id, int):
            raise ValueError(
                f"RankedChunk {source.chunk_id} não possui "
                "DocumentChunk.id válido nos metadados."
            )

        db.add(
            ResponseSource(
                response_id=response.id,
                chunk_id=document_chunk_id,
                rank=rank,
                relevance_score=source.rrf_score,
            )
        )

    db.commit()
    db.refresh(query)
    db.refresh(response)

    return query, response
