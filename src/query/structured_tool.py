"""Ferramenta de leitura controlada para contratos e contratações no SQLite.

O Query Analyzer produz estruturas Pydantic; este módulo usa apenas essas
estruturas e mapeamentos internos. Nenhuma parte do SQL vem livremente da LLM.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.text_normalization import normalizar_para_busca

from .schemas import (
    QueryAnalysis,
    QueryEntity,
    StructuredField,
    StructuredOperation,
    StructuredQueryResult,
    ValueFilterField,
)


class StructuredQueryError(ValueError):
    """Indica que uma análise não pode ser consultada na base estruturada."""


@dataclass(frozen=True)
class _EntitySqlConfig:
    resource_type: str
    fields: dict[StructuredField, str]


_ENTITY_CONFIG: dict[QueryEntity, _EntitySqlConfig] = {
    QueryEntity.CONTRATO: _EntitySqlConfig(
        resource_type="contrato",
        fields={
            StructuredField.VALOR_GLOBAL: (
                "CAST(json_extract(asset.payload, '$.valorGlobal') AS REAL)"
            ),
            StructuredField.VALOR_ESTIMADO: (
                "CAST(json_extract(asset.payload, '$.valorInicial') AS REAL)"
            ),
            StructuredField.ANO: (
                "CAST(json_extract(asset.payload, '$.anoContrato') AS INTEGER)"
            ),
            StructuredField.DATA_PUBLICACAO: (
                "json_extract(asset.payload, '$.dataPublicacaoPncp')"
            ),
            StructuredField.MUNICIPIO: (
                "json_extract(asset.payload, '$.unidadeOrgao.municipioNome')"
            ),
            StructuredField.ORGAO: (
                "json_extract(asset.payload, '$.orgaoEntidade.razaoSocial')"
            ),
            StructuredField.FORNECEDOR: (
                "json_extract(asset.payload, '$.nomeRazaoSocialFornecedor')"
            ),
            StructuredField.OBJETO: (
                "json_extract(asset.payload, '$.objetoContrato')"
            ),
        },
    ),
    QueryEntity.CONTRATACAO: _EntitySqlConfig(
        resource_type="contratacao",
        fields={
            StructuredField.VALOR_GLOBAL: (
                "CAST(COALESCE("
                "json_extract(asset.payload, '$.valorTotalHomologado'), "
                "json_extract(asset.payload, '$.valorTotalEstimado')"
                ") AS REAL)"
            ),
            StructuredField.VALOR_ESTIMADO: (
                "CAST(json_extract(asset.payload, '$.valorTotalEstimado') AS REAL)"
            ),
            StructuredField.ANO: (
                "CAST(json_extract(asset.payload, '$.anoCompra') AS INTEGER)"
            ),
            StructuredField.DATA_PUBLICACAO: (
                "json_extract(asset.payload, '$.dataPublicacaoPncp')"
            ),
            StructuredField.MUNICIPIO: (
                "json_extract(asset.payload, '$.unidadeOrgao.municipioNome')"
            ),
            StructuredField.ORGAO: (
                "json_extract(asset.payload, '$.orgaoEntidade.razaoSocial')"
            ),
            StructuredField.MODALIDADE: (
                "json_extract(asset.payload, '$.modalidadeNome')"
            ),
            StructuredField.OBJETO: (
                "json_extract(asset.payload, '$.objetoCompra')"
            ),
        },
    ),
}

_AGGREGATE_OPERATIONS = {
    StructuredOperation.SUM,
    StructuredOperation.AVG,
    StructuredOperation.MIN,
    StructuredOperation.MAX,
}

_CANONICAL_FIELDS = (
    StructuredField.ANO,
    StructuredField.DATA_PUBLICACAO,
    StructuredField.MUNICIPIO,
    StructuredField.ORGAO,
    StructuredField.FORNECEDOR,
    StructuredField.MODALIDADE,
    StructuredField.OBJETO,
    StructuredField.VALOR_GLOBAL,
    StructuredField.VALOR_ESTIMADO,
)


def _field_expression(
    config: _EntitySqlConfig,
    field: StructuredField,
    *,
    required: bool = True,
) -> str | None:
    expression = config.fields.get(field)
    if expression is None and required:
        raise StructuredQueryError(
            f"O campo '{field.value}' não está disponível para esta entidade."
        )
    return expression


def _latest_assets_cte() -> str:
    """Seleciona uma única versão, a mais recente, por registro PNCP."""

    return """
        WITH latest_assets AS (
            SELECT asset.payload, asset.external_id, asset.resource_type
            FROM raw_asset AS asset
            JOIN (
                SELECT resource_type, external_id, MAX(rowid) AS latest_rowid
                FROM raw_asset
                WHERE resource_type = ?
                GROUP BY resource_type, external_id
            ) AS latest ON latest.latest_rowid = asset.rowid
        )
    """


def _where_clause(
    analysis: QueryAnalysis,
    config: _EntitySqlConfig,
) -> tuple[list[str], list[object]]:
    filters = analysis.filters
    if filters.tipo_entidade and filters.tipo_entidade is not analysis.entity:
        raise StructuredQueryError("tipo_entidade diverge da entidade analisada.")
    if filters.document_id is not None or filters.tipo_documento:
        raise StructuredQueryError(
            "Filtros de documento pertencem à recuperação documental, não à base estruturada."
        )

    clauses: list[str] = []
    params: list[object] = []

    if filters.numero_controle_pncp:
        clauses.append("asset.external_id = ?")
        params.append(filters.numero_controle_pncp)

    text_filters = (
        (StructuredField.ORGAO, filters.orgao),
        (StructuredField.MUNICIPIO, filters.municipio),
        (StructuredField.FORNECEDOR, filters.fornecedor),
        (StructuredField.MODALIDADE, filters.modalidade),
        (StructuredField.OBJETO, filters.objeto),
    )
    for field, value in text_filters:
        if value:
            expression = _field_expression(config, field)
            clauses.append(f"normalizar_busca({expression}) LIKE ?")
            params.append(f"%{normalizar_para_busca(value)}%")

    if filters.ano is not None:
        clauses.append(f"{_field_expression(config, StructuredField.ANO)} = ?")
        params.append(filters.ano)

    publication_date = _field_expression(config, StructuredField.DATA_PUBLICACAO)
    if filters.period_start:
        clauses.append(f"date({publication_date}) >= date(?)")
        params.append(filters.period_start.isoformat())
    if filters.period_end:
        clauses.append(f"date({publication_date}) <= date(?)")
        params.append(filters.period_end.isoformat())

    value_filter_field = filters.campo_valor or ValueFilterField.VALOR_GLOBAL
    value_expression = _field_expression(
        config,
        StructuredField(value_filter_field.value),
    )
    if filters.valor_minimo is not None:
        clauses.append(f"{value_expression} >= ?")
        params.append(filters.valor_minimo)
    if filters.valor_maximo is not None:
        clauses.append(f"{value_expression} <= ?")
        params.append(filters.valor_maximo)

    return clauses, params


def _select_clause(
    analysis: QueryAnalysis,
    config: _EntitySqlConfig,
) -> tuple[list[str], bool]:
    aggregate_operations = _AGGREGATE_OPERATIONS.intersection(analysis.operations)
    is_aggregate = bool(aggregate_operations or StructuredOperation.COUNT in analysis.operations)

    if analysis.group_by and not is_aggregate:
        raise StructuredQueryError("group_by exige ao menos uma operação de agregação.")

    if not is_aggregate:
        selections = ["asset.external_id AS numero_controle_pncp"]
        for field in _CANONICAL_FIELDS:
            expression = _field_expression(config, field, required=False)
            selections.append(
                f"{expression} AS {field.value}"
                if expression
                else f"NULL AS {field.value}"
            )
        return selections, False

    selections: list[str] = []
    for field in analysis.group_by:
        selections.append(f"{_field_expression(config, field)} AS {field.value}")
    if StructuredOperation.COUNT in analysis.operations:
        selections.append("COUNT(*) AS count")
    for operation in sorted(aggregate_operations, key=lambda value: value.value):
        field = analysis.aggregation_field
        if field is None:  # proteção adicional, apesar da validação Pydantic.
            raise StructuredQueryError("Operação agregada sem campo de agregação.")
        function_name = operation.value.upper()
        selections.append(
            f"{function_name}({_field_expression(config, field)}) "
            f"AS {operation.value}_{field.value}"
        )
    return selections, True


def _order_clause(
    analysis: QueryAnalysis,
    config: _EntitySqlConfig,
) -> str:
    if not analysis.order_by:
        return ""
    fields: list[str] = []
    for order in analysis.order_by:
        if order.field.value == "count":
            # Só chega aqui se QueryAnalysis validou a operação COUNT. Usamos
            # a expressão, não texto vindo da LLM, preservando SQL seguro.
            expression = "COUNT(*)"
        else:
            expression = _field_expression(config, order.field)
        fields.append(f"{expression} {order.direction.value.upper()}")
    return " ORDER BY " + ", ".join(fields)


def executar_consulta_estruturada(
    connection: sqlite3.Connection,
    analysis: QueryAnalysis,
) -> StructuredQueryResult:
    """Executa uma consulta estruturada segura sobre a camada ``raw_asset``.

    A função aceita apenas análises das entidades contrato e contratação. O
    SQL tem estrutura fixa; todos os valores extraídos da pergunta são sempre
    enviados como parâmetros do SQLite.
    """

    if analysis.entity not in _ENTITY_CONFIG:
        raise StructuredQueryError(
            "A ferramenta estruturada suporta somente contrato ou contratação."
        )

    # Conexões abertas diretamente por testes ou scripts não passam pelo
    # evento do SQLAlchemy; registramos a função também aqui.
    connection.create_function(
        "normalizar_busca",
        1,
        normalizar_para_busca,
        deterministic=True,
    )

    config = _ENTITY_CONFIG[analysis.entity]
    clauses, params = _where_clause(analysis, config)
    selections, is_aggregate = _select_clause(analysis, config)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    group_sql = ""
    if analysis.group_by:
        group_sql = " GROUP BY " + ", ".join(
            _field_expression(config, field) for field in analysis.group_by
        )

    limit = min(max(analysis.limit or 20, 1), 100)
    query = (
        _latest_assets_cte()
        + " SELECT "
        + ", ".join(selections)
        + " FROM latest_assets AS asset"
        + where_sql
        + group_sql
        + _order_clause(analysis, config)
        + " LIMIT ?"
    )

    import time

    previous_query_only = int(
        connection.execute("PRAGMA query_only").fetchone()[0]
    )
    deadline = time.monotonic() + 5.0

    def query_deadline_exceeded() -> int:
        return int(time.monotonic() >= deadline)

    query_only_enabled = False
    progress_handler_enabled = False
    try:
        connection.execute("PRAGMA query_only = ON")
        query_only_enabled = True
        connection.set_progress_handler(query_deadline_exceeded, 1000)
        progress_handler_enabled = True
        cursor = connection.execute(query, [config.resource_type, *params, limit])
        columns = [description[0] for description in cursor.description or []]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise StructuredQueryError(
                "A consulta estruturada excedeu o tempo limite de 5 segundos."
            ) from exc
        raise
    finally:
        if progress_handler_enabled:
            connection.set_progress_handler(None, 0)
        if query_only_enabled:
            connection.execute(
                f"PRAGMA query_only = {previous_query_only}"
            )

    return StructuredQueryResult(
        entity=analysis.entity,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        limit_applied=limit,
    )
