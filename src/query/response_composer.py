"""Composição determinística de respostas para consultas estruturadas.

Os cálculos permanecem sob responsabilidade da SQL Tool. Este módulo apenas
apresenta o resultado validado em linguagem natural, sem chamar uma LLM nem
recalcular contagens, totais ou médias.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .schemas import QueryAnalysis, QueryEntity, StructuredQueryResult
from .coverage import acrescentar_avisos_cobertura


_ENTITY_LABELS = {
    QueryEntity.CONTRATO: ("contrato", "contratos"),
    QueryEntity.CONTRATACAO: ("contratação", "contratações"),
    QueryEntity.CONTRATACAO_ITEM: ("item de contratação", "itens de contratação"),
    QueryEntity.RESULTADO_ITEM: ("resultado homologado", "resultados homologados"),
    QueryEntity.ATA_REGISTRO_PRECO: ("ata de registro de preços", "atas de registro de preços"),
    QueryEntity.PCA: ("PCA", "PCAs"),
    QueryEntity.PCA_ITEM: ("item de PCA", "itens de PCA"),
    QueryEntity.DOCUMENTO: ("documento", "documentos"),
}

_EMPTY_RESULT_LABELS = {
    QueryEntity.CONTRATO: "Nenhum contrato foi encontrado",
    QueryEntity.CONTRATACAO: "Nenhuma contratação foi encontrada",
    QueryEntity.CONTRATACAO_ITEM: "Nenhum item de contratação foi encontrado",
    QueryEntity.RESULTADO_ITEM: "Nenhum resultado homologado foi encontrado",
    QueryEntity.ATA_REGISTRO_PRECO: "Nenhuma ata de registro de preços foi encontrada",
    QueryEntity.PCA: "Nenhum PCA foi encontrado",
    QueryEntity.PCA_ITEM: "Nenhum item de PCA foi encontrado",
    QueryEntity.DOCUMENTO: "Nenhum documento foi encontrado",
}

_FIELD_LABELS = {
    "numero_controle_pncp": "PNCP",
    "numero_controle_pncp_ata": "ata PNCP",
    "id_pca_pncp": "PCA PNCP",
    "ano": "ano",
    "data_publicacao": "publicação",
    "municipio": "município",
    "orgao": "órgão",
    "fornecedor": "fornecedor",
    "modalidade": "modalidade",
    "objeto": "objeto",
    "valor_global": "valor global",
    "valor_inicial": "valor inicial",
    "valor_acumulado": "valor acumulado",
    "valor_parcela": "valor da parcela",
    "valor_estimado": "valor estimado",
    "valor_homologado": "valor homologado",
    "valor_unitario": "valor unitário",
    "valor_total": "valor total",
    "valor_planejado": "valor planejado",
    "quantidade": "quantidade",
    "numero_parcelas": "número de parcelas",
    "numero_item": "item",
    "sequencial_resultado": "resultado",
    "unidade_medida": "unidade",
    "categoria": "categoria",
    "situacao": "situação",
    "esfera": "esfera",
    "cnpj_orgao": "CNPJ do órgão",
    "codigo_unidade": "unidade administrativa",
    "data_assinatura": "assinatura",
    "data_resultado": "data do resultado",
    "data_desejada": "data desejada",
    "vigencia_inicio": "início da vigência",
    "vigencia_fim": "fim da vigência",
    "data_cancelamento": "data de cancelamento",
    "possibilidade_adesao": "permite adesão",
    "cobertura": "cobertura",
    "formato_fonte": "formato da fonte",
    "nome_unidade": "unidade administrativa",
    "count": "quantidade",
    "included_records": "registros incluídos",
    "excluded_records": "registros excluídos",
    "total_quantity_kg": "quantidade total normalizada (kg)",
    "weighted_avg_valor_homologado_kg": "média ponderada homologada por kg",
}


def _money(value: Any) -> str:
    try:
        number = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return str(value)
    rendered = f"{number:,.2f}"
    return "R$ " + rendered.replace(",", "_").replace(".", ",").replace("_", ".")


def _format_value(field: str, value: Any) -> str:
    if field.startswith((
        "sum_valor_", "avg_valor_", "min_valor_", "max_valor_",
        "weighted_avg_valor_",
    )):
        return _money(value)
    if field in {
        "valor_global", "valor_inicial", "valor_acumulado", "valor_parcela",
        "valor_estimado", "valor_homologado",
        "valor_unitario", "valor_total", "valor_planejado",
    }:
        return _money(value)
    if isinstance(value, bool):
        return "sim" if value else "não"
    return str(value)


def _field_label(field: str) -> str:
    if field.startswith("sum_"):
        return "total de " + _FIELD_LABELS.get(field[4:], field[4:].replace("_", " "))
    if field.startswith("avg_"):
        return "média de " + _FIELD_LABELS.get(field[4:], field[4:].replace("_", " "))
    if field.startswith("min_"):
        return "mínimo de " + _FIELD_LABELS.get(field[4:], field[4:].replace("_", " "))
    if field.startswith("max_"):
        return "máximo de " + _FIELD_LABELS.get(field[4:], field[4:].replace("_", " "))
    return _FIELD_LABELS.get(field, field.replace("_", " "))


def _criteria(analysis: QueryAnalysis) -> str:
    filters = analysis.filters
    parts: list[str] = []
    if filters.ano is not None:
        parts.append(f"ano {filters.ano}")
    if filters.period_start is not None or filters.period_end is not None:
        start = filters.period_start.isoformat() if filters.period_start else "início aberto"
        end = filters.period_end.isoformat() if filters.period_end else "fim aberto"
        date_label = {
            "data_publicacao": "publicação",
            "data_assinatura": "assinatura",
            "data_resultado": "resultado",
            "data_desejada": "data desejada",
            "vigencia_fim": "fim da vigência",
        }[filters.campo_data.value]
        parts.append(f"{date_label} entre {start} e {end}")
    if filters.cnpj_orgao:
        parts.append(f"CNPJ do órgão {filters.cnpj_orgao}")
    if filters.esfera:
        parts.append(f"esfera {filters.esfera}")
    if filters.codigo_unidade:
        parts.append(f"unidade administrativa {filters.codigo_unidade}")
    for label, value in (
        ("órgão", filters.orgao),
        ("município", filters.municipio),
        ("fornecedor", filters.fornecedor),
        ("modalidade", filters.modalidade),
        ("objeto", filters.objeto),
        ("unidade", filters.unidade_medida),
        ("categoria", filters.categoria),
        ("situação", filters.situacao),
    ):
        if value:
            parts.append(f"{label} contendo “{value}”")
    if filters.valor_minimo is not None:
        parts.append(f"valor mínimo {_money(filters.valor_minimo)}")
    if filters.valor_maximo is not None:
        parts.append(f"valor máximo {_money(filters.valor_maximo)}")
    if filters.quantidade_minima is not None:
        parts.append(f"quantidade mínima {filters.quantidade_minima}")
    if filters.quantidade_maxima is not None:
        parts.append(f"quantidade máxima {filters.quantidade_maxima}")
    if filters.possibilidade_adesao is not None:
        parts.append(
            "adesão permitida"
            if filters.possibilidade_adesao
            else "adesão não permitida"
        )
    return "; ".join(parts) if parts else "sem filtros adicionais"


def _row_summary(row: dict[str, Any]) -> str:
    preferred = (
        "numero_controle_pncp",
        "numero_controle_pncp_ata",
        "id_pca_pncp",
        "numero_item",
        "sequencial_resultado",
        "orgao",
        "fornecedor",
        "modalidade",
        "municipio",
        "ano",
        "data_publicacao",
        "data_assinatura",
        "data_resultado",
        "data_desejada",
        "vigencia_inicio",
        "vigencia_fim",
        "data_cancelamento",
        "valor_global",
        "valor_inicial",
        "valor_acumulado",
        "valor_parcela",
        "valor_estimado",
        "valor_homologado",
        "valor_unitario",
        "valor_total",
        "valor_planejado",
        "quantidade",
        "numero_parcelas",
        "unidade_medida",
        "possibilidade_adesao",
        "objeto",
        "situacao",
        "possibilidade_adesao",
        "cobertura",
        "formato_fonte",
        "nome_unidade",
    )
    values = [
        f"{_field_label(field)}: {_format_value(field, row[field])}"
        for field in preferred
        if row.get(field) not in (None, "")
    ]
    if not values:
        values = [
            f"{_field_label(field)}: {_format_value(field, value)}"
            for field, value in row.items()
            if value not in (None, "")
        ]
    return "; ".join(values)


def compose_structured_answer(
    analysis: QueryAnalysis,
    result: StructuredQueryResult,
) -> str:
    """Transforma linhas SQL já calculadas em uma explicação auditável."""

    singular, plural = _ENTITY_LABELS.get(
        analysis.entity,
        (analysis.entity.value, analysis.entity.value),
    )
    criteria = _criteria(analysis)
    if not result.rows:
        empty = _EMPTY_RESULT_LABELS.get(
            analysis.entity,
            f"Nenhum {singular} foi encontrado",
        )
        answer = f"{empty} ({criteria})."
        return _append_warnings(answer, result)

    aggregate_columns = {
        column
        for column in result.columns
        if column in {
            "count", "included_records", "excluded_records", "total_quantity_kg",
        }
        or column.startswith(("sum_", "avg_", "min_", "max_", "weighted_avg_"))
    }
    if aggregate_columns:
        if len(result.rows) == 1 and not analysis.group_by:
            values = "; ".join(
                f"{_field_label(column)}: {_format_value(column, result.rows[0][column])}"
                for column in result.columns
                if column in aggregate_columns
            )
            answer = f"Resultado para {plural}: {values} ({criteria})."
            return _append_warnings(answer, result)

        lines = []
        for row in result.rows[:10]:
            values = "; ".join(
                f"{_field_label(column)}: {_format_value(column, row[column])}"
                for column in result.columns
                if row.get(column) is not None
            )
            lines.append(f"- {values}")
        suffix = ""
        if len(result.rows) > 10:
            suffix = f"\nA tabela contém mais {len(result.rows) - 10} grupos."
        answer = f"Resultado agregado de {plural} ({criteria}):\n" + "\n".join(lines) + suffix
        return _append_warnings(answer, result)

    lines = [f"{index}. {_row_summary(row)}" for index, row in enumerate(result.rows[:3], 1)]
    suffix = ""
    if result.row_count > 3:
        suffix = f"\nA tabela apresenta mais {result.row_count - 3} registros desta página."
    entity_label = singular if result.row_count == 1 else plural
    answer = (
        f"A consulta retornou {result.row_count} {entity_label} ({criteria}; "
        f"limite aplicado: {result.limit_applied}).\n"
        + "\n".join(lines)
        + suffix
    )
    return _append_warnings(answer, result)


def _append_warnings(answer: str, result: StructuredQueryResult) -> str:
    answer = acrescentar_avisos_cobertura(answer, result)
    if not result.warnings:
        return answer
    warnings = "\n".join(f"- {warning.message}" for warning in result.warnings)
    return f"{answer}\n\nAlertas de qualidade:\n{warnings}"
