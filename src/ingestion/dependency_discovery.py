from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from src.ingestion.checkpoint import stable_target_metadata


DEPENDENCY_PARENTS = {
    "contratacao_item": "contratacao",
    "resultado_item": "contratacao_item",
    "empenho": "contrato",
}


class DependencyDiscoveryError(ValueError):
    pass


def _target_from_parent(
    child_resource: str,
    external_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if child_resource in {"contratacao_item", "empenho"}:
        return {"numero_controle": external_id}
    numero_controle = payload.get("numeroControlePNCPCompra")
    if numero_controle in (None, ""):
        parent, separator, _ = external_id.rpartition("|")
        numero_controle = parent if separator else None
    try:
        numero_item = int(payload["numeroItem"])
    except (KeyError, TypeError, ValueError):
        raise DependencyDiscoveryError(
            "Item alterado sem numeroItem válido"
        ) from None
    if numero_controle in (None, ""):
        raise DependencyDiscoveryError(
            "Item alterado sem contratação pai"
        )
    return {
        "numero_controle": str(numero_controle),
        "numero_item": numero_item,
    }


def discover_dependency_targets(
    connection: sqlite3.Connection,
    parent_run_id: str,
    child_resource: str,
) -> tuple[dict[str, Any], ...]:
    """Extrai pais NEW/UPDATED persistidos por uma execução concluída.

    Registros `UNCHANGED` não criam outra versão RAW e, portanto, não entram
    como nova descoberta. Eles continuam cobertos pela fila de reconciliação.
    """

    try:
        expected_parent = DEPENDENCY_PARENTS[child_resource]
    except KeyError as error:
        raise DependencyDiscoveryError(
            f"Recurso dependente não suportado: {child_resource}"
        ) from error
    run = connection.execute(
        "SELECT resource_type, status FROM ingestion_run WHERE id = ?",
        (parent_run_id,),
    ).fetchone()
    if run is None:
        raise DependencyDiscoveryError("Execução pai não encontrada")
    if run["resource_type"] != expected_parent:
        raise DependencyDiscoveryError(
            "Execução pai incompatível com o recurso dependente"
        )
    if run["status"] != "SUCCESS":
        raise DependencyDiscoveryError(
            "Descoberta exige execução pai concluída com sucesso"
        )

    targets: dict[str, dict[str, Any]] = {}
    rows = connection.execute(
        "SELECT external_id, payload FROM raw_asset "
        "WHERE ingestion_run_id = ? AND resource_type = ? ORDER BY rowid",
        (parent_run_id, expected_parent),
    )
    for external_id, serialized in rows:
        payload = json.loads(serialized)
        target = _target_from_parent(
            child_resource, str(external_id), payload
        )
        key, _ = stable_target_metadata(target)
        targets[key] = target
    return tuple(targets[key] for key in sorted(targets))


__all__ = [
    "DEPENDENCY_PARENTS",
    "DependencyDiscoveryError",
    "discover_dependency_targets",
]
