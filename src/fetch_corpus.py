from __future__ import annotations

import argparse
import json
from dataclasses import replace

from .ingest import RawRepository, ingest_records
from .ingestion.pncp_client import PNCPClient
from .ingestion.checkpoint import stable_target_metadata
from .ingestion.pncp_collector import (
    CommitmentCollector,
    ContractCollector,
    ContractTimelineCollector,
    ItemResultCollector,
    PCACollector,
    PCAFullCollector,
    PNCPCollector,
    PriceRegistryCollector,
    PublishedContractCollector,
    PurchaseItemCollector,
)
from .schema import Manifest, SourceConfig, value_at_path

Collector = PNCPCollector


def resolve_source_targets(
    repository: RawRepository, source: SourceConfig
) -> SourceConfig:
    """Resolve alvos a partir da última versão dos ativos RAW."""
    target_source_ids = source.target_source_ids
    if not target_source_ids and source.target_source_id:
        target_source_ids = (source.target_source_id,)
    if not target_source_ids or not source.target_resource_type:
        return source

    if not source.target_field_paths:
        raise ValueError("Fonte dependente sem target_field_paths")

    targets = []
    seen = set()
    if len(target_source_ids) == 1 and hasattr(
        repository, "latest_targets"
    ):
        resolved = repository.latest_targets(
            target_source_ids[0],
            source.target_resource_type,
            dict(source.target_field_paths),
        )
    else:
        assets = []
        for target_source_id in target_source_ids:
            assets.extend(
                repository.latest_assets(
                    target_source_id, source.target_resource_type
                )
            )
        resolved = (
            {
                name: _target_value(
                    asset["payload"], asset["external_id"], path
                )
                for name, path in source.target_field_paths.items()
            }
            for asset in sorted(
                assets, key=lambda item: item["external_id"]
            )
        )
    for target in resolved:
        missing = [
            name for name, value in target.items() if value in (None, "")
        ]
        if missing:
            raise ValueError(
                "Ativo RAW sem campos para resolver target: "
                + ", ".join(missing)
            )
        identity = tuple(target.items())
        if identity not in seen:
            seen.add(identity)
            targets.append(target)
    if not targets:
        return source
    return replace(source, targets=tuple(targets))


def prepare_window_targets(
    sync_repository,
    source: SourceConfig,
    window_id: str,
    *,
    qualifier: str | None = None,
) -> SourceConfig:
    """Registra e filtra alvos v2 por identidade, nunca pela posição.

    A ordem da lista pode mudar entre execuções. Um alvo só é retirado da
    retomada quando a mesma identidade e o mesmo fingerprint já estiverem
    confirmados na janela.
    """

    pending = []
    for target in source.targets:
        target_key, fingerprint = stable_target_metadata(target, qualifier)
        row = sync_repository.register_target(
            window_id, target_key, fingerprint
        )
        if not (
            row["status"] == "COMPLETED"
            and row["processed_fingerprint"] == fingerprint
        ):
            pending.append(target)
    return replace(source, targets=tuple(pending))


def _target_value(payload: dict, external_id: str, path: str):
    if path == "@external_id":
        return external_id
    if path == "@parent_external_id":
        parent, separator, _ = external_id.rpartition("|")
        return parent if separator else None
    return value_at_path(payload, path)


def run(
    manifest_path: str,
    source_id: str,
    mode: str,
    database_url: str | None = None,
) -> None:
    manifest = Manifest.load(manifest_path)
    source = manifest.source(source_id)
    repository = RawRepository(database_url)
    try:
        source = resolve_source_targets(repository, source)
        checkpoint = repository.get_checkpoint(
            source.id, source.resource_type
        )
        collector_classes = {
            "paged": PNCPCollector,
            "pca": PCACollector,
            "pncp_pca_plans": PCAFullCollector,
            "pncp_price_registry": PriceRegistryCollector,
            "pncp_published_contracts": PublishedContractCollector,
            "pncp_commitments": CommitmentCollector,
            "pncp_contracts": ContractCollector,
            "pncp_contract_timeline": ContractTimelineCollector,
            "pncp_purchase_items": PurchaseItemCollector,
            "pncp_item_results": ItemResultCollector,
        }
        collector_name = source.collector
        if collector_name == "paged" and source.resource_type == "pca":
            collector_name = "pca"
        try:
            collector_class = collector_classes[collector_name]
        except KeyError as error:
            raise ValueError(
                f"Coletor não suportado: {collector_name}"
            ) from error
        start_page = (
            checkpoint + 1
            if mode == "update"
            and checkpoint
            else 1
        )
        result = ingest_records(
            repository,
            source.id,
            source.key_fields,
            collector_class(PNCPClient()).collect(
                source, start_page=start_page
            ),
            mode=mode,
            resource_type=source.resource_type,
            base_id=source.base_id,
            natureza=source.natureza,
            query_params=dict(source.params),
            sensitivity=source.sensitivity,
            initial_checkpoint=(checkpoint if mode == "update" else 0),
            window_start=str(source.params.get("dataInicial") or "") or None,
            window_end=str(source.params.get("dataFinal") or "") or None,
        )
        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta PNCP e persiste a camada RAW"
    )
    parser.add_argument("command", choices=("fetch", "update"))
    parser.add_argument("--manifest", default="corpus_manifest.yaml")
    parser.add_argument("--source", default=None)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    manifest = Manifest.load(args.manifest)
    if not manifest.sources and not args.source:
        parser.error("O manifesto não possui fontes")
    source_id = args.source or manifest.sources[0].id
    run(args.manifest, source_id, args.command, args.database_url)


if __name__ == "__main__":
    main()
