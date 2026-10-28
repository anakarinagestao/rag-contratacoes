from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Callable, Iterable, Mapping, Any

from app.core.database.connection import database_path
from app.core.database.repositories import SyncRepository
from app.core.database.repositories.sync_repository import (
    LeaseConflictError,
    LeaseExpiredError,
)
from src.ingest import RawRepository, ingest_window_records
from src.ingestion.checkpoint import (
    CoveragePlanSpec,
    CoverageWindowSpec,
    INCREMENTAL_MODE,
    SyncJobSpec,
    SyncWindowSpec,
    stable_target_metadata,
)
from src.ingestion.incremental_runner import (
    ENDPOINT_STRATEGY,
    RELATIONSHIP_STRATEGY,
    IncrementalConfigurationError,
    IncrementalRunConfig,
    IncrementalRunReport,
    LeaseRenewingIterable,
    resource_definition,
)
from src.obs import JsonlLogger
from src.schema import CollectionCheckpoint, CollectedRecord


Event = CollectedRecord | CollectionCheckpoint
EventProvider = Callable[[tuple[Mapping[str, Any], ...]], Iterable[Event]]

REQUIRED_INCREMENTAL_TABLES = {
    "raw_observation",
    "sync_job",
    "sync_window",
    "sync_target_progress",
    "sync_watermark",
    "sync_reconciliation_target",
    "sync_coverage_plan",
    "sync_coverage_window",
    "sync_coverage_member",
    "sync_coverage_watermark",
}


@dataclass(frozen=True)
class IncrementalAdapterPlan:
    """Plano injetável; não cria cliente HTTP nem habilita o CLI online."""

    endpoint: str
    filters: Mapping[str, Any]
    event_provider: EventProvider
    discoveries: tuple[Mapping[str, Any], ...] = ()
    watermark_value: str | None = None
    tie_breaker: str = ""

    def __post_init__(self) -> None:
        if not self.endpoint.strip():
            raise IncrementalConfigurationError(
                "Plano incremental requer endpoint estável"
            )
        object.__setattr__(self, "filters", dict(self.filters))
        object.__setattr__(
            self,
            "discoveries",
            tuple(dict(target) for target in self.discoveries),
        )


def _require_temporary_database(database_url: str) -> Path:
    path = Path(database_path(database_url)).resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        path.relative_to(temporary_root)
    except ValueError as error:
        raise IncrementalConfigurationError(
            "Runner de adaptador permanece restrito a SQLite dentro de /tmp"
        ) from error
    return path


def _require_operational_database(
    database_url: str, confirmed_database: Path
) -> Path:
    path = Path(database_path(database_url)).resolve()
    confirmed = confirmed_database.expanduser().resolve()
    if path != confirmed:
        raise IncrementalConfigurationError(
            "Confirmação operacional diverge do banco solicitado"
        )
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        path.relative_to(temporary_root)
    except ValueError:
        pass
    else:
        raise IncrementalConfigurationError(
            "Use o modo piloto para bancos localizados em /tmp"
        )
    if not path.is_file():
        raise IncrementalConfigurationError(
            "Banco operacional confirmado não existe"
        )
    if path.suffix.lower() not in {".db", ".sqlite"}:
        raise IncrementalConfigurationError(
            "Banco operacional deve usar extensão .db ou .sqlite"
        )
    return path


def _require_temporary_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        resolved.relative_to(temporary_root)
    except ValueError as error:
        raise IncrementalConfigurationError(
            "Artefatos do runner de adaptador devem permanecer em /tmp"
        ) from error
    return resolved


def _require_operational_log(path: Path) -> Path:
    if not path.is_absolute():
        raise IncrementalConfigurationError(
            "Log operacional deve usar caminho absoluto"
        )
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() != ".jsonl":
        raise IncrementalConfigurationError(
            "Log operacional deve usar extensão .jsonl"
        )
    return resolved


def _validate_existing_window(
    existing: sqlite3.Row | None, config: IncrementalRunConfig
) -> None:
    if existing is None:
        return
    status = existing["status"]
    attempted = int(existing["attempt_count"]) > 0
    if status == "COMPLETED" and not config.reprocess:
        raise IncrementalConfigurationError(
            "Janela já concluída; use reprocess explicitamente"
        )
    if status != "COMPLETED" and attempted and not config.resume:
        raise IncrementalConfigurationError(
            "Janela incompleta já possui tentativa; use resume"
        )
    if status == "COMPLETED" and config.resume:
        raise IncrementalConfigurationError(
            "Janela concluída requer reprocess, não resume"
        )


