"""Consulta determinística e somente leitura sobre a base PNCP normalizada.

O SQL é construído exclusivamente a partir de mapeamentos internos. Valores
extraídos da pergunta são sempre enviados como parâmetros ao SQLite.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.text_normalization import normalizar_para_busca

from .financial_quality import evaluate_financial_quality
from .coverage import avaliar_cobertura
from .schemas import (
    QueryAnalysis,
    QueryEntity,
    StructuredField,
    StructuredOperation,
    StructuredQueryResult,
    StructuredWarning,
    ValueFilterField,
)
from .structured_tool import StructuredQueryError
from .unit_normalization import safe_kg_factor


@dataclass(frozen=True)
class _NormalizedEntitySqlConfig:
    from_sql: str
    id_expression: str
    required_tables: frozenset[str]
    fields: dict[StructuredField, str]
    id_alias: str = "numero_controle_pncp"
    list_fields: tuple[StructuredField, ...] | None = None
    default_value_field: StructuredField = StructuredField.VALOR_GLOBAL
    aggregate_cents_fields: dict[StructuredField, str] | None = None
    weighted_value_cents_expression: str | None = None
    weighted_quantity_expression: str | None = None
    weighted_unit_expression: str | None = None


_ENTITY_CONFIG: dict[QueryEntity, _NormalizedEntitySqlConfig] = {
    QueryEntity.CONTRATO: _NormalizedEntitySqlConfig(
        from_sql=(
            "contrato AS entity "
            "JOIN unidade AS unit "
            "ON unit.orgao_cnpj = entity.orgao_cnpj "
            "AND unit.codigo_unidade = entity.codigo_unidade "
            "JOIN orgao AS agency ON agency.cnpj = entity.orgao_cnpj "
            "LEFT JOIN fornecedor AS supplier "
            "ON supplier.tipo_pessoa = entity.tipo_pessoa_fornecedor "
            "AND supplier.ni_fornecedor = entity.ni_fornecedor "
            "LEFT JOIN contratacao AS purchase "
            "ON purchase.numero_controle_pncp = entity.contratacao_id"
        ),
        id_expression="entity.numero_controle_pncp",
        required_tables=frozenset(
            {"contrato", "unidade", "orgao", "fornecedor", "contratacao"}
        ),
        fields={
            StructuredField.VALOR_GLOBAL: (
                "CAST(entity.valor_global_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_INICIAL: (
                "CAST(entity.valor_inicial_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_ACUMULADO: (
                "CAST(entity.valor_acumulado_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_PARCELA: (
                "CAST(entity.valor_parcela_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_ESTIMADO: (
                "CAST(entity.valor_inicial_centavos AS REAL) / 100.0"
            ),
            StructuredField.ANO: "entity.ano_contrato",
            StructuredField.DATA_PUBLICACAO: "entity.data_publicacao_pncp",
            StructuredField.MUNICIPIO: "unit.municipio_nome",
            StructuredField.ORGAO: "agency.razao_social",
            StructuredField.FORNECEDOR: (
                "COALESCE(supplier.nome_razao_social, "
                "entity.nome_fornecedor_informado)"
            ),
            StructuredField.MODALIDADE: "purchase.modalidade_nome",
            StructuredField.OBJETO: "entity.objeto_contrato",
            StructuredField.CNPJ_ORGAO: "agency.cnpj",
            StructuredField.ESFERA: "agency.esfera_id",
            StructuredField.CODIGO_UNIDADE: "entity.codigo_unidade",
            StructuredField.DATA_ASSINATURA: "entity.data_assinatura",
            StructuredField.NUMERO_PARCELAS: "entity.numero_parcelas",
        },
        list_fields=(
            StructuredField.ANO,
            StructuredField.DATA_PUBLICACAO,
            StructuredField.DATA_ASSINATURA,
            StructuredField.MUNICIPIO,
            StructuredField.ORGAO,
            StructuredField.CNPJ_ORGAO,
            StructuredField.ESFERA,
            StructuredField.CODIGO_UNIDADE,
            StructuredField.FORNECEDOR,
            StructuredField.MODALIDADE,
            StructuredField.OBJETO,
            StructuredField.VALOR_INICIAL,
            StructuredField.VALOR_GLOBAL,
            StructuredField.VALOR_ACUMULADO,
            StructuredField.VALOR_PARCELA,
            StructuredField.NUMERO_PARCELAS,
        ),
        aggregate_cents_fields={
            StructuredField.VALOR_GLOBAL: "entity.valor_global_centavos",
            StructuredField.VALOR_INICIAL: "entity.valor_inicial_centavos",
            StructuredField.VALOR_ACUMULADO: "entity.valor_acumulado_centavos",
            StructuredField.VALOR_PARCELA: "entity.valor_parcela_centavos",
            StructuredField.VALOR_ESTIMADO: "entity.valor_inicial_centavos",
        },
    ),
    QueryEntity.CONTRATACAO: _NormalizedEntitySqlConfig(
        from_sql=(
            "contratacao AS entity "
            "JOIN unidade AS unit "
            "ON unit.orgao_cnpj = entity.orgao_cnpj "
            "AND unit.codigo_unidade = entity.codigo_unidade "
            "JOIN orgao AS agency ON agency.cnpj = entity.orgao_cnpj"
        ),
        id_expression="entity.numero_controle_pncp",
        required_tables=frozenset({"contratacao", "unidade", "orgao"}),
        fields={
            StructuredField.VALOR_GLOBAL: (
                "CAST(COALESCE(entity.valor_total_homologado_centavos, "
                "entity.valor_total_estimado_centavos) AS REAL) / 100.0"
            ),
            StructuredField.VALOR_ESTIMADO: (
                "CAST(entity.valor_total_estimado_centavos AS REAL) / 100.0"
            ),
            StructuredField.ANO: "entity.ano_compra",
            StructuredField.DATA_PUBLICACAO: "entity.data_publicacao_pncp",
            StructuredField.MUNICIPIO: "unit.municipio_nome",
            StructuredField.ORGAO: "agency.razao_social",
            StructuredField.MODALIDADE: "entity.modalidade_nome",
            StructuredField.OBJETO: "entity.objeto_compra",
            StructuredField.CNPJ_ORGAO: "agency.cnpj",
            StructuredField.ESFERA: "agency.esfera_id",
            StructuredField.CODIGO_UNIDADE: "entity.codigo_unidade",
        },
        aggregate_cents_fields={
            StructuredField.VALOR_GLOBAL: (
                "COALESCE(entity.valor_total_homologado_centavos, "
                "entity.valor_total_estimado_centavos)"
            ),
            StructuredField.VALOR_ESTIMADO: "entity.valor_total_estimado_centavos",
        },
    ),
    QueryEntity.CONTRATACAO_ITEM: _NormalizedEntitySqlConfig(
        from_sql=(
            "contratacao_item AS entity "
            "JOIN contratacao AS purchase "
            "ON purchase.numero_controle_pncp = entity.numero_controle_pncp "
            "JOIN unidade AS unit "
            "ON unit.orgao_cnpj = purchase.orgao_cnpj "
            "AND unit.codigo_unidade = purchase.codigo_unidade "
            "JOIN orgao AS agency ON agency.cnpj = purchase.orgao_cnpj"
        ),
        id_expression="entity.numero_controle_pncp",
        required_tables=frozenset(
            {"contratacao_item", "contratacao", "unidade", "orgao"}
        ),
        fields={
            StructuredField.VALOR_ESTIMADO: (
                "CAST(entity.valor_total_estimado_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_TOTAL: (
                "CAST(entity.valor_total_estimado_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_UNITARIO: (
                "CAST(entity.valor_unitario_estimado_centavos AS REAL) / 100.0"
            ),
            StructuredField.QUANTIDADE: "entity.quantidade",
            StructuredField.ANO: "purchase.ano_compra",
            StructuredField.DATA_PUBLICACAO: "purchase.data_publicacao_pncp",
            StructuredField.MUNICIPIO: "unit.municipio_nome",
            StructuredField.ORGAO: "agency.razao_social",
            StructuredField.CNPJ_ORGAO: "agency.cnpj",
            StructuredField.ESFERA: "agency.esfera_id",
            StructuredField.CODIGO_UNIDADE: "purchase.codigo_unidade",
            StructuredField.MODALIDADE: "purchase.modalidade_nome",
            StructuredField.OBJETO: "entity.descricao",
            StructuredField.NUMERO_ITEM: "entity.numero_item",
            StructuredField.UNIDADE_MEDIDA: "entity.unidade_medida",
            StructuredField.CATEGORIA: "entity.categoria_item_nome",
            StructuredField.SITUACAO: "entity.situacao_item_nome",
        },
        list_fields=(
            StructuredField.NUMERO_ITEM,
            StructuredField.OBJETO,
            StructuredField.QUANTIDADE,
            StructuredField.UNIDADE_MEDIDA,
            StructuredField.VALOR_UNITARIO,
            StructuredField.VALOR_TOTAL,
            StructuredField.CATEGORIA,
            StructuredField.SITUACAO,
            StructuredField.ANO,
            StructuredField.DATA_PUBLICACAO,
            StructuredField.MODALIDADE,
            StructuredField.ORGAO,
            StructuredField.CNPJ_ORGAO,
            StructuredField.ESFERA,
            StructuredField.CODIGO_UNIDADE,
            StructuredField.MUNICIPIO,
        ),
        default_value_field=StructuredField.VALOR_ESTIMADO,
        aggregate_cents_fields={
            StructuredField.VALOR_ESTIMADO: "entity.valor_total_estimado_centavos",
            StructuredField.VALOR_TOTAL: "entity.valor_total_estimado_centavos",
            StructuredField.VALOR_UNITARIO: "entity.valor_unitario_estimado_centavos",
        },
    ),
    QueryEntity.RESULTADO_ITEM: _NormalizedEntitySqlConfig(
        from_sql=(
            "resultado_item AS entity "
            "JOIN contratacao_item AS item "
            "ON item.numero_controle_pncp = entity.numero_controle_pncp "
            "AND item.numero_item = entity.numero_item "
            "JOIN contratacao AS purchase "
            "ON purchase.numero_controle_pncp = entity.numero_controle_pncp "
            "JOIN unidade AS unit "
            "ON unit.orgao_cnpj = purchase.orgao_cnpj "
            "AND unit.codigo_unidade = purchase.codigo_unidade "
            "JOIN orgao AS agency ON agency.cnpj = purchase.orgao_cnpj "
            "LEFT JOIN fornecedor AS supplier "
            "ON supplier.tipo_pessoa = entity.tipo_pessoa_fornecedor "
            "AND supplier.ni_fornecedor = entity.ni_fornecedor"
        ),
        id_expression="entity.numero_controle_pncp",
        required_tables=frozenset(
            {
                "resultado_item", "contratacao_item", "contratacao",
                "unidade", "orgao", "fornecedor",
            }
        ),
        fields={
            StructuredField.VALOR_HOMOLOGADO: (
                "CAST(entity.valor_total_homologado_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_TOTAL: (
                "CAST(entity.valor_total_homologado_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_UNITARIO: (
                "CAST(entity.valor_unitario_homologado_centavos AS REAL) / 100.0"
            ),
            StructuredField.QUANTIDADE: "entity.quantidade_homologada",
            StructuredField.ANO: "purchase.ano_compra",
            StructuredField.DATA_PUBLICACAO: "purchase.data_publicacao_pncp",
            StructuredField.DATA_RESULTADO: "entity.data_resultado",
            StructuredField.MUNICIPIO: "unit.municipio_nome",
            StructuredField.ORGAO: "agency.razao_social",
            StructuredField.CNPJ_ORGAO: "agency.cnpj",
            StructuredField.ESFERA: "agency.esfera_id",
            StructuredField.CODIGO_UNIDADE: "purchase.codigo_unidade",
            StructuredField.FORNECEDOR: (
                "COALESCE(supplier.nome_razao_social, entity.ni_fornecedor)"
            ),
            StructuredField.MODALIDADE: "purchase.modalidade_nome",
            StructuredField.OBJETO: "item.descricao",
            StructuredField.NUMERO_ITEM: "entity.numero_item",
            StructuredField.SEQUENCIAL_RESULTADO: "entity.sequencial_resultado",
            StructuredField.UNIDADE_MEDIDA: "item.unidade_medida",
            StructuredField.CATEGORIA: "item.categoria_item_nome",
            StructuredField.SITUACAO: "entity.situacao_resultado_nome",
            StructuredField.BENEFICIO_ME_EPP: "entity.aplicacao_beneficio_me_epp",
            StructuredField.QUALIDADE_DADO: "entity.data_quality_status",
        },
        list_fields=(
            StructuredField.NUMERO_ITEM,
            StructuredField.SEQUENCIAL_RESULTADO,
            StructuredField.OBJETO,
            StructuredField.QUANTIDADE,
            StructuredField.UNIDADE_MEDIDA,
            StructuredField.VALOR_UNITARIO,
            StructuredField.VALOR_TOTAL,
            StructuredField.FORNECEDOR,
            StructuredField.CATEGORIA,
            StructuredField.SITUACAO,
            StructuredField.BENEFICIO_ME_EPP,
            StructuredField.QUALIDADE_DADO,
            StructuredField.DATA_RESULTADO,
            StructuredField.ANO,
            StructuredField.DATA_PUBLICACAO,
            StructuredField.MODALIDADE,
            StructuredField.ORGAO,
            StructuredField.CNPJ_ORGAO,
            StructuredField.ESFERA,
            StructuredField.CODIGO_UNIDADE,
            StructuredField.MUNICIPIO,
        ),
        default_value_field=StructuredField.VALOR_HOMOLOGADO,
        aggregate_cents_fields={
            StructuredField.VALOR_HOMOLOGADO: "entity.valor_total_homologado_centavos",
            StructuredField.VALOR_TOTAL: "entity.valor_total_homologado_centavos",
            StructuredField.VALOR_UNITARIO: "entity.valor_unitario_homologado_centavos",
        },
        weighted_value_cents_expression="entity.valor_total_homologado_centavos",
        weighted_quantity_expression="entity.quantidade_homologada",
        weighted_unit_expression="item.unidade_medida",
    ),
    QueryEntity.ATA_REGISTRO_PRECO: _NormalizedEntitySqlConfig(
        from_sql=(
            "ata_registro_preco AS entity "
            "JOIN unidade AS unit "
            "ON unit.orgao_cnpj = entity.orgao_cnpj "
            "AND unit.codigo_unidade = entity.codigo_unidade "
            "JOIN orgao AS agency ON agency.cnpj = entity.orgao_cnpj"
        ),
        id_expression="entity.numero_controle_pncp_ata",
        id_alias="numero_controle_pncp_ata",
        required_tables=frozenset({"ata_registro_preco", "unidade", "orgao"}),
        fields={
            StructuredField.ANO: "entity.ano_ata",
            StructuredField.DATA_PUBLICACAO: "entity.data_publicacao_pncp",
            StructuredField.DATA_ASSINATURA: "entity.data_assinatura",
            StructuredField.VIGENCIA_INICIO: "entity.vigencia_inicio",
            StructuredField.VIGENCIA_FIM: "entity.vigencia_fim",
            StructuredField.DATA_CANCELAMENTO: "entity.data_cancelamento",
            StructuredField.MUNICIPIO: "unit.municipio_nome",
            StructuredField.ORGAO: "agency.razao_social",
            StructuredField.CNPJ_ORGAO: "agency.cnpj",
            StructuredField.ESFERA: "agency.esfera_id",
            StructuredField.CODIGO_UNIDADE: "entity.codigo_unidade",
            StructuredField.NOME_UNIDADE: "unit.nome_unidade",
            StructuredField.OBJETO: "entity.objeto_contratacao",
            StructuredField.POSSIBILIDADE_ADESAO: "entity.possibilidade_adesao",
            StructuredField.COBERTURA: "entity.coverage_status",
            StructuredField.SITUACAO: (
                "CASE "
                "WHEN entity.cancelado = 1 THEN 'cancelada' "
                "WHEN entity.vigencia_inicio IS NULL OR entity.vigencia_fim IS NULL "
                "THEN 'vigencia desconhecida' "
                "WHEN date(entity.vigencia_inicio) > date('now') THEN 'futura' "
                "WHEN date(entity.vigencia_fim) < date('now') THEN 'vencida' "
                "ELSE 'vigente' END"
            ),
        },
        list_fields=(
            StructuredField.ANO,
            StructuredField.OBJETO,
            StructuredField.SITUACAO,
            StructuredField.POSSIBILIDADE_ADESAO,
            StructuredField.DATA_PUBLICACAO,
            StructuredField.DATA_ASSINATURA,
            StructuredField.VIGENCIA_INICIO,
            StructuredField.VIGENCIA_FIM,
            StructuredField.DATA_CANCELAMENTO,
            StructuredField.ORGAO,
            StructuredField.CNPJ_ORGAO,
            StructuredField.ESFERA,
            StructuredField.CODIGO_UNIDADE,
            StructuredField.NOME_UNIDADE,
            StructuredField.MUNICIPIO,
            StructuredField.COBERTURA,
        ),
    ),
    QueryEntity.PCA: _NormalizedEntitySqlConfig(
        from_sql=(
            "pca AS entity "
            "JOIN unidade AS unit "
            "ON unit.orgao_cnpj = entity.orgao_cnpj "
            "AND unit.codigo_unidade = entity.codigo_unidade "
            "JOIN orgao AS agency ON agency.cnpj = entity.orgao_cnpj"
        ),
        id_expression="entity.id_pca_pncp",
        id_alias="id_pca_pncp",
        required_tables=frozenset({"pca", "unidade", "orgao"}),
        fields={
            StructuredField.ANO: "entity.ano_pca",
            StructuredField.DATA_PUBLICACAO: "entity.data_publicacao_pncp",
            StructuredField.MUNICIPIO: "unit.municipio_nome",
            StructuredField.ORGAO: "agency.razao_social",
            StructuredField.CNPJ_ORGAO: "agency.cnpj",
            StructuredField.ESFERA: "agency.esfera_id",
            StructuredField.CODIGO_UNIDADE: "entity.codigo_unidade",
            StructuredField.NOME_UNIDADE: "COALESCE(entity.nome_unidade, unit.nome_unidade)",
            StructuredField.SITUACAO: "entity.scope_status",
            StructuredField.FORMATO_FONTE: "entity.source_format",
        },
        list_fields=(
            StructuredField.ANO,
            StructuredField.DATA_PUBLICACAO,
            StructuredField.ORGAO,
            StructuredField.CNPJ_ORGAO,
            StructuredField.ESFERA,
            StructuredField.CODIGO_UNIDADE,
            StructuredField.NOME_UNIDADE,
            StructuredField.MUNICIPIO,
            StructuredField.SITUACAO,
            StructuredField.FORMATO_FONTE,
        ),
    ),
    QueryEntity.PCA_ITEM: _NormalizedEntitySqlConfig(
        from_sql=(
            "pca_item AS entity "
            "JOIN pca AS plan ON plan.id_pca_pncp = entity.id_pca_pncp "
            "JOIN unidade AS unit "
            "ON unit.orgao_cnpj = plan.orgao_cnpj "
            "AND unit.codigo_unidade = plan.codigo_unidade "
            "JOIN orgao AS agency ON agency.cnpj = plan.orgao_cnpj"
        ),
        id_expression="entity.id_pca_pncp",
        id_alias="id_pca_pncp",
        required_tables=frozenset({"pca_item", "pca", "unidade", "orgao"}),
        fields={
            StructuredField.VALOR_PLANEJADO: (
                "CAST(entity.valor_total_planejado_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_TOTAL: (
                "CAST(entity.valor_total_planejado_centavos AS REAL) / 100.0"
            ),
            StructuredField.VALOR_UNITARIO: (
                "CAST(entity.valor_unitario_planejado_centavos AS REAL) / 100.0"
            ),
            StructuredField.QUANTIDADE: "entity.quantidade_estimada",
            StructuredField.ANO: "plan.ano_pca",
            StructuredField.DATA_PUBLICACAO: "plan.data_publicacao_pncp",
            StructuredField.DATA_DESEJADA: "entity.data_desejada",
            StructuredField.MUNICIPIO: "unit.municipio_nome",
            StructuredField.ORGAO: "agency.razao_social",
            StructuredField.CNPJ_ORGAO: "agency.cnpj",
            StructuredField.ESFERA: "agency.esfera_id",
            StructuredField.CODIGO_UNIDADE: "plan.codigo_unidade",
            StructuredField.NOME_UNIDADE: "COALESCE(plan.nome_unidade, unit.nome_unidade)",
            StructuredField.OBJETO: "entity.descricao",
            StructuredField.NUMERO_ITEM: "entity.numero_item",
            StructuredField.UNIDADE_MEDIDA: "entity.unidade_fornecimento",
            StructuredField.CATEGORIA: "entity.categoria_item_nome",
        },
        list_fields=(
            StructuredField.NUMERO_ITEM,
            StructuredField.OBJETO,
            StructuredField.QUANTIDADE,
            StructuredField.UNIDADE_MEDIDA,
            StructuredField.VALOR_UNITARIO,
            StructuredField.VALOR_PLANEJADO,
            StructuredField.CATEGORIA,
            StructuredField.DATA_DESEJADA,
            StructuredField.ANO,
            StructuredField.DATA_PUBLICACAO,
            StructuredField.ORGAO,
            StructuredField.CNPJ_ORGAO,
            StructuredField.ESFERA,
            StructuredField.CODIGO_UNIDADE,
            StructuredField.NOME_UNIDADE,
            StructuredField.MUNICIPIO,
        ),
        default_value_field=StructuredField.VALOR_PLANEJADO,
        aggregate_cents_fields={
            StructuredField.VALOR_PLANEJADO: "entity.valor_total_planejado_centavos",
            StructuredField.VALOR_TOTAL: "entity.valor_total_planejado_centavos",
            StructuredField.VALOR_UNITARIO: "entity.valor_unitario_planejado_centavos",
        },
    ),
}

_CONVENTIONAL_AGGREGATE_OPERATIONS = {
    StructuredOperation.SUM,
    StructuredOperation.AVG,
    StructuredOperation.MIN,
    StructuredOperation.MAX,
}
_AGGREGATE_OPERATIONS = {
    *_CONVENTIONAL_AGGREGATE_OPERATIONS,
    StructuredOperation.WEIGHTED_AVG_KG,
}

_CANONICAL_FIELDS = (
    StructuredField.ANO,
    StructuredField.DATA_PUBLICACAO,
    StructuredField.MUNICIPIO,
    StructuredField.ORGAO,
    StructuredField.CNPJ_ORGAO,
    StructuredField.ESFERA,
    StructuredField.CODIGO_UNIDADE,
    StructuredField.FORNECEDOR,
    StructuredField.MODALIDADE,
    StructuredField.OBJETO,
    StructuredField.DATA_ASSINATURA,
    StructuredField.VALOR_GLOBAL,
    StructuredField.VALOR_ESTIMADO,
)

_OBJECT_STOPWORDS = frozenset({"a", "as", "com", "da", "das", "de", "do", "dos", "e", "em", "o", "os", "para"})


def _object_search_terms(value: str) -> list[str]:
    """Produz termos AND conservadores para descrições de objeto.

    A família ``licenc*`` cobre apenas variações morfológicas verificáveis
    (licença, licenças e licenciamento). Os demais termos não recebem stemming
    ou expansão semântica, evitando transformar uma SQL lexical em inferência.
    """

    normalized = normalizar_para_busca(value)
    terms: list[str] = []
    for token in normalized.split():
        if token in _OBJECT_STOPWORDS:
            continue
        selected = "licenc" if token.startswith("licenc") else token
        if selected not in terms:
            terms.append(selected)
    return terms or [normalized]


def _field_expression(
    config: _NormalizedEntitySqlConfig,
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


def _require_normalized_schema(
    connection: sqlite3.Connection,
    config: _NormalizedEntitySqlConfig,
) -> None:
    existing = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = sorted(config.required_tables - existing)
    if missing:
        raise StructuredQueryError(
            "Banco normalizado incompleto; tabelas ausentes: " + ", ".join(missing)
        )


def _where_clause(
    analysis: QueryAnalysis,
    config: _NormalizedEntitySqlConfig,
) -> tuple[list[str], list[object]]:
    filters = analysis.filters
    if filters.tipo_entidade and filters.tipo_entidade is not analysis.entity:
        raise StructuredQueryError("tipo_entidade diverge da entidade analisada.")
    if filters.document_id is not None or filters.tipo_documento:
        raise StructuredQueryError(
            "Filtros de documento pertencem à recuperação documental."
        )

    clauses: list[str] = []
    params: list[object] = []
    if filters.numero_controle_pncp:
        clauses.append(f"{config.id_expression} = ?")
        params.append(filters.numero_controle_pncp)

    for field, value in (
        (StructuredField.ORGAO, filters.orgao),
        (StructuredField.ESFERA, filters.esfera),
        (StructuredField.MUNICIPIO, filters.municipio),
        (StructuredField.FORNECEDOR, filters.fornecedor),
        (StructuredField.MODALIDADE, filters.modalidade),
        (StructuredField.UNIDADE_MEDIDA, filters.unidade_medida),
        (StructuredField.CATEGORIA, filters.categoria),
        (StructuredField.SITUACAO, filters.situacao),
    ):
        if value:
            expression = _field_expression(config, field)
            clauses.append(f"normalizar_busca({expression}) LIKE ?")
            params.append(f"%{normalizar_para_busca(value)}%")

    if filters.objeto:
        expression = _field_expression(config, StructuredField.OBJETO)
        for term in _object_search_terms(filters.objeto):
            clauses.append(f"normalizar_busca({expression}) LIKE ?")
            params.append(f"%{term}%")

    for field, value in (
        (StructuredField.CNPJ_ORGAO, filters.cnpj_orgao),
        (StructuredField.CODIGO_UNIDADE, filters.codigo_unidade),
    ):
        if value:
            clauses.append(f"{_field_expression(config, field)} = ?")
            params.append(value.strip())

    if filters.possibilidade_adesao is not None:
        clauses.append(
            f"{_field_expression(config, StructuredField.POSSIBILIDADE_ADESAO)} = ?"
        )
        params.append(int(filters.possibilidade_adesao))

    if filters.ano is not None:
        clauses.append(f"{_field_expression(config, StructuredField.ANO)} = ?")
        params.append(filters.ano)

    if filters.period_start or filters.period_end:
        temporal_field = StructuredField(filters.campo_data.value)
        selected_date = _field_expression(config, temporal_field)
        if filters.period_start:
            clauses.append(f"date({selected_date}) >= date(?)")
            params.append(filters.period_start.isoformat())
        if filters.period_end:
            clauses.append(f"date({selected_date}) <= date(?)")
            params.append(filters.period_end.isoformat())

    if filters.valor_minimo is not None or filters.valor_maximo is not None:
        value_field = (
            StructuredField(filters.campo_valor.value)
            if filters.campo_valor is not None
            else config.default_value_field
        )
        value_expression = _field_expression(config, value_field)
        if filters.valor_minimo is not None:
            clauses.append(f"{value_expression} >= ?")
            params.append(filters.valor_minimo)
        if filters.valor_maximo is not None:
            clauses.append(f"{value_expression} <= ?")
            params.append(filters.valor_maximo)

    quantity_expression = _field_expression(
        config,
        StructuredField.QUANTIDADE,
        required=False,
    )
    if filters.quantidade_minima is not None:
        if quantity_expression is None:
            raise StructuredQueryError(
                "O filtro de quantidade não está disponível para esta entidade."
            )
        clauses.append(f"{quantity_expression} >= ?")
        params.append(filters.quantidade_minima)
    if filters.quantidade_maxima is not None:
        if quantity_expression is None:
            raise StructuredQueryError(
                "O filtro de quantidade não está disponível para esta entidade."
            )
        clauses.append(f"{quantity_expression} <= ?")
        params.append(filters.quantidade_maxima)
    return clauses, params


def _select_clause(
    analysis: QueryAnalysis,
    config: _NormalizedEntitySqlConfig,
) -> list[str]:
    aggregate_operations = _AGGREGATE_OPERATIONS.intersection(analysis.operations)
    is_aggregate = bool(
        aggregate_operations or StructuredOperation.COUNT in analysis.operations
    )
    if analysis.group_by and not is_aggregate:
        raise StructuredQueryError("group_by exige ao menos uma agregação.")

    if not is_aggregate:
        selections = [f"{config.id_expression} AS {config.id_alias}"]
        for field in config.list_fields or _CANONICAL_FIELDS:
            expression = _field_expression(config, field, required=False)
            selections.append(
                f"{expression} AS {field.value}"
                if expression
                else f"NULL AS {field.value}"
            )
        return selections

    selections = [
        f"{_field_expression(config, field)} AS {field.value}"
        for field in analysis.group_by
    ]
    if StructuredOperation.COUNT in analysis.operations:
        selections.append("COUNT(*) AS count")
    for operation in sorted(
        aggregate_operations - {StructuredOperation.WEIGHTED_AVG_KG},
        key=lambda value: value.value,
    ):
        field = analysis.aggregation_field
        if field is None:
            raise StructuredQueryError("Operação agregada sem campo de agregação.")
        cents_expression = (config.aggregate_cents_fields or {}).get(field)
        if cents_expression:
            calculation = (
                f"CAST({operation.value.upper()}({cents_expression}) AS REAL) / 100.0"
            )
        else:
            calculation = (
                f"{operation.value.upper()}({_field_expression(config, field)})"
            )
        selections.append(
            f"{calculation} AS {operation.value}_{field.value}"
        )
    if StructuredOperation.WEIGHTED_AVG_KG in aggregate_operations:
        if analysis.aggregation_field is not StructuredField.VALOR_HOMOLOGADO:
            raise StructuredQueryError(
                "weighted_avg_kg exige aggregation_field valor_homologado."
            )
        value = config.weighted_value_cents_expression
        quantity = config.weighted_quantity_expression
        unit = config.weighted_unit_expression
        if not value or not quantity or not unit:
            raise StructuredQueryError(
                "Média ponderada por kg não está disponível para esta entidade."
            )
        factor = f"fator_kg_seguro({unit})"
        eligible = (
            f"{factor} IS NOT NULL AND {quantity} IS NOT NULL "
            f"AND {quantity} > 0 AND {factor} > 0 AND {value} IS NOT NULL"
        )
        selections.extend(
            [
                "(CAST(SUM(CASE WHEN "
                f"{eligible} THEN {value} ELSE 0 END) AS REAL) / 100.0) "
                "/ NULLIF(SUM(CASE WHEN "
                f"{eligible} THEN {quantity} * {factor} ELSE 0 END), 0) "
                "AS weighted_avg_valor_homologado_kg",
                f"SUM(CASE WHEN {eligible} THEN 1 ELSE 0 END) AS included_records",
                f"SUM(CASE WHEN {eligible} THEN 0 ELSE 1 END) AS excluded_records",
                f"SUM(CASE WHEN {eligible} THEN {quantity} * {factor} ELSE 0 END) "
                "AS total_quantity_kg",
            ]
        )
    return selections


def _weighted_having_clause(
    analysis: QueryAnalysis,
    config: _NormalizedEntitySqlConfig,
) -> str:
    """Omite grupos nos quais nenhuma massa possui conversão comprovada."""

    if (
        StructuredOperation.WEIGHTED_AVG_KG not in analysis.operations
        or not analysis.group_by
    ):
        return ""
    value = config.weighted_value_cents_expression
    quantity = config.weighted_quantity_expression
    unit = config.weighted_unit_expression
    if not value or not quantity or not unit:
        return ""
    factor = f"fator_kg_seguro({unit})"
    eligible = (
        f"{factor} IS NOT NULL AND {quantity} IS NOT NULL "
        f"AND {quantity} > 0 AND {factor} > 0 AND {value} IS NOT NULL"
    )
    return f" HAVING SUM(CASE WHEN {eligible} THEN 1 ELSE 0 END) > 0"


def _order_clause(
    analysis: QueryAnalysis,
    config: _NormalizedEntitySqlConfig,
) -> str:
    if not analysis.order_by:
        return ""
    values: list[str] = []
    for order in analysis.order_by:
        expression = (
            "COUNT(*)"
            if order.field.value == "count"
            else _field_expression(config, order.field)
        )
        values.append(f"{expression} {order.direction.value.upper()}")
    return " ORDER BY " + ", ".join(values)


def executar_consulta_normalizada(
    connection: sqlite3.Connection,
    analysis: QueryAnalysis,
) -> StructuredQueryResult:
    """Executa o plano validado nas tabelas relacionais normalizadas."""

    config = _ENTITY_CONFIG.get(analysis.entity)
    if config is None:
        raise StructuredQueryError(
            "A entidade solicitada não é suportada pela ferramenta normalizada."
        )
    _require_normalized_schema(connection, config)
    connection.create_function(
        "normalizar_busca",
        1,
        normalizar_para_busca,
        deterministic=True,
    )
    connection.create_function(
        "fator_kg_seguro",
        1,
        safe_kg_factor,
        deterministic=True,
    )

    clauses, params = _where_clause(analysis, config)
    selections = _select_clause(analysis, config)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    group_sql = ""
    if analysis.group_by:
        group_sql = " GROUP BY " + ", ".join(
            _field_expression(config, field) for field in analysis.group_by
        )
    having_sql = _weighted_having_clause(analysis, config)
    limit = analysis.limit or 20
    query = (
        "SELECT "
        + ", ".join(selections)
        + " FROM "
        + config.from_sql
        + where_sql
        + group_sql
        + having_sql
        + _order_clause(analysis, config)
        + " LIMIT ?"
    )
    cursor = connection.execute(query, [*params, limit])
    columns = [description[0] for description in cursor.description or []]
    rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    warnings = evaluate_financial_quality(rows)
    excluded = sum(
        int(row.get("excluded_records") or 0)
        for row in rows
    )
    if excluded:
        warnings.append(
            StructuredWarning(
                code="unit_conversion_exclusions",
                message=(
                    "A média por kg excluiu registros cuja unidade não possui "
                    "equivalência de massa explícita e comprovável."
                ),
                affected_rows=excluded,
            )
        )
    return StructuredQueryResult(
        entity=analysis.entity,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        limit_applied=limit,
        warnings=warnings,
        coverage=avaliar_cobertura(connection, analysis),
    )
