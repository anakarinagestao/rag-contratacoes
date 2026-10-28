from __future__ import annotations

import argparse
import json
from src.ingestion.change_detector import asset_key
from src.ingestion.hashing import payload_hash
from src.ingestion.pncp_client import PNCPClient
from src.ingestion.pncp_collector import (
    ItemResultCollector,
    PCACollector,
    PNCPCollector,
    PurchaseItemCollector,
)
from src.schema import Manifest, SourceConfig
from src.schema import CollectionCheckpoint


def collector_for(source: SourceConfig):
    collector_name = source.collector
    if collector_name == "paged" and source.resource_type == "pca":
        collector_name = "pca"
    collector_classes = {
        "paged": PNCPCollector,
        "pca": PCACollector,
        "pncp_purchase_items": PurchaseItemCollector,
        "pncp_item_results": ItemResultCollector,
    }
    try:
        return collector_classes[collector_name](PNCPClient())
    except KeyError as error:
        raise ValueError(f"Coletor não suportado: {collector_name}") from error


def safe_record_summary(record, source: SourceConfig) -> dict[str, object]:
    serialized = json.dumps(
        record.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "cursor": record.cursor,
        "ordinal": record.ordinal,
        "external_id": record.external_id
        or asset_key(record.payload, source.key_fields),
        "payload_hash": payload_hash(record.payload),
        "payload_size_bytes": len(serialized),
        "field_names": sorted(record.payload),
        "payload_omitted": True,
    }


def preview_source(
    manifest_path: str,
    source_id: str,
    limit: int,
) -> dict[str, object]:
    if limit < 1:
        raise ValueError("limit deve ser maior que zero")
    manifest = Manifest.load(manifest_path)
    source = manifest.source(source_id)
    records = []
    for collected in collector_for(source).collect(source):
        if isinstance(collected, CollectionCheckpoint):
            continue
        records.append(collected)
        if len(records) == limit:
            break
    return {
        "source": source.id,
        "resource_type": source.resource_type,
        "collector": source.collector,
        "records_previewed": len(records),
        "persisted": False,
        "records": [safe_record_summary(record, source) for record in records],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consulta uma amostra real do PNCP sem gravar no SQLite"
    )
    parser.add_argument("--manifest", default="corpus_manifest.yaml")
    parser.add_argument("--source", required=True)
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()

    report = preview_source(args.manifest, args.source, args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