def _require_incremental_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = sorted(REQUIRED_INCREMENTAL_TABLES - tables)
    canonical_index = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_raw_asset_canonical_identity'"
    ).fetchone()
    if missing or canonical_index is None:
        details = []
        if canonical_index is None:
            details.append("idx_raw_asset_canonical_identity")
        details.extend(missing)
        raise IncrementalConfigurationError(
            "Banco temporário não recebeu todas as migrações 001-005: "
            + ", ".join(details)
        )

    # A primeira versão experimental da migração 003 declarava
    # UNIQUE(raw_asset_id). Isso confunde a versão imutável com o evento de
    # observação e impede registrar a mesma versão em janelas diferentes.
    # Recusamos esse esquema de forma explícita para evitar uma falha tardia.
    for index in connection.execute("PRAGMA index_list(raw_observation)"):
        if not bool(index[2]):
            continue
        index_name = str(index[1])
        columns = tuple(
            row[2]
            for row in connection.execute(
                "SELECT * FROM pragma_index_info(?)", (index_name,)
            )
        )
        if columns == ("raw_asset_id",):
            raise IncrementalConfigurationError(
                "Banco temporário usa a versão experimental incompatível "
                "da migração 003: raw_observation.raw_asset_id não pode "
                "ser UNIQUE"
            )


