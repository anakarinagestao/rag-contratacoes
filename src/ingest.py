from __future__ import annotations

import json
from datetime import date, datetime
from typing import Iterable

from app.core.database import initialize_database
from app.core.database.repositories import (
    CheckpointRepository,
    RawRepository as RawAssetRepository,
    RunRepository,
    SyncRepository,
)
from app.core.database.repositories.sync_repository import (
    LeaseConflictError,
    LeaseExpiredError,
)
from .ingestion.change_detector import asset_key, detect_change
from .ingestion.hashing import payload_hash
from .ingestion.identity import (
    CanonicalIdentity,
    build_observation_key,
    extract_source_timestamps,
)
from .obs import JsonlLogger
from .schema import CollectionCheckpoint, CollectedRecord, IngestionResult


class RawRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.connection = initialize_database(database_url)
        self.assets = RawAssetRepository(self.connection)
        self.runs = RunRepository(self.connection)
        self.checkpoints = CheckpointRepository(self.connection)

    def close(self) -> None:
        self.connection.close()

    def _begin_immediate(self) -> None:
        if self.connection.in_transaction:
            raise RuntimeError(
                "Persistência RAW atômica exige conexão sem transação pendente"
            )
        self.connection.execute("BEGIN IMMEDIATE")

    def start_run(
        self,
        source_id: str,
        resource_type: str = "records",
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> str:
        return self.runs.start(
            source_id,
            resource_type,
            window_start=window_start,
            window_end=window_end,
        )

    def get_checkpoint(
        self,
        source_id: str,
        resource_type: str = "records",
    ) -> int:
        row = self.checkpoints.get_checkpoint(source_id, resource_type)
        return int(row["last_page"]) if row else 0

    def save_checkpoint(
        self,
        source_id: str,
        resource_type: str,
        run_id: str,
        last_page: int,
    ) -> None:
        self.checkpoints.update_checkpoint(
            source_id, resource_type, run_id, last_page
        )

    def save_progress(
        self,
        source_id: str,
        resource_type: str,
        run_id: str,
        last_page: int,
    ) -> None:
        self.checkpoints.update_progress(
            source_id, resource_type, run_id, last_page
        )

    def fail_progress(
        self,
        source_id: str,
        resource_type: str,
        run_id: str,
    ) -> None:
        self.checkpoints.mark_failed(source_id, resource_type, run_id)

    def latest_payloads(
        self, source_id: str, resource_type: str
    ) -> list[dict]:
        return [
            json.loads(row["payload"])
            for row in self.assets.list_latest(source_id, resource_type)
        ]

    def latest_assets(
        self, source_id: str, resource_type: str
    ) -> list[dict]:
        return self.assets.list_latest_with_payload(source_id, resource_type)

    def latest_targets(
        self,
        source_id: str,
        resource_type: str,
        field_paths: dict[str, str],
    ):
        return self.assets.iter_latest_target_values(
            source_id, resource_type, field_paths
        )

    def persist_record(
        self,
        run_id: str,
        record: CollectedRecord,
        key_fields: tuple[str, ...],
        resource_type: str = "records",
        base_id: str = "pncp",
        natureza: str = "estruturada",
        query_params: dict | None = None,
        corpus_version: str = "v1",
        sensitivity: str = "public",
        window_start: str | None = None,
        window_end: str | None = None,
        lease_window_id: str | None = None,
        lease_token: str | None = None,
    ) -> str:
        if (lease_window_id is None) != (lease_token is None):
            raise ValueError(
                "Persistência protegida exige window_id e lease_token"
            )
        key = record.external_id or asset_key(record.payload, key_fields)
        digest = payload_hash(record.payload)
        identity = CanonicalIdentity(base_id, resource_type, key)
        timestamps = extract_source_timestamps(record.payload, resource_type)
        observation_key = build_observation_key(
            identity,
            digest,
            timestamps.updated,
            source_id=record.source_id,
            window_start=window_start,
            window_end=window_end,
        )
        self._begin_immediate()
        try:
            lease_guard = None
            if lease_window_id is not None and lease_token is not None:
                lease_guard = SyncRepository(self.connection)
                lease_guard.require_active_lease(
                    lease_window_id, lease_token, run_id
                )
            current = self.assets.find_current(identity)
            latest_digest = current["payload_hash"] if current else None
            status = detect_change(latest_digest, digest)
            observation_exists = self.assets.contains_observation(
                identity,
                digest,
                timestamps.updated.raw,
                observation_key,
            )
            if status == "UPDATED" and observation_exists:
                status = "UNCHANGED"
            if status != "UNCHANGED":
                self.assets.insert_raw(
                    run_id,
                    record,
                    key,
                    digest,
                    resource_type=resource_type,
                    base_id=base_id,
                    natureza=natureza,
                    query_params=query_params,
                    corpus_version=corpus_version,
                    sensitivity=sensitivity,
                    source_published_at=timestamps.published.raw,
                    source_updated_at=timestamps.updated.raw,
                    identity=identity,
                    previous_version_id=current["id"] if current else None,
                    source_timestamp=timestamps.updated,
                    observation_key=observation_key,
                    window_start=window_start,
                    window_end=window_end,
                    commit=False,
                )
            elif not observation_exists:
                if current is None:
                    raise RuntimeError(
                        "Registro UNCHANGED sem versão RAW correspondente"
                    )
                self.assets.insert_observation(
                    observation_key,
                    current["id"],
                    run_id,
                    record,
                    identity,
                    digest,
                    timestamps.updated,
                    window_start,
                    window_end,
                    commit=False,
                )
            if lease_guard is not None:
                lease_guard.require_active_lease(
                    lease_window_id, lease_token, run_id
                )
            self.connection.commit()
            return status
        except BaseException:
            self.connection.rollback()
            raise

    def finish_run(
        self,
        run_id: str,
        counts: dict[str, int],
        pages_processed: int = 0,
        status: str = "SUCCESS",
        error: str | None = None,
    ) -> None:
        self.runs.finish(run_id, counts, pages_processed, status, error)


def ingest_records(
    repository: RawRepository,
    source_id: str,
    key_fields: tuple[str, ...],
    records: Iterable[CollectedRecord | CollectionCheckpoint],
    mode: str = "update",
    logger: JsonlLogger | None = None,
    resource_type: str = "records",
    base_id: str = "pncp",
    natureza: str = "estruturada",
    query_params: dict | None = None,
    corpus_version: str = "v1",
    sensitivity: str = "public",
    initial_checkpoint: int = 0,
    window_start: str | None = None,
    window_end: str | None = None,
) -> IngestionResult:
    run_id = repository.start_run(
        source_id,
        resource_type,
        window_start=window_start,
        window_end=window_end,
    )
    counts = {
        "records_seen": 0,
        "NEW": 0,
        "UPDATED": 0,
        "UNCHANGED": 0,
        "FAILED": 0,
    }
    logger = logger or JsonlLogger()
    last_page = 0
    processed_pages: set[int] = set()
    try:
        for record in records:
            page = int(record.cursor)
            is_new_page = page not in processed_pages
            processed_pages.add(page)
            if is_new_page:
                logger.event(
                    "pncp_fetch",
                    stage="pncp_fetch",
                    run_id=run_id,
                    resource_type=resource_type,
                    page=page,
                    status="SUCCESS",
                )
            if isinstance(record, CollectionCheckpoint):
                repository.save_progress(
                    source_id, resource_type, run_id, page
                )
                last_page = page
                continue
            status = repository.persist_record(
                run_id,
                record,
                key_fields,
                resource_type,
                base_id,
                natureza,
                (
                    dict(record.query_params)
                    if record.query_params is not None
                    else query_params
                ),
                corpus_version,
                sensitivity,
                window_start,
                window_end,
            )
            counts["records_seen"] += 1
            counts[status] += 1
            logger.event(
                "change_detection",
                stage="change_detection",
                run_id=run_id,
                resource_type=resource_type,
                page=page,
                status=status,
                records_received=counts["records_seen"],
                new=counts["NEW"],
                updated=counts["UPDATED"],
                unchanged=counts["UNCHANGED"],
                failed=counts.get("FAILED", 0),
            )
            if status != "UNCHANGED":
                logger.event(
                    "raw_persistence",
                    stage="raw_persistence",
                    run_id=run_id,
                    resource_type=resource_type,
                    page=page,
                    status=status,
                )
        last_page = max(processed_pages, default=initial_checkpoint)
        repository.finish_run(run_id, counts, len(processed_pages))
        repository.save_checkpoint(
            source_id, resource_type, run_id, last_page
        )
        logger.event(
            "checkpoint_update",
            stage="checkpoint_update",
            run_id=run_id,
            resource_type=resource_type,
            page=last_page,
            status="SUCCESS",
            records_received=counts["records_seen"],
            new=counts["NEW"],
            updated=counts["UPDATED"],
            unchanged=counts["UNCHANGED"],
            failed=counts.get("FAILED", 0),
        )
        return IngestionResult(
            run_id,
            source_id,
            "SUCCEEDED",
            counts["records_seen"],
            counts["NEW"],
            counts["UPDATED"],
            counts["UNCHANGED"],
        )
    except BaseException as error:
        repository.connection.rollback()
        last_page = max(processed_pages, default=initial_checkpoint)
        error_message = str(error) or type(error).__name__
        counts["FAILED"] += 1
        logger.event(
            "ingestion_failure",
            stage="pncp_fetch",
            run_id=run_id,
            resource_type=resource_type,
            page=last_page,
            status="FAILED",
            records_received=counts["records_seen"],
            new=counts["NEW"],
            updated=counts["UPDATED"],
            unchanged=counts["UNCHANGED"],
            failed=counts["FAILED"],
            error=error_message,
        )
        repository.runs.fail_run(
            run_id,
            counts,
            pages_processed=len(processed_pages),
            error=error_message,
        )
        repository.fail_progress(source_id, resource_type, run_id)
        raise


def ingest_window_records(
    repository: RawRepository,
    sync_repository: SyncRepository,
    window_id: str,
    lease_token: str,
    watermark_value: datetime | date | str,
    source_id: str,
    key_fields: tuple[str, ...],
    records: Iterable[CollectedRecord | CollectionCheckpoint],
    *,
    tie_breaker: str = "",
    logger: JsonlLogger | None = None,
    resource_type: str = "records",
    base_id: str = "pncp",
    natureza: str = "estruturada",
    query_params: dict | None = None,
    corpus_version: str = "v1",
    sensitivity: str = "public",
    coverage_window_id: str | None = None,
    coverage_member_key: str | None = None,
) -> IngestionResult:
    """Persiste uma janela v2 sem reinterpretar o checkpoint legado.

    O checkpoint estável de um alvo só é confirmado depois que todos os RAWs
    emitidos antes dele já foram persistidos. O watermark só avança depois do
    `ingestion_run` terminar com sucesso e de todos os alvos estarem completos.
    """

    window = sync_repository.get_window(window_id)
    if window is None:
        raise ValueError("Janela de sincronização não encontrada")
    run_id = repository.start_run(
        source_id,
        resource_type,
        window_start=window["window_start"],
        window_end=window["window_end"],
    )
    counts = {
        "records_seen": 0,
        "NEW": 0,
        "UPDATED": 0,
        "UNCHANGED": 0,
        "FAILED": 0,
    }
    logger = logger or JsonlLogger()
    processed_cursors: set[str] = set()
    run_finished = False
    try:
        sync_repository.attach_run(window_id, lease_token, run_id)
        for event in records:
            cursor = str(event.cursor)
            processed_cursors.add(cursor)
            if isinstance(event, CollectionCheckpoint):
                if event.target_key is None:
                    sync_repository.update_cursor(
                        window_id, cursor, lease_token
                    )
                else:
                    if event.target_fingerprint is None:
                        raise ValueError(
                            "Checkpoint de alvo sem fingerprint estável"
                        )
                    if event.reconciliation_outcome is None:
                        sync_repository.confirm_target(
                            window_id,
                            event.target_key,
                            event.target_fingerprint,
                            lease_token,
                            cursor=cursor,
                        )
                    else:
                        sync_repository.confirm_reconciled_target(
                            window_id,
                            event.target_key,
                            event.target_fingerprint,
                            event.reconciliation_outcome,
                            lease_token,
                            cursor=cursor,
                        )
                continue

            status = repository.persist_record(
                run_id,
                event,
                key_fields,
                resource_type,
                base_id,
                natureza,
                (
                    dict(event.query_params)
                    if event.query_params is not None
                    else query_params
                ),
                corpus_version,
                sensitivity,
                window["window_start"],
                window["window_end"],
                lease_window_id=window_id,
                lease_token=lease_token,
            )
            counts["records_seen"] += 1
            counts[status] += 1
            logger.event(
                "change_detection",
                stage="change_detection",
                run_id=run_id,
                resource_type=resource_type,
                page=cursor,
                status=status,
                records_received=counts["records_seen"],
                new=counts["NEW"],
                updated=counts["UPDATED"],
                unchanged=counts["UNCHANGED"],
                failed=counts["FAILED"],
            )

        repository.finish_run(
            run_id, counts, pages_processed=len(processed_cursors)
        )
        run_finished = True
        sync_repository.complete_window(
            window_id,
            lease_token,
            watermark_value,
            tie_breaker=tie_breaker,
            coverage_window_id=coverage_window_id,
            coverage_member_key=coverage_member_key,
        )
        return IngestionResult(
            run_id,
            source_id,
            "SUCCEEDED",
            counts["records_seen"],
            counts["NEW"],
            counts["UPDATED"],
            counts["UNCHANGED"],
        )
    except BaseException as error:
        repository.connection.rollback()
        error_message = str(error) or type(error).__name__
        if not run_finished:
            counts["FAILED"] += 1
            repository.runs.fail_run(
                run_id,
                counts,
                pages_processed=len(processed_cursors),
                error=error_message,
            )
        try:
            sync_repository.fail_window(
                window_id, lease_token, error_message
            )
        except (LeaseConflictError, LeaseExpiredError):
            # Lease expirado/tomado por outro worker não autoriza esta
            # execução antiga a sobrescrever o estado atual da janela.
            pass
        raise


__all__ = [
    "RawRepository",
    "asset_key",
    "detect_change",
    "ingest_records",
    "ingest_window_records",
    "payload_hash",
]
