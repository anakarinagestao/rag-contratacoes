from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterable, Iterator, Mapping

from app.core.database.repositories import SyncRepository
from app.core.database.connection import database_path
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
    normalized_date,
    stable_target_metadata,
)
from src.ingestion.pca import (
    pca_plan_context,
    validate_complete_pca_plan,
)
from src.schema import CollectionCheckpoint, CollectedRecord
from src.obs import JsonlLogger


ENDPOINT_STRATEGY = "endpoint"
RELATIONSHIP_STRATEGY = "relationship"


@dataclass(frozen=True)
class IncrementalResource:
    name: str
    resource_type: str
    strategy: str
    key_fields: tuple[str, ...]
    natureza: str = "estruturada"
    reconciliation_cadence_days: int | None = None
    reconciliation_horizon_days: int | None = None


RESOURCE_ALIASES = {"ata": "ata_registro_preco"}

INCREMENTAL_RESOURCES = {
    "pca": IncrementalResource(
        "pca", "pca", ENDPOINT_STRATEGY, ("idPcaPncp",), "agregada"
    ),
    "contratacao": IncrementalResource(
        "contratacao",
        "contratacao",
        ENDPOINT_STRATEGY,
        ("numeroControlePNCP",),
    ),
    "contrato": IncrementalResource(
        "contrato",
        "contrato",
        ENDPOINT_STRATEGY,
        ("numeroControlePNCP",),
    ),
    "ata_registro_preco": IncrementalResource(
        "ata_registro_preco",
        "ata_registro_preco",
        ENDPOINT_STRATEGY,
        ("numeroControlePNCPAta",),
        "conversacional",
    ),
    "contratacao_item": IncrementalResource(
        "contratacao_item",
        "contratacao_item",
        RELATIONSHIP_STRATEGY,
        ("numeroItem",),
        reconciliation_cadence_days=1,
        reconciliation_horizon_days=365,
    ),
    "resultado_item": IncrementalResource(
        "resultado_item",
        "resultado_item",
        RELATIONSHIP_STRATEGY,
        ("sequencialResultado",),
        "agregada",
        reconciliation_cadence_days=1,
        reconciliation_horizon_days=365,
    ),
    "empenho": IncrementalResource(
        "empenho",
        "empenho",
        RELATIONSHIP_STRATEGY,
        ("sequencialEmpenho",),
        reconciliation_cadence_days=7,
        reconciliation_horizon_days=730,
    ),
}


class IncrementalConfigurationError(ValueError):
    pass


class OfflineFixtureError(ValueError):
    pass


def resource_definition(resource: str) -> IncrementalResource:
    canonical = RESOURCE_ALIASES.get(resource, resource)
    try:
        return INCREMENTAL_RESOURCES[canonical]
    except KeyError as error:
        supported = ", ".join(sorted((*INCREMENTAL_RESOURCES, "ata")))
        raise IncrementalConfigurationError(
            f"Recurso incremental não suportado: {resource}. "
            f"Opções: {supported}"
        ) from error


