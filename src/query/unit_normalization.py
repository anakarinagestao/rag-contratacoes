"""Conversões de unidade estritamente comprováveis pelo texto da unidade.

O normalizador não tenta adivinhar o peso de ``UNIDADE``, ``PACOTE``, ``FARDO``
ou abreviações sem massa explícita. Assim, uma média por quilograma pode
informar exatamente quais registros foram incluídos e quais ficaram de fora.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class UnitConversionStatus(str, Enum):
    EXACT = "EXACT"
    EXCLUDED = "EXCLUDED"


@dataclass(frozen=True)
class UnitConversion:
    status: UnitConversionStatus
    factor_to_kg: Decimal | None
    normalized_unit: str | None
    reason: str


_DIRECT_KG = {
    "kg",
    "kg - quilograma",
    "kilo",
    "kilograma",
    "quilo",
    "quilograma",
    "quilogramas",
    "quilograma(s)",
}

_EXPLICIT_MASS = re.compile(
    r"^(?:embalagem|pacote|pct|saco)(?:\s+(?:com|de))?\s+"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>kg|g)$"
)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())


def unit_to_kg(unit: str | None) -> UnitConversion:
    """Retorna fator apenas quando a massa está explícita e inequívoca."""

    if not unit or not unit.strip():
        return UnitConversion(
            UnitConversionStatus.EXCLUDED,
            None,
            None,
            "unidade ausente",
        )
    normalized = _normalize(unit)
    if normalized in _DIRECT_KG:
        return UnitConversion(
            UnitConversionStatus.EXACT,
            Decimal("1"),
            "kg",
            "equivalência textual direta com quilograma",
        )

    match = _EXPLICIT_MASS.fullmatch(normalized)
    if match:
        try:
            value = Decimal(match.group("value").replace(",", "."))
        except InvalidOperation:
            value = Decimal("0")
        if value > 0:
            factor = value if match.group("unit") == "kg" else value / Decimal("1000")
            return UnitConversion(
                UnitConversionStatus.EXACT,
                factor,
                "kg",
                "massa explícita na unidade de fornecimento",
            )

    return UnitConversion(
        UnitConversionStatus.EXCLUDED,
        None,
        normalized,
        "sem equivalência de massa comprovada",
    )


def safe_kg_factor(unit: str | None) -> float | None:
    """Adaptador SQLite: fator exato ou NULL para exclusão auditável."""

    conversion = unit_to_kg(unit)
    return (
        float(conversion.factor_to_kg)
        if conversion.factor_to_kg is not None
        else None
    )
