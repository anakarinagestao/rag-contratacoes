"""Roteamento técnico entre o plano validado e seus executores permitidos."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from app.core.logging import perf_stage

from .executor import QueryExecutionError, executar_analise_estruturada
from .planner import planejar_execucao
from .schemas import AdaptiveQueryResult, ExecutionPlan, QueryAnalysis, QueryRoute


RagExecutorFn = Callable[[], Any]
HybridExecutorFn = Callable[[AdaptiveQueryResult], Any]
CatalogExecutorFn = Callable[[], list[dict[str, Any]]]
StructuredExecutorFn = Callable[
    [sqlite3.Connection, QueryAnalysis],
    AdaptiveQueryResult,
]


@dataclass(frozen=True)
class AdaptiveExecutionResult:
    """Resultado de uma rota estruturada ou RAG antes da adaptação HTTP."""

    analysis: QueryAnalysis
    plan: ExecutionPlan
    structured_result: AdaptiveQueryResult | None = None
    rag_result: Any | None = None
    # Referências catalogadas para arquivos ainda não processados. Não são
    # baixadas automaticamente nem entram no prompt da LLM.
    external_documents: list[dict[str, Any]] = field(default_factory=list)
    # Tarefas persistidas/reutilizadas para processamento assíncrono. Criar a
    # tarefa não executa download, OCR ou indexação durante a requisição.
    processing_tasks: list[dict[str, Any]] = field(default_factory=list)


def executar_plano_adaptativo(
    analysis: QueryAnalysis,
    *,
    structured_connection: sqlite3.Connection | None = None,
    rag_executor: RagExecutorFn | None = None,
    hybrid_executor: HybridExecutorFn | None = None,
    catalog_executor: CatalogExecutorFn | None = None,
    structured_executor: StructuredExecutorFn | None = None,
) -> AdaptiveExecutionResult:
    """Executa exclusivamente a ferramenta indicada pela rota planejada.

    A rota híbrida executa SQL primeiro e entrega seu resultado ao executor
    documental, que é responsável por manter os dois contextos separados.
    """

    with perf_stage("planner"):
        plan = planejar_execucao(analysis)
    execute_structured = structured_executor or executar_analise_estruturada
    if plan.route is QueryRoute.STRUCTURED:
        if structured_connection is None:
            raise QueryExecutionError("A rota structured requer conexão SQLite.")
        with perf_stage("structured_query"):
            structured = execute_structured(structured_connection, analysis)
        return AdaptiveExecutionResult(
            analysis=analysis,
            plan=plan,
            structured_result=structured,
        )

    if plan.route is QueryRoute.RAG:
        if rag_executor is None:
            raise QueryExecutionError("A rota rag requer um executor documental.")
        return AdaptiveExecutionResult(
            analysis=analysis,
            plan=plan,
            rag_result=rag_executor(),
        )

    if plan.route is QueryRoute.CATALOG:
        if catalog_executor is None:
            raise QueryExecutionError("A rota catalog requer um executor de catálogo.")
        return AdaptiveExecutionResult(
            analysis=analysis,
            plan=plan,
            external_documents=catalog_executor(),
        )

    if structured_connection is None:
        raise QueryExecutionError("A rota hybrid requer conexão SQLite.")
    if hybrid_executor is None:
        raise QueryExecutionError("A rota hybrid requer um executor híbrido.")
    with perf_stage("structured_query"):
        structured = execute_structured(structured_connection, analysis)
    return AdaptiveExecutionResult(
        analysis=analysis,
        plan=plan,
        structured_result=structured,
        rag_result=hybrid_executor(structured),
    )
