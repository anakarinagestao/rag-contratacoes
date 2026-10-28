"""Contratos validados entre Query Analyzer, Planner e ferramentas.

Este módulo não executa SQL, retrieval nem chamadas à LLM. Ele representa
somente o plano que será produzido pelo Query Analyzer e consumido pelo
Planner/Router nas etapas seguintes.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QueryRoute(str, Enum):
    """Estratégias de execução previstas para uma pergunta."""

    STRUCTURED = "structured"
    RAG = "rag"
    HYBRID = "hybrid"
    # Consulta somente os metadados locais dos arquivos anunciados pelo PNCP.
    # Não baixa arquivo, não usa FAISS/FTS5 e não chama a LLM geradora.
    CATALOG = "catalog"


class ExecutionTool(str, Enum):
    """Ferramentas internas permitidas no plano de execução."""

    STRUCTURED_DATABASE = "structured_database"
    RAG_RETRIEVAL = "rag_retrieval"
    DOCUMENT_CATALOG = "document_catalog"


class QueryIntent(str, Enum):
    """Intenções de alto nível reconhecidas pelo Analyzer."""

    AGGREGATE = "aggregate"
    LOOKUP = "lookup"
    DOCUMENTAL = "documental"
    COMPARISON = "comparison"


class QueryEntity(str, Enum):
    """Entidades públicas que podem ser consultadas."""

    CONTRATO = "contrato"
    CONTRATACAO = "contratacao"
    CONTRATACAO_ITEM = "contratacao_item"
    RESULTADO_ITEM = "resultado_item"
    ATA_REGISTRO_PRECO = "ata_registro_preco"
    PCA = "pca"
    PCA_ITEM = "pca_item"
    DOCUMENTO = "documento"


class KnowledgeBase(str, Enum):
    """Bases de evidência previstas na arquitetura Adaptive RAG."""

    ESTRUTURADA = "estruturada"
    NORMATIVA = "normativa"
    CONVERSACIONAL = "conversacional"
    AGREGADA = "agregada"


class StructuredOperation(str, Enum):
    """Operações permitidas para a futura Structured DB Tool."""

    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    WEIGHTED_AVG_KG = "weighted_avg_kg"
    MIN = "min"
    MAX = "max"
    GROUP_BY = "group_by"
    ORDER_BY = "order_by"
    LIST = "list"


class StructuredField(str, Enum):
    """Campos públicos que poderão ser mapeados pela SQL Tool."""

    VALOR_GLOBAL = "valor_global"
    VALOR_INICIAL = "valor_inicial"
    VALOR_ACUMULADO = "valor_acumulado"
    VALOR_PARCELA = "valor_parcela"
    VALOR_ESTIMADO = "valor_estimado"
    VALOR_HOMOLOGADO = "valor_homologado"
    VALOR_UNITARIO = "valor_unitario"
    VALOR_TOTAL = "valor_total"
    VALOR_PLANEJADO = "valor_planejado"
    QUANTIDADE = "quantidade"
    ANO = "ano"
    DATA_PUBLICACAO = "data_publicacao"
    DATA_ASSINATURA = "data_assinatura"
    DATA_RESULTADO = "data_resultado"
    DATA_DESEJADA = "data_desejada"
    VIGENCIA_INICIO = "vigencia_inicio"
    VIGENCIA_FIM = "vigencia_fim"
    DATA_CANCELAMENTO = "data_cancelamento"
    MUNICIPIO = "municipio"
    ORGAO = "orgao"
    CNPJ_ORGAO = "cnpj_orgao"
    ESFERA = "esfera"
    CODIGO_UNIDADE = "codigo_unidade"
    FORNECEDOR = "fornecedor"
    MODALIDADE = "modalidade"
    OBJETO = "objeto"
    NUMERO_ITEM = "numero_item"
    SEQUENCIAL_RESULTADO = "sequencial_resultado"
    UNIDADE_MEDIDA = "unidade_medida"
    CATEGORIA = "categoria"
    SITUACAO = "situacao"
    BENEFICIO_ME_EPP = "beneficio_me_epp"
    QUALIDADE_DADO = "qualidade_dado"
    NUMERO_PARCELAS = "numero_parcelas"
    POSSIBILIDADE_ADESAO = "possibilidade_adesao"
    COBERTURA = "cobertura"
    FORMATO_FONTE = "formato_fonte"
    NOME_UNIDADE = "nome_unidade"


class AggregateSortField(str, Enum):
    """Campos calculados que podem ordenar uma consulta agregada."""

    COUNT = "count"


class ValueFilterField(str, Enum):
    """Campos monetários permitidos em filtros por faixa de valor."""

    VALOR_GLOBAL = "valor_global"
    VALOR_ESTIMADO = "valor_estimado"
    VALOR_HOMOLOGADO = "valor_homologado"
    VALOR_UNITARIO = "valor_unitario"
    VALOR_TOTAL = "valor_total"
    VALOR_PLANEJADO = "valor_planejado"


class TemporalFilterField(str, Enum):
    """Semântica explícita do intervalo informado pelo usuário."""

    DATA_PUBLICACAO = "data_publicacao"
    DATA_ASSINATURA = "data_assinatura"
    DATA_RESULTADO = "data_resultado"
    DATA_DESEJADA = "data_desejada"
    VIGENCIA_FIM = "vigencia_fim"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class OrderBy(BaseModel):
    """Uma ordenação declarativa, sem conter SQL livre."""

    model_config = ConfigDict(extra="forbid")

    # ``count`` não é uma coluna do asset: é o alias produzido por
    # ``COUNT(*)`` nas consultas agrupadas. Mantê-lo separado dos campos
    # públicos evita abrir espaço para SQL arbitrário.
    field: StructuredField | AggregateSortField
    direction: SortDirection = SortDirection.DESC


class QueryFilters(BaseModel):
    """Filtros extraídos da pergunta e reutilizados por SQL e RAG."""

    model_config = ConfigDict(extra="forbid")

    numero_controle_pncp: str | None = None
    document_id: int | None = Field(default=None, ge=1)
    tipo_entidade: QueryEntity | None = None
    tipo_documento: str | None = None
    orgao: str | None = None
    cnpj_orgao: str | None = None
    esfera: str | None = None
    codigo_unidade: str | None = None
    municipio: str | None = None
    fornecedor: str | None = None
    modalidade: str | None = None
    objeto: str | None = None
    unidade_medida: str | None = None
    categoria: str | None = None
    situacao: str | None = None
    possibilidade_adesao: bool | None = None
    ano: int | None = Field(default=None, ge=2000, le=2100)
    campo_data: TemporalFilterField = TemporalFilterField.DATA_PUBLICACAO
    period_start: date | None = None
    period_end: date | None = None
    # Ausente preserva o comportamento histórico: a faixa usa valor global.
    campo_valor: ValueFilterField | None = None
    valor_minimo: float | None = Field(default=None, ge=0)
    valor_maximo: float | None = Field(default=None, ge=0)
    quantidade_minima: float | None = Field(default=None, ge=0)
    quantidade_maxima: float | None = Field(default=None, ge=0)

    @field_validator("campo_data", mode="before")
    @classmethod
    def normalizar_campo_data_ausente(cls, value: object) -> object:
        """Trata ``null`` produzido pela LLM como o default seguro."""

        if value is None:
            return TemporalFilterField.DATA_PUBLICACAO
        return value

    @model_validator(mode="after")
    def validar_intervalos(self) -> "QueryFilters":
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError(
                "period_start não pode ser posterior a period_end.")
        if (
            self.valor_minimo is not None
            and self.valor_maximo is not None
            and self.valor_minimo > self.valor_maximo
        ):
            raise ValueError(
                "valor_minimo não pode ser maior que valor_maximo.")
        if (
            self.quantidade_minima is not None
            and self.quantidade_maxima is not None
            and self.quantidade_minima > self.quantidade_maxima
        ):
            raise ValueError(
                "quantidade_minima não pode ser maior que quantidade_maxima."
            )
        return self


class QueryAnalysis(BaseModel):
    """Plano validado que será entregue pelo Query Analyzer ao Planner."""

    model_config = ConfigDict(extra="forbid")

    route: QueryRoute
    intent: QueryIntent
    entity: QueryEntity
    bases: list[KnowledgeBase] = Field(min_length=1)
    filters: QueryFilters = Field(default_factory=QueryFilters)
    operations: list[StructuredOperation] = Field(default_factory=list)
    aggregation_field: StructuredField | None = None
    group_by: list[StructuredField] = Field(default_factory=list)
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=100)
    semantic_query: str | None = None
    needs_document_retrieval: bool = False

    @model_validator(mode="after")
    def validar_plano(self) -> "QueryAnalysis":
        if len(set(self.operations)) != len(self.operations):
            raise ValueError(
                "operations não pode conter operações duplicadas.")

        operacoes_agregadas = {
            StructuredOperation.SUM,
            StructuredOperation.AVG,
            StructuredOperation.MIN,
            StructuredOperation.MAX,
            StructuredOperation.WEIGHTED_AVG_KG,
        }
        if operacoes_agregadas.intersection(self.operations) and not self.aggregation_field:
            raise ValueError(
                "aggregation_field é obrigatório para sum, avg, min ou max."
            )
        if self.group_by and StructuredOperation.GROUP_BY not in self.operations:
            raise ValueError("group_by exige a operação group_by.")
        if StructuredOperation.GROUP_BY in self.operations and not self.group_by:
            raise ValueError("A operação group_by exige ao menos um campo.")
        if self.order_by and StructuredOperation.ORDER_BY not in self.operations:
            raise ValueError("order_by exige a operação order_by.")
        if any(order.field is AggregateSortField.COUNT for order in self.order_by):
            if StructuredOperation.COUNT not in self.operations:
                raise ValueError("order_by por count exige a operação count.")

        if self.route is QueryRoute.STRUCTURED:
            structured_base_by_entity = {
                QueryEntity.CONTRATO: KnowledgeBase.ESTRUTURADA,
                QueryEntity.CONTRATACAO: KnowledgeBase.ESTRUTURADA,
                QueryEntity.CONTRATACAO_ITEM: KnowledgeBase.ESTRUTURADA,
                QueryEntity.RESULTADO_ITEM: KnowledgeBase.ESTRUTURADA,
                QueryEntity.ATA_REGISTRO_PRECO: KnowledgeBase.CONVERSACIONAL,
                QueryEntity.PCA: KnowledgeBase.AGREGADA,
                QueryEntity.PCA_ITEM: KnowledgeBase.AGREGADA,
            }
            required_base = structured_base_by_entity.get(self.entity)
            if required_base is None or required_base not in self.bases:
                raise ValueError(
                    "A rota structured exige a base correspondente à entidade."
                )
            if self.needs_document_retrieval:
                raise ValueError(
                    "A rota structured não deve solicitar recuperação documental."
                )
        elif self.route is QueryRoute.RAG:
            if self.operations:
                raise ValueError(
                    "A rota rag não aceita operações estruturadas.")
            if not self.needs_document_retrieval:
                raise ValueError(
                    "A rota rag exige needs_document_retrieval igual a true."
                )
        elif self.route is QueryRoute.HYBRID:
            hybrid_base_by_entity = {
                QueryEntity.CONTRATO: KnowledgeBase.ESTRUTURADA,
                QueryEntity.CONTRATACAO: KnowledgeBase.ESTRUTURADA,
                QueryEntity.CONTRATACAO_ITEM: KnowledgeBase.ESTRUTURADA,
                QueryEntity.RESULTADO_ITEM: KnowledgeBase.ESTRUTURADA,
                QueryEntity.ATA_REGISTRO_PRECO: KnowledgeBase.CONVERSACIONAL,
                QueryEntity.PCA: KnowledgeBase.AGREGADA,
                QueryEntity.PCA_ITEM: KnowledgeBase.AGREGADA,
            }
            required_base = hybrid_base_by_entity.get(self.entity)
            # ``documento`` continua válido na rota híbrida por
            # compatibilidade com planos documentais existentes. Para as
            # entidades relacionais, entretanto, a base lógica correta é
            # obrigatória.
            if required_base is not None and required_base not in self.bases:
                if self.entity in {
                    QueryEntity.CONTRATO,
                    QueryEntity.CONTRATACAO,
                    QueryEntity.CONTRATACAO_ITEM,
                    QueryEntity.RESULTADO_ITEM,
                }:
                    raise ValueError("A rota hybrid exige a base estruturada.")
                raise ValueError(
                    "A rota hybrid exige a base correspondente à entidade."
                )
            if not self.needs_document_retrieval:
                raise ValueError(
                    "A rota hybrid exige needs_document_retrieval igual a true."
                )
        elif self.route is QueryRoute.CATALOG:
            if self.intent is not QueryIntent.LOOKUP:
                raise ValueError("A rota catalog exige intent lookup.")
            if self.entity is not QueryEntity.DOCUMENTO:
                raise ValueError("A rota catalog exige entity documento.")
            if self.bases != [KnowledgeBase.AGREGADA]:
                raise ValueError("A rota catalog exige somente a base agregada.")
            if self.operations or self.aggregation_field or self.group_by or self.order_by:
                raise ValueError("A rota catalog não aceita operações estruturadas.")
            if self.needs_document_retrieval:
                raise ValueError(
                    "A rota catalog não deve solicitar recuperação documental."
                )

        return self


class ExecutionPlan(BaseModel):
    """Plano determinístico que o Router executará em uma etapa posterior."""

    model_config = ConfigDict(extra="forbid")

    route: QueryRoute
    tools: list[ExecutionTool] = Field(min_length=1)
    analysis: QueryAnalysis

    @model_validator(mode="after")
    def validar_consistencia(self) -> "ExecutionPlan":
        if self.route is not self.analysis.route:
            raise ValueError("route deve corresponder à rota da análise.")

        ferramentas_por_rota = {
            QueryRoute.STRUCTURED: [ExecutionTool.STRUCTURED_DATABASE],
            QueryRoute.RAG: [ExecutionTool.RAG_RETRIEVAL],
            QueryRoute.HYBRID: [
                ExecutionTool.STRUCTURED_DATABASE,
                ExecutionTool.RAG_RETRIEVAL,
            ],
            QueryRoute.CATALOG: [ExecutionTool.DOCUMENT_CATALOG],
        }
        ferramentas_esperadas = ferramentas_por_rota[self.route]
        if self.tools != ferramentas_esperadas:
            raise ValueError(
                "tools deve conter, na ordem definida, as ferramentas da rota."
            )
        return self


class StructuredWarning(BaseModel):
    """Alerta de qualidade que preserva, mas contextualiza, o dado de origem."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    affected_rows: int = Field(ge=1)


class DataCoverage(BaseModel):
    """Cobertura efetiva da consulta; não confundir com sucesso da carga."""

    model_config = ConfigDict(extra="forbid")
    state: str = Field(default="unknown", pattern="^(covered|partial|unavailable|unknown)$")
    resources: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=lambda: [
        "Cobertura não comprovada; os resultados se limitam à base disponível e não comprovam inexistência."
    ])


class StructuredQueryResult(BaseModel):
    """Resultado serializável de uma consulta somente-leitura ao SQLite."""

    model_config = ConfigDict(extra="forbid")

    entity: QueryEntity
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    limit_applied: int
    warnings: list[StructuredWarning] = Field(default_factory=list)
    coverage: DataCoverage = Field(default_factory=DataCoverage)


class AdaptiveQueryResult(BaseModel):
    """Resultado da execução estruturada de uma pergunta já analisada."""

    model_config = ConfigDict(extra="forbid")

    analysis: QueryAnalysis
    plan: ExecutionPlan
    structured_result: StructuredQueryResult