@dataclass(frozen=True)
class IncrementalRunConfig:
    resource: str
    start_date: str
    end_date: str
    uf: str
    database_url: str
    fixture_path: Path | None = None
    modality: int | None = None
    job_name: str | None = None
    worker_id: str = "offline-worker"
    resume: bool = False
    reprocess: bool = False
    lease_seconds: float = 300.0
    lease_renewal_seconds: float = 120.0
    reconciliation_cadence_days: int | None = None
    reconciliation_horizon_days: int | None = None
    coverage_name: str | None = None
    coverage_member: str | None = None
    coverage_members: tuple[str, ...] = ()
    log_path: Path | None = None

    def __post_init__(self) -> None:
        start = normalized_date(self.start_date)
        end = normalized_date(self.end_date)
        if end < start:
            raise IncrementalConfigurationError(
                "Intervalo incremental possui data final anterior à inicial"
            )
        normalized_uf = self.uf.strip().upper()
        if len(normalized_uf) != 2 or not normalized_uf.isalpha():
            raise IncrementalConfigurationError("UF deve possuir duas letras")
        object.__setattr__(self, "uf", normalized_uf)
        if self.resume and self.reprocess:
            raise IncrementalConfigurationError(
                "Retomada e reprocessamento são opções mutuamente exclusivas"
            )
        if self.lease_seconds <= 0:
            raise IncrementalConfigurationError(
                "Duração do lease deve ser positiva"
            )
        if not 0 < self.lease_renewal_seconds < self.lease_seconds:
            raise IncrementalConfigurationError(
                "Renovação deve ser positiva e menor que a duração do lease"
            )
        if (
            self.reconciliation_cadence_days is not None
            and self.reconciliation_cadence_days <= 0
        ):
            raise IncrementalConfigurationError(
                "Cadência de reconciliação deve ser positiva"
            )
        if (
            self.reconciliation_horizon_days is not None
            and self.reconciliation_horizon_days < 0
        ):
            raise IncrementalConfigurationError(
                "Horizonte de reconciliação não pode ser negativo"
            )
        coverage_values = (
            self.coverage_name is not None,
            self.coverage_member is not None,
            bool(self.coverage_members),
        )
        if any(coverage_values) and not all(coverage_values):
            raise IncrementalConfigurationError(
                "Cobertura composta requer nome, membro atual e membros esperados"
            )
        if self.coverage_members:
            normalized_members = tuple(
                str(member).strip() for member in self.coverage_members
            )
            if any(not member for member in normalized_members):
                raise IncrementalConfigurationError(
                    "Cobertura composta possui membro vazio"
                )
            if len(set(normalized_members)) != len(normalized_members):
                raise IncrementalConfigurationError(
                    "Cobertura composta possui membros duplicados"
                )
            if str(self.coverage_member).strip() not in normalized_members:
                raise IncrementalConfigurationError(
                    "Membro atual não pertence ao manifesto de cobertura"
                )
            object.__setattr__(
                self, "coverage_members", tuple(sorted(normalized_members))
            )
            object.__setattr__(
                self, "coverage_member", str(self.coverage_member).strip()
            )
            object.__setattr__(
                self, "coverage_name", str(self.coverage_name).strip()
            )

    @property
    def normalized_start(self) -> str:
        return normalized_date(self.start_date)

    @property
    def normalized_end(self) -> str:
        return normalized_date(self.end_date)


