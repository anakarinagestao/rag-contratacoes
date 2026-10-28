from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from src.ingestion.scope_inventory import OrganizationUnit, ScopeInventory


EXPECTED_MODALITIES = tuple(range(1, 15))
INVENTORY_COVERAGE_RESOURCES = (
    "pca",
    "contrato",
    "ata_registro_preco",
)


def _nested_scope(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    organization = payload.get("orgaoEntidade") or {}
    unit = payload.get("unidadeOrgao") or {}
    return (
        str(organization.get("cnpj") or "").strip(),
        str(unit.get("codigoUnidade") or "").strip(),
        str(unit.get("ufSigla") or "").strip().upper(),
    )


def _direct_scope(payload: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(payload.get("cnpjOrgao") or "").strip(),
        str(payload.get("codigoUnidadeOrgao") or "").strip(),
    )


def _pca_scopes(payload: Mapping[str, Any]) -> set[tuple[str, str]]:
    cnpj = str(payload.get("orgaoEntidadeCnpj") or "").strip()
    unit = str(payload.get("codigoUnidade") or "").strip()
    if cnpj and unit:
        return {(cnpj, unit)}
    return {
        (
            str(item.get("cnpj") or "").strip(),
            str(item.get("codigoUnidade") or "").strip(),
        )
        for item in payload.get("itens") or ()
        if isinstance(item, Mapping)
        and item.get("cnpj") not in (None, "")
        and item.get("codigoUnidade") not in (None, "")
    }


def build_inventory_document(
    database_path: Path,
    *,
    expected_uf: str = "RJ",
) -> dict[str, Any]:
    """Congela o escopo comprovado pela RAW sem alterar o SQLite."""

    resolved = database_path.expanduser().resolve()
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    records = Counter()
    malformed = Counter()
    outside_scope = Counter()
    observed_modalities: set[int] = set()
    members: set[OrganizationUnit] = set()
    resource_pairs: dict[str, set[tuple[str, str]]] = {
        resource: set() for resource in INVENTORY_COVERAGE_RESOURCES
    }
    pca_plans_inside = 0
    pca_plans_outside = 0
    try:
        rows = connection.execute(
            "SELECT resource_type, payload FROM raw_asset "
            "WHERE resource_type IN "
            "('contratacao', 'contrato', 'pca', 'ata_registro_preco')"
        )
        for resource, serialized in rows:
            records[resource] += 1
            payload = json.loads(serialized)
            if resource == "contratacao":
                cnpj, unit, uf = _nested_scope(payload)
                if not cnpj or not unit or not uf:
                    malformed[resource] += 1
                    continue
                if uf != expected_uf:
                    outside_scope[resource] += 1
                    continue
                members.add(OrganizationUnit(cnpj, unit, uf))
                modality = payload.get("modalidadeId")
                if modality is not None:
                    observed_modalities.add(int(modality))
            elif resource == "contrato":
                cnpj, unit, uf = _nested_scope(payload)
                if not cnpj or not unit or not uf:
                    malformed[resource] += 1
                    continue
                if uf != expected_uf:
                    outside_scope[resource] += 1
                    continue
                resource_pairs[resource].add((cnpj, unit))
            elif resource == "ata_registro_preco":
                pair = _direct_scope(payload)
                if not all(pair):
                    malformed[resource] += 1
                    continue
                resource_pairs[resource].add(pair)
            else:
                pairs = _pca_scopes(payload)
                if not pairs:
                    malformed[resource] += 1
                    continue
                resource_pairs[resource].update(pairs)

        inventory = ScopeInventory(expected_uf, tuple(sorted(members)))
        frozen_pairs = {
            (member.cnpj, member.unit_code) for member in inventory.members
        }

        # Um plano PCA pode conter uma unidade. A contagem por plano evidencia
        # coletas nacionais trazidas por CNPJ, sem incorporar esses pares ao RJ.
        pca_rows = connection.execute(
            "SELECT payload FROM raw_asset WHERE resource_type = 'pca'"
        )
        for (serialized,) in pca_rows:
            pairs = _pca_scopes(json.loads(serialized))
            if pairs and pairs <= frozen_pairs:
                pca_plans_inside += 1
            elif pairs:
                pca_plans_outside += 1

        subset_validation = {
            resource: {
                "observed_pairs": len(resource_pairs[resource]),
                "inside_inventory": len(
                    resource_pairs[resource] & frozen_pairs
                ),
                "outside_inventory": len(
                    resource_pairs[resource] - frozen_pairs
                ),
            }
            for resource in INVENTORY_COVERAGE_RESOURCES
        }
        member_targets = [member.target for member in inventory.members]
        expected_modality_members = [
            f"modalidade:{number}" for number in EXPECTED_MODALITIES
        ]
        return {
            "schema_version": 1,
            "base_id": "pncp",
            "scope": {
                "uf": inventory.uf,
                "evidence": "historical_raw_contratacao",
                "inventory_id": inventory.inventory_id,
                "organization_count": len(
                    {member.cnpj for member in inventory.members}
                ),
                "member_count": len(inventory.members),
                "members": member_targets,
            },
            "coverage_plans": {
                "contratacao": {
                    "name": "pncp-rj-contratacao-modalidades",
                    "member_kind": "modalidade",
                    "members": expected_modality_members,
                },
                **{
                    resource: {
                        "name": f"pncp-rj-{resource}-inventario",
                        "member_kind": "orgao_unidade",
                        "inventory_id": inventory.inventory_id,
                        "members_path": "scope.members",
                        "member_count": len(inventory.members),
                    }
                    for resource in INVENTORY_COVERAGE_RESOURCES
                },
            },
            "validation": {
                "source_database_name": resolved.name,
                "records_by_resource": dict(sorted(records.items())),
                "malformed_scope_records": dict(sorted(malformed.items())),
                "outside_rj_records_with_explicit_uf": dict(
                    sorted(outside_scope.items())
                ),
                "expected_modalities": list(EXPECTED_MODALITIES),
                "observed_modalities": sorted(observed_modalities),
                "expected_modalities_without_observation": sorted(
                    set(EXPECTED_MODALITIES) - observed_modalities
                ),
                "resource_pair_validation": subset_validation,
                "pca_plans_inside_inventory": pca_plans_inside,
                "pca_plans_outside_inventory": pca_plans_outside,
                "completeness_claim": (
                    "relative_to_historical_raw_contratacao_only"
                ),
            },
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Congela o inventário RJ comprovado pela RAW histórica."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists() and not args.force:
        parser.error("arquivo de saída já existe; use --force conscientemente")
    document = build_inventory_document(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "inventory_id": document["scope"]["inventory_id"],
                "member_count": document["scope"]["member_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
