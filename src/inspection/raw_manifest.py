from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.schema import Manifest, SourceConfig


ALLOWED_NATURES = {"normativa", "estruturada", "conversacional", "agregada"}
ALLOWED_COLLECTORS = {
    "paged",
    "pca",
    "pncp_pca_plans",
    "pncp_price_registry",
    "pncp_contracts",
    "pncp_contract_timeline",
    "pncp_published_contracts",
    "pncp_commitments",
    "pncp_purchase_items",
    "pncp_item_results",
}
ITEM_RESULT_FIELDS = {
    "numeroControlePNCPCompra",
    "numeroItem",
    "sequencialResultado",
}


def validate_source(source: SourceConfig) -> list[str]:
    errors: list[str] = []
    prefix = f"Fonte {source.id!r}"

    if not source.base.startswith(("http://", "https://")):
        errors.append(f"{prefix}: base deve ser uma URL HTTP(S)")
    if not source.endpoint.startswith("/"):
        errors.append(f"{prefix}: endpoint deve começar com '/'")
    if source.natureza not in ALLOWED_NATURES:
        errors.append(
            f"{prefix}: natureza {source.natureza!r} não é uma das "
            f"opções {sorted(ALLOWED_NATURES)}"
        )
    if source.collector not in ALLOWED_COLLECTORS:
        errors.append(f"{prefix}: collector {source.collector!r} não suportado")
    if not source.resource_type:
        errors.append(f"{prefix}: resource_type não pode ser vazio")
    if not source.key_fields:
        errors.append(f"{prefix}: key_fields não pode ser vazio")
    if source.page_size < 1:
        errors.append(f"{prefix}: page_size deve ser maior que zero")
    if source.max_pages is not None and source.max_pages < 1:
        errors.append(f"{prefix}: max_pages deve ser maior que zero")
    if source.max_retries < 0:
        errors.append(f"{prefix}: max_retries não pode ser negativo")
    if source.retry_backoff_seconds < 0:
        errors.append(
            f"{prefix}: retry_backoff_seconds não pode ser negativo"
        )
    if source.max_concurrency < 1:
        errors.append(f"{prefix}: max_concurrency deve ser maior que zero")

    if source.collector == "pncp_item_results":
        for placeholder in ("{cnpj}", "{ano}", "{sequencial}", "{numeroItem}"):
            if placeholder not in source.endpoint:
                errors.append(
                    f"{prefix}: endpoint de resultados não possui {placeholder}"
                )
        if (
            not source.targets
            and not source.target_source_id
            and not source.target_source_ids
        ):
            errors.append(
                f"{prefix}: configure target_source_id para os itens RAW"
            )
        missing_keys = ITEM_RESULT_FIELDS.difference(source.key_fields)
        if missing_keys:
            errors.append(
                f"{prefix}: key_fields sem {', '.join(sorted(missing_keys))}"
            )

    if source.collector == "pncp_purchase_items":
        for placeholder in ("{cnpj}", "{ano}", "{sequencial}"):
            if placeholder not in source.endpoint:
                errors.append(
                    f"{prefix}: endpoint de itens não possui {placeholder}"
                )
        if (
            not source.targets
            and not source.target_source_id
            and not source.target_source_ids
        ):
            errors.append(
                f"{prefix}: configure targets ou target_source_id para as compras"
            )

    if source.collector == "pncp_pca_plans":
        for placeholder in ("{cnpj}", "{ano}", "{sequencial}"):
            if placeholder not in source.endpoint:
                errors.append(
                    f"{prefix}: endpoint de PCA completo não possui {placeholder}"
                )
        discovery = source.discovery_endpoint_template or ""
        for placeholder in ("{cnpj}", "{ano}"):
            if placeholder not in discovery:
                errors.append(
                    f"{prefix}: endpoint de descoberta de PCA não possui "
                    f"{placeholder}"
                )
        if (
            not source.targets
            and not source.target_source_id
            and not source.target_source_ids
        ):
            errors.append(
                f"{prefix}: configure fontes RAW para resolver os órgãos do PCA"
            )
        if source.params.get("anoPca") is None:
            errors.append(f"{prefix}: parâmetro obrigatório ausente: anoPca")

    if source.collector == "pncp_price_registry":
        if source.resource_type != "ata_registro_preco":
            errors.append(
                f"{prefix}: pncp_price_registry exige "
                "resource_type ata_registro_preco"
            )
        if source.item_path != "data":
            errors.append(
                f"{prefix}: pncp_price_registry exige item_path 'data'"
            )
        if "numeroControlePNCPAta" not in source.key_fields:
            errors.append(
                f"{prefix}: key_fields deve conter numeroControlePNCPAta"
            )
        for parameter in ("dataInicial", "dataFinal"):
            if parameter not in source.params:
                errors.append(
                    f"{prefix}: parâmetro obrigatório ausente: {parameter}"
                )
        if (
            not source.targets
            and not source.target_source_id
            and not source.target_source_ids
        ):
            errors.append(
                f"{prefix}: configure fontes RAW para resolver as unidades"
            )

    if source.collector == "pncp_published_contracts":
        if source.resource_type != "contrato":
            errors.append(
                f"{prefix}: pncp_published_contracts exige resource_type contrato"
            )
        if source.item_path != "data":
            errors.append(
                f"{prefix}: pncp_published_contracts exige item_path 'data'"
            )
        if "numeroControlePNCP" not in source.key_fields:
            errors.append(
                f"{prefix}: key_fields deve conter numeroControlePNCP"
            )
        for parameter in ("dataInicial", "dataFinal"):
            if parameter not in source.params:
                errors.append(
                    f"{prefix}: parâmetro obrigatório ausente: {parameter}"
                )
        if (
            not source.targets
            and not source.target_source_id
            and not source.target_source_ids
        ):
            errors.append(
                f"{prefix}: configure fontes RAW para resolver as unidades"
            )

    if source.collector == "pncp_commitments":
        if source.resource_type != "empenho":
            errors.append(
                f"{prefix}: pncp_commitments exige resource_type empenho"
            )
        for placeholder in ("{cnpj}", "{ano}", "{sequencial}"):
            if placeholder not in source.endpoint:
                errors.append(
                    f"{prefix}: endpoint de empenhos não possui {placeholder}"
                )
        if source.item_path != "data":
            errors.append(
                f"{prefix}: pncp_commitments exige item_path 'data'"
            )
        if (
            not source.targets
            and not source.target_source_id
            and not source.target_source_ids
        ):
            errors.append(
                f"{prefix}: configure a fonte RAW dos contratos"
            )
        required_keys = {
            "cnpj",
            "ano",
            "sequencialContrato",
            "sequencialEmpenho",
        }
        missing_keys = required_keys.difference(source.key_fields)
        if missing_keys:
            errors.append(
                f"{prefix}: key_fields sem {', '.join(sorted(missing_keys))}"
            )

    if source.collector == "pncp_contract_timeline":
        if source.resource_type != "contratacao":
            errors.append(
                f"{prefix}: pncp_contract_timeline exige "
                "resource_type contratacao"
            )
        if source.item_path != "data":
            errors.append(
                f"{prefix}: pncp_contract_timeline exige item_path 'data'"
            )
        if "numeroControlePNCP" not in source.key_fields:
            errors.append(
                f"{prefix}: key_fields deve conter numeroControlePNCP"
            )
        for parameter in ("dataInicial", "dataFinal", "uf", "modalidades"):
            if parameter not in source.params:
                errors.append(
                    f"{prefix}: parâmetro obrigatório ausente: {parameter}"
                )
        modalities = source.params.get("modalidades")
        if not isinstance(modalities, list) or not modalities:
            errors.append(f"{prefix}: modalidades deve ser uma lista não vazia")

    if source.collector == "pncp_contracts":
        if source.resource_type != "contratacao":
            errors.append(
                f"{prefix}: pncp_contracts exige resource_type contratacao"
            )
        if source.item_path != "data":
            errors.append(f"{prefix}: pncp_contracts exige item_path 'data'")
        if "numeroControlePNCP" not in source.key_fields:
            errors.append(
                f"{prefix}: key_fields deve conter numeroControlePNCP"
            )
        for parameter in (
            "dataInicial",
            "dataFinal",
            "codigoModalidadeContratacao",
            "uf",
        ):
            if parameter not in source.params:
                errors.append(
                    f"{prefix}: parâmetro obrigatório ausente: {parameter}"
                )

    return errors