@dataclass(frozen=True)
class OfflineFixture:
    strategy: str
    pages: tuple[Mapping[str, Any], ...] = ()
    targets: tuple[Mapping[str, Any], ...] = ()
    discoveries: tuple[Mapping[str, Any], ...] = ()
    watermark_value: str | None = None
    tie_breaker: str = ""
    fail_after_records: int | None = None
    fail_after_checkpoints: int | None = None
    delay_seconds: float = 0.0

    @classmethod
    def load(
        cls, path: str | Path, expected_strategy: str
    ) -> "OfflineFixture":
        fixture_path = Path(path)
        try:
            document = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OfflineFixtureError(
                f"Não foi possível carregar fixture offline: {error}"
            ) from error
        if not isinstance(document, Mapping):
            raise OfflineFixtureError("Fixture deve ser um objeto JSON")
        strategy = str(document.get("strategy") or expected_strategy)
        if strategy != expected_strategy:
            raise OfflineFixtureError(
                f"Fixture usa estratégia {strategy!r}; esperado "
                f"{expected_strategy!r}"
            )
        pages = cls._validated_pages(document.get("pages", []), "pages")
        if "targets" in document and "responses" in document:
            raise OfflineFixtureError(
                "Use targets (legado) ou discoveries/responses, não ambos"
            )
        response_name = "responses" if "responses" in document else "targets"
        targets = cls._validated_targets(
            document.get(response_name, []), response_name
        )
        discoveries_value = document.get("discoveries")
        if discoveries_value is None:
            discoveries = tuple(dict(entry["target"]) for entry in targets)
        else:
            discoveries = cls._validated_discoveries(discoveries_value)
        if strategy == ENDPOINT_STRATEGY and (targets or discoveries):
            raise OfflineFixtureError(
                "Estratégia endpoint deve utilizar pages, não alvos"
            )
        if strategy == RELATIONSHIP_STRATEGY and pages:
            raise OfflineFixtureError(
                "Estratégia relationship deve utilizar targets"
            )
        fail_records = cls._optional_positive_int(
            document.get("fail_after_records"), "fail_after_records"
        )
        fail_checkpoints = cls._optional_positive_int(
            document.get("fail_after_checkpoints"),
            "fail_after_checkpoints",
        )
        try:
            delay = float(document.get("delay_seconds", 0.0))
        except (TypeError, ValueError) as error:
            raise OfflineFixtureError("delay_seconds inválido") from error
        if delay < 0:
            raise OfflineFixtureError("delay_seconds não pode ser negativo")
        fixture = cls(
            strategy=strategy,
            pages=pages,
            targets=tuple(targets),
            discoveries=discoveries,
            watermark_value=(
                str(document["watermark_value"])
                if document.get("watermark_value") not in (None, "")
                else None
            ),
            tie_breaker=str(document.get("tie_breaker") or ""),
            fail_after_records=fail_records,
            fail_after_checkpoints=fail_checkpoints,
            delay_seconds=delay,
        )
        # Percorre toda a estrutura agora, antes de qualquer lease ou escrita.
        for page in fixture.pages:
            fixture._validate_records(page["records"])
        for target in fixture.targets:
            for page in target["pages"]:
                fixture._validate_records(page["records"])
        return fixture

    @classmethod
    def _validated_targets(cls, value, name: str):
        if not isinstance(value, list):
            raise OfflineFixtureError(f"{name} deve ser uma lista")
        targets = []
        seen_target_keys = set()
        for index, entry in enumerate(value, start=1):
            if not isinstance(entry, Mapping):
                raise OfflineFixtureError(f"{name}[{index}] deve ser objeto")
            target = entry.get("target")
            if not isinstance(target, Mapping) or not target:
                raise OfflineFixtureError(
                    f"{name}[{index}].target deve ser um objeto não vazio"
                )
            target_key, _ = stable_target_metadata(target)
            if target_key in seen_target_keys:
                raise OfflineFixtureError(
                    f"Alvo duplicado na fixture: {target_key}"
                )
            seen_target_keys.add(target_key)
            target_pages = cls._validated_pages(
                entry.get("pages", []), f"{name}[{index}].pages"
            )
            record_count = sum(len(page["records"]) for page in target_pages)
            outcome = str(
                entry.get("outcome")
                or ("FOUND" if record_count else "EMPTY")
            ).upper()
            if outcome not in {"FOUND", "EMPTY", "NOT_FOUND"}:
                raise OfflineFixtureError(
                    f"{name}[{index}].outcome inválido: {outcome}"
                )
            if (outcome == "FOUND") != (record_count > 0):
                raise OfflineFixtureError(
                    f"{name}[{index}] possui outcome incompatível com records"
                )
            targets.append(
                {
                    "target": dict(target),
                    "pages": target_pages,
                    "outcome": outcome,
                }
            )
        return tuple(targets)

    @staticmethod
    def _validated_discoveries(value):
        if not isinstance(value, list):
            raise OfflineFixtureError("discoveries deve ser uma lista")
        discoveries = []
        seen = set()
        for index, target in enumerate(value, start=1):
            if not isinstance(target, Mapping) or not target:
                raise OfflineFixtureError(
                    f"discoveries[{index}] deve ser um objeto não vazio"
                )
            key, _ = stable_target_metadata(target)
            if key in seen:
                raise OfflineFixtureError(
                    f"Alvo duplicado em discoveries: {key}"
                )
            seen.add(key)
            discoveries.append(dict(target))
        return tuple(discoveries)

    @staticmethod
    def _optional_positive_int(value, name: str) -> int | None:
        if value in (None, ""):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise OfflineFixtureError(f"{name} inválido") from error
        if parsed <= 0:
            raise OfflineFixtureError(f"{name} deve ser positivo")
        return parsed

    @classmethod
    def _validated_pages(cls, value, name: str):
        if not isinstance(value, list):
            raise OfflineFixtureError(f"{name} deve ser uma lista")
        pages = []
        seen_cursors = set()
        for index, page in enumerate(value, start=1):
            if not isinstance(page, Mapping):
                raise OfflineFixtureError(f"{name}[{index}] deve ser objeto")
            cursor = str(page.get("cursor") or index)
            if cursor in seen_cursors:
                raise OfflineFixtureError(
                    f"Cursor duplicado em {name}: {cursor}"
                )
            seen_cursors.add(cursor)
            records = page.get("records", ())
            if not isinstance(records, list):
                raise OfflineFixtureError(
                    f"{name}[{index}].records deve ser lista"
                )
            pages.append({"cursor": cursor, "records": tuple(records)})
        return tuple(pages)

    @staticmethod
    def _validate_records(records) -> None:
        for index, record in enumerate(records, start=1):
            if not isinstance(record, Mapping):
                raise OfflineFixtureError(
                    f"Registro {index} da fixture deve ser objeto"
                )
            if record.get("external_id") in (None, ""):
                raise OfflineFixtureError(
                    f"Registro {index} sem external_id explícito"
                )
            if not isinstance(record.get("payload"), Mapping):
                raise OfflineFixtureError(
                    f"Registro {index} sem payload JSON válido"
                )

    def target_metadata(self):
        for entry in self.targets:
            key, fingerprint = stable_target_metadata(entry["target"])
            yield key, fingerprint, entry

    def discovery_metadata(self):
        for target in self.discoveries:
            key, fingerprint = stable_target_metadata(target)
            yield key, fingerprint, target

    def validate_resource(self, resource: str) -> None:
        if resource != "pca":
            return
        seen: set[str] = set()
        for page in self.pages:
            for record in page["records"]:
                external_id = str(record["external_id"])
                if record.get("payload_kind") != "complete_pca_plan":
                    raise OfflineFixtureError(
                        "Fixture incremental de PCA deve conter somente "
                        "complete_pca_plan obtido pelo endpoint direto"
                    )
                if external_id in seen:
                    raise OfflineFixtureError(
                        "Fixture incremental de PCA repetiu o mesmo plano; "
                        "fragmentos paginados não podem virar versões RAW"
                    )
                seen.add(external_id)
                try:
                    context = pca_plan_context(external_id)
                    validate_complete_pca_plan(
                        record["payload"],
                        context,
                        str(
                            record.get("source_url")
                            or "offline://fixture/pca"
                        ),
                    )
                except ValueError as error:
                    raise OfflineFixtureError(str(error)) from error

    def events(
        self,
        source_id: str,
        *,
        start_after_cursor: str | None = None,
        pending_target_keys: set[str] | None = None,
    ) -> Iterator[CollectedRecord | CollectionCheckpoint]:
        records_emitted = 0
        checkpoints_emitted = 0
        ordinal = 0

        def maybe_delay():
            if self.delay_seconds:
                time.sleep(self.delay_seconds)

        def emit_record(value, cursor):
            nonlocal ordinal
            ordinal += 1
            maybe_delay()
            return CollectedRecord(
                source_id,
                dict(value["payload"]),
                str(value.get("source_url") or "offline://fixture"),
                str(cursor),
                ordinal,
                external_id=str(value["external_id"]),
                query_params=dict(value.get("query_params") or {}),
            )

        def after_record():
            nonlocal records_emitted
            records_emitted += 1
            if self.fail_after_records == records_emitted:
                raise RuntimeError(
                    "Falha offline simulada após persistência RAW"
                )

        def after_checkpoint():
            nonlocal checkpoints_emitted
            checkpoints_emitted += 1
            if self.fail_after_checkpoints == checkpoints_emitted:
                raise RuntimeError(
                    "Falha offline simulada durante paginação"
                )

        if self.strategy == ENDPOINT_STRATEGY:
            skip = start_after_cursor is not None
            for page in self.pages:
                cursor = page["cursor"]
                if skip:
                    if cursor == start_after_cursor:
                        skip = False
                    continue
                for value in page["records"]:
                    yield emit_record(value, cursor)
                    after_record()
                maybe_delay()
                yield CollectionCheckpoint(cursor)
                after_checkpoint()
            if skip:
                raise RuntimeError(
                    "Cursor salvo não existe mais na fixture; "
                    "retomada recusada para evitar perda de páginas"
                )
            return

        allowed = pending_target_keys
        for key, fingerprint, entry in self.target_metadata():
            if allowed is not None and key not in allowed:
                continue
            for page in entry["pages"]:
                for value in page["records"]:
                    yield emit_record(value, page["cursor"])
                    after_record()
            maybe_delay()
            yield CollectionCheckpoint(
                key,
                key,
                fingerprint,
                reconciliation_outcome=entry["outcome"],
            )
            after_checkpoint()


