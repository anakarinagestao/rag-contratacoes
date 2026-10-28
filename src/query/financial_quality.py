"""Heurísticas conservadoras para sinalizar dados exibidos suspeitos.

Um alerta nunca altera, exclui ou corrige o valor retornado pela SQL Tool. Os
limiares servem apenas para pedir revisão da proveniência antes de interpretar
uma magnitude incomum como erro ou como fato econômico consolidado.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .schemas import StructuredWarning


EXTREME_FINANCIAL_VALUE = Decimal("100000000000")  # R$ 100 bilhões
DIVERGENCE_RATIO = Decimal("1000")


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _financial_columns(rows: list[dict[str, Any]]) -> set[str]:
    prefixes = (
        "valor_",
        "sum_valor_",
        "avg_valor_",
        "min_valor_",
        "max_valor_",
        "weighted_avg_valor_",
    )
    return {
        column
        for row in rows
        for column in row
        if column.startswith(prefixes)
    }


def evaluate_financial_quality(rows: list[dict[str, Any]]) -> list[StructuredWarning]:
    """Sinaliza somente valores e datas presentes no resultado exibido."""

    columns = _financial_columns(rows)
    negative_rows: set[int] = set()
    extreme_rows: set[int] = set()
    divergent_rows: set[int] = set()
    invalid_date_rows: set[int] = set()
    for index, row in enumerate(rows):
        values: list[Decimal] = []
        for column in columns:
            value = _decimal(row.get(column))
            if value is None:
                continue
            values.append(value)
            if value < 0:
                negative_rows.add(index)
            if abs(value) >= EXTREME_FINANCIAL_VALUE:
                extreme_rows.add(index)

        # Compara somente campos financeiros do registro. Comparar SUM com AVG
        # em uma linha agregada produziria um falso alerta por construção.
        positive = [
            value
            for column in columns
            if column.startswith("valor_")
            if (value := _decimal(row.get(column))) is not None and value > 0
        ]
        if len(positive) >= 2 and max(positive) / min(positive) >= DIVERGENCE_RATIO:
            divergent_rows.add(index)

        for field, value in row.items():
            if not field.startswith("data_") or value in (None, ""):
                continue
            if isinstance(value, (date, datetime)):
                continue
            try:
                date.fromisoformat(str(value)[:10])
            except (TypeError, ValueError):
                invalid_date_rows.add(index)

    warnings: list[StructuredWarning] = []
    if negative_rows:
        warnings.append(
            StructuredWarning(
                code="negative_financial_value",
                message=(
                    "O resultado contém valor financeiro negativo. O dado foi "
                    "preservado e deve ter sua proveniência conferida antes do uso."
                ),
                affected_rows=len(negative_rows),
            )
        )
    if extreme_rows:
        warnings.append(
            StructuredWarning(
                code="extreme_financial_value",
                message=(
                    "O resultado contém magnitude financeira igual ou superior a "
                    "R$ 100 bilhões. Isso não prova erro, mas pode distorcer rankings "
                    "e agregados e exige conferência da fonte."
                ),
                affected_rows=len(extreme_rows),
            )
        )
    if divergent_rows:
        warnings.append(
            StructuredWarning(
                code="financial_fields_diverge",
                message=(
                    "Campos financeiros do mesmo registro divergem por fator igual "
                    "ou superior a 1.000. Preserve as semânticas de cada campo e "
                    "confira a fonte antes de compará-los."
                ),
                affected_rows=len(divergent_rows),
            )
        )
    if invalid_date_rows:
        warnings.append(
            StructuredWarning(
                code="invalid_date_value",
                message=(
                    "O resultado contém data em formato inválido. O valor foi "
                    "preservado e deve ser conferido na fonte."
                ),
                affected_rows=len(invalid_date_rows),
            )
        )

    signatures: dict[tuple[tuple[str, str], ...], int] = {}
    duplicate_rows: set[int] = set()
    for index, row in enumerate(rows):
        signature = tuple(sorted((key, repr(value)) for key, value in row.items()))
        previous = signatures.get(signature)
        if previous is not None:
            duplicate_rows.update({previous, index})
        else:
            signatures[signature] = index
    if duplicate_rows:
        warnings.append(
            StructuredWarning(
                code="possible_duplicate_rows",
                message=(
                    "O resultado contém linhas exatamente repetidas. A agregação "
                    "deve ser revisada para evitar dupla contagem."
                ),
                affected_rows=len(duplicate_rows),
            )
        )
    return warnings