def run_adapter_incremental(
    config: IncrementalRunConfig,
    plan: IncrementalAdapterPlan,
    *,
    confirmed_operational_database: Path | None = None,
) -> IncrementalRunReport:
    """Executa adaptadores injetados contra checkpoint v2.

    Por padrão, banco e logs permanecem restritos a ``/tmp``. A escrita fora
    dele exige repetir explicitamente o caminho operacional confirmado.
    """

    if confirmed_operational_database is None:
        sqlite_path = _require_temporary_database(config.database_url)
    else:
        sqlite_path = _require_operational_database(
            config.database_url, confirmed_operational_database
        )
    definition = resource_definition(config.resource)
    if definition.strategy == ENDPOINT_STRATEGY and plan.discoveries:
        raise IncrementalConfigurationError(
            "Recurso por endpoint não aceita descobertas de alvos"
        )
    source_id = config.job_name or f"pncp_incremental_{definition.name}"
    cadence = (
        config.reconciliation_cadence_days
        if config.reconciliation_cadence_days is not None
        else definition.reconciliation_cadence_days
    )
    horizon = (
        config.reconciliation_horizon_days
        if config.reconciliation_horizon_days is not None
        else definition.reconciliation_horizon_days
    )
    job_filters = {
        **dict(plan.filters),
        "strategy": definition.strategy,
        "adapter_contract_version": 1,
        "reconciliation_cadence_days": cadence,
        "reconciliation_horizon_days": horizon,
        "coverage_name": config.coverage_name,
        "coverage_member": config.coverage_member,
    }
    job = SyncJobSpec(
        source=source_id,
        mode=INCREMENTAL_MODE,
        base_id="pncp",
        resource_type=definition.resource_type,
        endpoint=plan.endpoint,
        uf=config.uf.upper(),
        modality=config.modality,
        filters=job_filters,
    )
    window = SyncWindowSpec(
        job.job_id, config.normalized_start, config.normalized_end
    )
    watermark_value = plan.watermark_value or (
        f"{config.normalized_end}T23:59:59.999999Z"
    )
    repository = RawRepository(config.database_url)
    sync = SyncRepository(repository.connection)
    lease = None
    coverage_window = None
    try:
        _require_incremental_schema(repository.connection)
        sync.register_job(job)
        sync.validate_window_candidate(
            window,
            watermark_value,
            tie_breaker=plan.tie_breaker,
        )
        existing = sync.get_window(window.window_id)
        sync.create_window(window)
        _validate_existing_window(existing, config)
        if config.coverage_name is not None:
            coverage_plan = CoveragePlanSpec(
                config.coverage_name,
                "pncp",
                definition.resource_type,
                config.uf.upper(),
                tuple(config.coverage_members),
            )
            sync.register_coverage_plan(coverage_plan)
            coverage_spec = CoverageWindowSpec(
                coverage_plan.plan_id,
                config.normalized_start,
                config.normalized_end,
            )
            coverage_window = sync.create_coverage_window(coverage_spec)
            sync.attach_coverage_member(
                coverage_spec.window_id,
                str(config.coverage_member),
                job.job_id,
                window.window_id,
            )
        lease = sync.acquire_window(
            window.window_id,
            config.worker_id,
            lease_seconds=config.lease_seconds,
            allow_completed=config.reprocess,
        )

        selected_targets: tuple[Mapping[str, Any], ...] = ()
        if definition.strategy == RELATIONSHIP_STRATEGY:
            if cadence is None or horizon is None:
                raise IncrementalConfigurationError(
                    "Recurso dependente sem política de reconciliação"
                )
            for target in plan.discoveries:
                key, fingerprint = stable_target_metadata(target)
                sync.register_reconciliation_target(
                    job.job_id,
                    key,
                    dict(target),
                    fingerprint,
                    config.normalized_end,
                    cadence_days=cadence,
                    horizon_days=horizon,
                )
            rows = (
                sync.list_reconciliation_targets(job.job_id)
                if config.reprocess
                else sync.list_due_reconciliation_targets(
                    job.job_id, config.normalized_end
                )
            )
            targets = []
            for queued in rows:
                target = json.loads(queued["target_json"])
                row = sync.register_target(
                    window.window_id,
                    queued["target_key"],
                    queued["target_fingerprint"],
                )
                if config.reprocess or not (
                    row["status"] == "COMPLETED"
                    and row["processed_fingerprint"]
                    == queued["target_fingerprint"]
                ):
                    targets.append(target)
            selected_targets = tuple(targets)

        raw_events = plan.event_provider(selected_targets)

        def validated_events():
            for event in raw_events:
                if (
                    isinstance(event, CollectedRecord)
                    and event.source_id != source_id
                ):
                    raise IncrementalConfigurationError(
                        "Evento incremental possui source_id diferente do job"
                    )
                yield event

        events = validated_events()
        renewing = LeaseRenewingIterable(
            events,
            sync,
            window.window_id,
            lease.token,
            config.lease_seconds,
            config.lease_renewal_seconds,
        )
        result = ingest_window_records(
            repository,
            sync,
            window.window_id,
            lease.token,
            watermark_value,
            source_id,
            definition.key_fields,
            renewing,
            tie_breaker=plan.tie_breaker,
            resource_type=definition.resource_type,
            base_id="pncp",
            natureza=definition.natureza,
            logger=JsonlLogger(
                (
                    _require_temporary_path(config.log_path)
                    if confirmed_operational_database is None
                    else _require_operational_log(config.log_path)
                )
                if config.log_path is not None
                else sqlite_path.with_suffix(".incremental.jsonl")
            ),
            query_params={
                **dict(plan.filters),
                "transport": "injected",
                "dataInicial": config.normalized_start,
                "dataFinal": config.normalized_end,
                "uf": config.uf.upper(),
                "modalidade": config.modality,
                "strategy": definition.strategy,
            },
            coverage_window_id=(
                coverage_window["id"] if coverage_window is not None else None
            ),
            coverage_member_key=config.coverage_member,
        )
        watermark = sync.get_watermark(job.job_id)
        completed_coverage = (
            sync.get_coverage_window(coverage_window["id"])
            if coverage_window is not None
            else None
        )
        return IncrementalRunReport(
            job_id=job.job_id,
            window_id=window.window_id,
            run_id=result.run_id,
            resource=definition.name,
            strategy=definition.strategy,
            status=result.status,
            records_seen=result.records_seen,
            new=result.new,
            updated=result.updated,
            unchanged=result.unchanged,
            lease_renewals=renewing.renewals,
            watermark_value=watermark["watermark_value"],
            resumed=config.resume,
            reprocessed=config.reprocess,
            coverage_window_id=(
                completed_coverage["id"]
                if completed_coverage is not None
                else None
            ),
            coverage_status=(
                completed_coverage["status"]
                if completed_coverage is not None
                else None
            ),
        )
    except BaseException as error:
        if lease is not None:
            current = sync.get_window(window.window_id)
            if (
                current is not None
                and current["status"] == "RUNNING"
                and current["lease_token"] == lease.token
            ):
                try:
                    sync.fail_window(window.window_id, lease.token, str(error))
                except (LeaseConflictError, LeaseExpiredError):
                    pass
        raise
    finally:
        repository.close()


__all__ = [
    "EventProvider",
    "IncrementalAdapterPlan",
    "REQUIRED_INCREMENTAL_TABLES",
    "run_adapter_incremental",
]