class LeaseRenewingIterable:
    """Renova no mesmo worker, entre eventos, sem thread auxiliar."""

    def __init__(
        self,
        records: Iterable[CollectedRecord | CollectionCheckpoint],
        sync_repository: SyncRepository,
        window_id: str,
        lease_token: str,
        lease_seconds: float,
        renewal_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.records = records
        self.sync_repository = sync_repository
        self.window_id = window_id
        self.lease_token = lease_token
        self.lease_seconds = lease_seconds
        self.renewal_seconds = renewal_seconds
        self.monotonic = monotonic
        self.utc_now = utc_now
        self.last_renewal = monotonic()
        self.renewals = 0

    def _renew_if_due(self) -> None:
        current = self.monotonic()
        if current - self.last_renewal < self.renewal_seconds:
            return
        self.sync_repository.renew_lease(
            self.window_id,
            self.lease_token,
            lease_seconds=self.lease_seconds,
            now=self.utc_now(),
        )
        self.last_renewal = current
        self.renewals += 1

    def __iter__(self):
        iterator = iter(self.records)
        while True:
            self._renew_if_due()
            try:
                event = next(iterator)
            except StopIteration:
                self._renew_if_due()
                return
            self._renew_if_due()
            yield event


@dataclass(frozen=True)
class IncrementalRunReport:
    job_id: str
    window_id: str
    run_id: str
    resource: str
    strategy: str
    status: str
    records_seen: int
    new: int
    updated: int
    unchanged: int
    lease_renewals: int
    watermark_value: str
    resumed: bool
    reprocessed: bool
    coverage_window_id: str | None = None
    coverage_status: str | None = None


def run_offline_incremental(config: IncrementalRunConfig):
    definition = resource_definition(config.resource)
    if config.fixture_path is None:
        raise IncrementalConfigurationError(
            "Execução offline requer fixture_path"
        )
    fixture = OfflineFixture.load(config.fixture_path, definition.strategy)
    fixture.validate_resource(definition.name)
    source_id = config.job_name or f"pncp_incremental_{definition.name}"
    reconciliation_cadence = (
        config.reconciliation_cadence_days
        if config.reconciliation_cadence_days is not None
        else definition.reconciliation_cadence_days
    )
    reconciliation_horizon = (
        config.reconciliation_horizon_days
        if config.reconciliation_horizon_days is not None
        else definition.reconciliation_horizon_days
    )
    filters = {
        "uf": config.uf.upper(),
        "modality": config.modality,
        "strategy": definition.strategy,
        "offline_contract_version": 1,
        "reconciliation_cadence_days": reconciliation_cadence,
        "reconciliation_horizon_days": reconciliation_horizon,
        "coverage_name": config.coverage_name,
        "coverage_member": config.coverage_member,
    }
    job = SyncJobSpec(
        source=source_id,
        mode=INCREMENTAL_MODE,
        base_id="pncp",
        resource_type=definition.resource_type,
        endpoint=f"offline://{definition.name}/{definition.strategy}",
        uf=config.uf.upper(),
        modality=config.modality,
        filters=filters,
    )
    window = SyncWindowSpec(
        job.job_id, config.normalized_start, config.normalized_end
    )
    repository = RawRepository(config.database_url)
    sync = SyncRepository(repository.connection)
    lease = None
    coverage_window = None
    try:
        try:
            sync.register_job(job)
        except sqlite3.OperationalError as error:
            if "sync_job" not in str(error):
                raise
            raise IncrementalConfigurationError(
                "Banco existente não possui checkpoint v2; aplique as "
                "migrações 001 e 002 somente em uma cópia isolada"
            ) from error
        existing = sync.get_window(window.window_id)
        sync.create_window(window)
        if config.coverage_name is not None:
            try:
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
            except sqlite3.OperationalError as error:
                if "sync_coverage_" not in str(error):
                    raise
                raise IncrementalConfigurationError(
                    "Banco existente não possui manifesto de cobertura; "
                    "aplique a migração 005 somente em uma cópia isolada"
                ) from error
        if existing is not None:
            status = existing["status"]
            attempted = int(existing["attempt_count"]) > 0
            if status == "COMPLETED" and not config.reprocess:
                raise IncrementalConfigurationError(
                    "Janela já concluída; use --reprocess explicitamente"
                )
            if status != "COMPLETED" and attempted and not config.resume:
                raise IncrementalConfigurationError(
                    "Janela incompleta já possui tentativa; use --resume"
                )
            if status == "COMPLETED" and config.resume:
                raise IncrementalConfigurationError(
                    "Janela concluída requer --reprocess, não --resume"
                )
        lease = sync.acquire_window(
            window.window_id,
            config.worker_id,
            lease_seconds=config.lease_seconds,
            allow_completed=config.reprocess,
        )

        pending_target_keys = None
        if definition.strategy == RELATIONSHIP_STRATEGY:
            if reconciliation_cadence is None or reconciliation_horizon is None:
                raise IncrementalConfigurationError(
                    "Recurso dependente sem política de reconciliação"
                )
            pending_target_keys = set()
            try:
                for target_key, fingerprint, target in (
                    fixture.discovery_metadata()
                ):
                    sync.register_reconciliation_target(
                        job.job_id,
                        target_key,
                        dict(target),
                        fingerprint,
                        config.normalized_end,
                        cadence_days=reconciliation_cadence,
                        horizon_days=reconciliation_horizon,
                    )
                responses = {
                    key: (fingerprint, entry)
                    for key, fingerprint, entry in fixture.target_metadata()
                }
                if config.reprocess:
                    selected_keys = set(responses)
                else:
                    selected_keys = {
                        row["target_key"]
                        for row in sync.list_due_reconciliation_targets(
                            job.job_id, config.normalized_end
                        )
                    }
            except sqlite3.OperationalError as error:
                if "sync_reconciliation_target" not in str(error):
                    raise
                raise IncrementalConfigurationError(
                    "Banco existente não possui a fila de reconciliação; "
                    "aplique a migração 004 somente em uma cópia isolada"
                ) from error
            missing_responses = sorted(selected_keys.difference(responses))
            if missing_responses:
                raise OfflineFixtureError(
                    "Fixture não forneceu resposta para alvos de reconciliação "
                    "devidos: " + ", ".join(missing_responses)
                )
            for target_key in sorted(selected_keys):
                fingerprint, _ = responses[target_key]
                queued = sync.get_reconciliation_target(
                    job.job_id, target_key
                )
                if queued is None or queued["target_fingerprint"] != fingerprint:
                    raise OfflineFixtureError(
                        "Resposta de reconciliação diverge do alvo enfileirado: "
                        + target_key
                    )
                row = sync.register_target(
                    window.window_id, target_key, fingerprint
                )
                if config.reprocess or not (
                    row["status"] == "COMPLETED"
                    and row["processed_fingerprint"] == fingerprint
                ):
                    pending_target_keys.add(target_key)

        # O PNCP não oferece snapshot paginado nem garante ordenação
        # estável. Uma retomada baseada no número/cursor da última página
        # poderia ignorar registros movidos para páginas anteriores. Por isso,
        # endpoints sempre repetem a janela completa; a identidade canônica e
        # o hash tornam essa repetição idempotente. progress_cursor permanece
        # apenas como evidência operacional da última página confirmada.
        events = fixture.events(
            source_id,
            start_after_cursor=None,
            pending_target_keys=pending_target_keys,
        )
        renewing = LeaseRenewingIterable(
            events,
            sync,
            window.window_id,
            lease.token,
            config.lease_seconds,
            config.lease_renewal_seconds,
        )
        watermark_value = fixture.watermark_value or (
            f"{config.normalized_end}T23:59:59.999999Z"
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
            tie_breaker=fixture.tie_breaker,
            resource_type=definition.resource_type,
            base_id="pncp",
            natureza=definition.natureza,
            logger=JsonlLogger(
                config.log_path
                or Path(database_path(config.database_url)).with_suffix(
                    ".incremental.jsonl"
                )
            ),
            query_params={
                "offline": True,
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
                    sync.fail_window(
                        window.window_id, lease.token, str(error)
                    )
                except (LeaseConflictError, LeaseExpiredError):
                    pass
        raise
    finally:
        repository.close()


__all__ = [
    "ENDPOINT_STRATEGY",
    "INCREMENTAL_RESOURCES",
    "IncrementalConfigurationError",
    "IncrementalResource",
    "IncrementalRunConfig",
    "IncrementalRunReport",
    "LeaseRenewingIterable",
    "OfflineFixture",
    "OfflineFixtureError",
    "RELATIONSHIP_STRATEGY",
    "resource_definition",
    "run_offline_incremental",
]
