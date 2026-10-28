from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.query.schemas import (
    ExecutionPlan,
    QueryAnalysis,
    StructuredQueryResult,
    QueryRoute,
)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)


class SourceResponse(BaseModel):
    rank: int
    chunk_id: str
    relevance_score: float
    metadata: dict[str, Any]


class ExternalDocumentResponse(BaseModel):
    """Arquivo anunciado pelo PNCP e seu estado local de processamento."""

    id: int
    tipoEntidade: str
    numeroControlePNCP: str
    cnpjOrgao: str | None = None
    ano: int | None = None
    sequencialEntidade: int | None = None
    sequencialDocumento: int
    tipoDocumento: str | None = None
    titulo: str | None = None
    urlDownload: str | None = None
    urlOriginal: str | None = None
    dataPublicacaoPncp: str | None = None
    statusAtivo: bool | None = None
    periodoColeta: str
    availabilityStatus: str = "UNKNOWN"
    firstSeenAt: str | None = None
    lastSeenAt: str | None = None
    missingSince: str | None = None
    inventoryId: str | None = None
    statusProcessamento: str
    documentoPersistido: bool
    chunksPersistidos: int = Field(ge=0)


class ProcessingTaskSummary(BaseModel):
    """Referência segura para acompanhar processamento iniciado sob demanda."""

    task_id: int
    catalog_entry_id: int
    status: str
    status_url: str
    created: bool


class GenerateResponse(BaseModel):
    answer: str
    sources: list[SourceResponse]
    trace_id: str | None = None


class StructuredQueryRequest(BaseModel):
    """Entrada da rota de consulta à base estruturada."""

    model_config = ConfigDict(extra="forbid")

    pergunta: str = Field(min_length=1)


class StructuredQueryResponse(BaseModel):
    """Análise, plano e registros retornados pela Structured Tool."""

    analysis: QueryAnalysis
    plan: ExecutionPlan
    structured_result: StructuredQueryResult
    answer: str


class AdaptiveAskRequest(BaseModel):
    """Entrada do endpoint principal de roteamento adaptativo."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"pergunta": "string", "top_k": 5}],
        },
    )

    pergunta: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)
    # Somente o plano validado anterior, nunca o histórico textual completo.
    contexto: QueryAnalysis | None = None


class AdaptiveAskResponse(BaseModel):
    """Resposta de consulta adaptativa ou recusa preventiva."""

    route: QueryRoute | None = None
    analysis: QueryAnalysis | None = None
    plan: ExecutionPlan | None = None
    structured_result: StructuredQueryResult | None = None
    answer: str | None = None
    refused: bool = False
    trace_id: str | None = None
    sources: list[SourceResponse] = Field(default_factory=list)
    external_documents: list[ExternalDocumentResponse] = Field(default_factory=list)
    processing_tasks: list[ProcessingTaskSummary] = Field(default_factory=list)