def validate_manifest(manifest: Manifest) -> list[str]:
    errors: list[str] = []
    source_ids = [source.id for source in manifest.sources]
    duplicates = sorted(
        source_id
        for source_id in set(source_ids)
        if source_ids.count(source_id) > 1
    )
    if not source_ids:
        errors.append("O manifesto não possui fontes")
    if duplicates:
        errors.append("IDs de fonte duplicados: " + ", ".join(duplicates))
    for source in manifest.sources:
        errors.extend(validate_source(source))
    return errors


def source_summary(source: SourceConfig) -> dict[str, object]:
    return {
        "id": source.id,
        "base_id": source.base_id,
        "natureza": source.natureza,
        "resource_type": source.resource_type,
        "collector": source.collector,
        "url": source.url,
        "parameter_names": sorted(source.params),
        "key_fields": list(source.key_fields),
        "page_size": source.page_size,
        "max_pages": source.max_pages,
        "target_count": len(source.targets),
        "target_source_id": source.target_source_id,
        "target_source_ids": list(source.target_source_ids),
        "max_concurrency": source.max_concurrency,
    }


def inspect_manifest(path: str | Path) -> dict[str, object]:
    manifest_path = Path(path)
    manifest = Manifest.load(manifest_path)
    errors = validate_manifest(manifest)
    return {
        "manifest": str(manifest_path.resolve()),
        "status": "VALID" if not errors else "INVALID",
        "source_count": len(manifest.sources),
        "errors": errors,
        "sources": [source_summary(source) for source in manifest.sources],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Valida e resume as fontes do corpus sem chamar a API"
    )
    parser.add_argument("--manifest", default="corpus_manifest.yaml")
    args = parser.parse_args()

    report = inspect_manifest(args.manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "VALID":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
