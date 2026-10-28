"""Planejador determinístico entre a análise e as ferramentas do RAG."""

from .schemas import ExecutionPlan, ExecutionTool, QueryAnalysis, QueryRoute


def planejar_execucao(analysis: QueryAnalysis) -> ExecutionPlan:
    """Converte uma análise já validada em ferramentas permitidas e ordenadas.

    Esta função não executa SQL, retrieval, FAISS nem chamadas à LLM. Ela evita
    que a escolha de ferramentas dependa de texto livre retornado por um modelo.
    """

    ferramentas_por_rota = {
        QueryRoute.STRUCTURED: [ExecutionTool.STRUCTURED_DATABASE],
        QueryRoute.RAG: [ExecutionTool.RAG_RETRIEVAL],
        QueryRoute.HYBRID: [
            ExecutionTool.STRUCTURED_DATABASE,
            ExecutionTool.RAG_RETRIEVAL,
        ],
        QueryRoute.CATALOG: [ExecutionTool.DOCUMENT_CATALOG],
    }

    return ExecutionPlan(
        route=analysis.route,
        tools=ferramentas_por_rota[analysis.route],
        analysis=analysis,
    )
