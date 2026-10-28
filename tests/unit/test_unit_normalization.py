from decimal import Decimal

import pytest

from src.query.unit_normalization import UnitConversionStatus, unit_to_kg


@pytest.mark.parametrize(
    ("unit", "factor"),
    [
        ("KG", Decimal("1")),
        ("Quilograma", Decimal("1")),
        ("KILO", Decimal("1")),
        ("Embalagem 1,00 KG", Decimal("1.00")),
        ("Embalagem 5 KG", Decimal("5")),
        ("Pacote com 5KG", Decimal("5")),
        ("Pct de 5kg", Decimal("5")),
        ("Embalagem 500 G", Decimal("0.5")),
        ("Embalagem 600 G", Decimal("0.6")),
    ],
)
def test_converte_apenas_massa_explicita(unit, factor):
    conversion = unit_to_kg(unit)

    assert conversion.status is UnitConversionStatus.EXACT
    assert conversion.factor_to_kg == factor
    assert conversion.normalized_unit == "kg"


@pytest.mark.parametrize(
    "unit",
    [None, "", "UNIDADE", "Pacote", "PCT", "Fardo", "PESSOAS", "QUILOGRAMA 0,00"],
)
def test_exclui_unidade_sem_fator_comprovado(unit):
    conversion = unit_to_kg(unit)

    assert conversion.status is UnitConversionStatus.EXCLUDED
    assert conversion.factor_to_kg is None
