"""Contratos e componentes técnicos para análise de perguntas."""

from .analyzer import QueryAnalysisError, analisar_pergunta
from .adaptive_executor import AdaptiveExecutionResult, executar_plano_adaptativo
from .executor import (
    QueryExecutionError,
    executar_analise_estruturada,
    executar_consulta_adaptativa,
)
from .planner import planejar_execucao
from .normalized_structured_tool import executar_consulta_normalizada
from .schemas import (
    ExecutionPlan,
    ExecutionTool,
    AdaptiveQueryResult,
    QueryAnalysis,
    QueryFilters,
    QueryRoute,
    StructuredQueryResult,
)
from .structured_tool import StructuredQueryError, executar_consulta_estruturada

__all__ = [
    "ExecutionPlan",
    "ExecutionTool",
    "AdaptiveQueryResult",
    "AdaptiveExecutionResult",
    "QueryAnalysis",
    "QueryAnalysisError",
    "QueryFilters",
    "QueryExecutionError",
    "QueryRoute",
    "StructuredQueryError",
    "StructuredQueryResult",
    "analisar_pergunta",
    "executar_analise_estruturada",
    "executar_consulta_adaptativa",
    "executar_consulta_normalizada",
    "executar_consulta_estruturada",
    "executar_plano_adaptativo",
    "planejar_execucao",
]
