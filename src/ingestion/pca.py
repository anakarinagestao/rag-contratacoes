from __future__ import annotations

import re
from typing import Any, Mapping


PCA_CONTROL_NUMBER_PATTERN = re.compile(
    r"^(?P<cnpj>\d{14})-0-(?P<sequencial>\d+)/(?P<ano>\d{4})$"
)


def pca_plan_context(
    numero_controle: Any,
    *,
    expected_cnpj: Any | None = None,
    expected_year: Any | None = None,
    expected_sequence: Any | None = None,
) -> dict[str, Any]:
    """Valida a identidade PNCP e devolve o contexto do plano completo."""

    match = PCA_CONTROL_NUMBER_PATTERN.fullmatch(str(numero_controle or ""))
    if not match:
        raise ValueError("PCA com numeroControlePNCP inválido")
    context = {
        "cnpj": match.group("cnpj"),
        "ano": int(match.group("ano")),
        "sequencial": int(match.group("sequencial")),
        "numero_controle": str(numero_controle),
    }
    expected = {
        "cnpj": str(expected_cnpj) if expected_cnpj is not None else None,
        "ano": int(expected_year) if expected_year is not None else None,
        "sequencial": (
            int(expected_sequence)
            if expected_sequence is not None
            else None
        ),
    }
    for field, expected_value in expected.items():
        if expected_value is not None and context[field] != expected_value:
            raise ValueError(
                "Identidade do PCA inconsistente com o contexto esperado"
            )
    return context


def validate_complete_pca_plan(
    payload: Any,
    context: Mapping[str, Any],
    source_url: str,
    *,
    expected_unit: Any | None = None,
) -> None:
    """Garante que um payload direto representa um único PCA completo."""

    if not isinstance(payload, Mapping):
        raise ValueError(
            f"PCA completo não retornou objeto em {source_url}"
        )
    payload_control = payload.get("idPcaPncp") or payload.get(
        "numeroControlePNCP"
    )
    if (
        payload_control is not None
        and str(payload_control) != context["numero_controle"]
    ):
        raise ValueError(
            f"PCA completo retornou identidade divergente em {source_url}"
        )
    items = payload.get("itens")
    if not isinstance(items, list):
        raise ValueError(f"PCA completo sem lista de itens em {source_url}")
    seen_items: set[int] = set()
    observed_units = {
        str(value).strip()
        for value in (payload.get("codigoUnidade"), payload.get("uasg"))
        if value not in (None, "")
    }
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError(
                f"PCA completo possui item inválido em {source_url}"
            )
        try:
            item_number = int(item["numeroItem"])
            item_year = int(item["anoPca"])
            item_sequence = int(item["sequencialPca"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"PCA completo possui identidade de item inválida em "
                f"{source_url}"
            ) from None
        if (
            str(item.get("cnpj")) != context["cnpj"]
            or item_year != context["ano"]
            or item_sequence != context["sequencial"]
        ):
            raise ValueError(f"Item fora do PCA esperado em {source_url}")
        if item_number in seen_items:
            raise ValueError(
                f"PCA completo possui numeroItem duplicado em {source_url}"
            )
        seen_items.add(item_number)
        item_unit = item.get("codigoUnidade")
        if item_unit not in (None, ""):
            observed_units.add(str(item_unit).strip())
    if expected_unit is not None:
        expected_text = str(expected_unit).strip()
        if not observed_units:
            raise ValueError(
                f"PCA completo sem unidade verificável em {source_url}"
            )
        if any(
            not _same_unit_code(unit, expected_text)
            for unit in observed_units
        ):
            raise ValueError(
                f"PCA completo fora da unidade esperada em {source_url}"
            )


def _same_unit_code(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    if actual.isdigit() and expected.isdigit():
        return int(actual) == int(expected)
    return False


__all__ = [
    "PCA_CONTROL_NUMBER_PATTERN",
    "pca_plan_context",
    "validate_complete_pca_plan",
]
