from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
import tempfile
import time
from typing import Callable, Iterable

from src.ingestion.checkpoint import (
    CoveragePlanSpec,
    CoverageWindowSpec,
    INCREMENTAL_MODE,
    SyncJobSpec,
    SyncWindowSpec,
    normalized_date,
)
from src.ingestion.incremental_runner import (
    ENDPOINT_STRATEGY,
    IncrementalConfigurationError,
    IncrementalRunConfig,
)
from src.ingestion.online_pilot import (
    CONTRATACAO_UPDATE_ENDPOINT,
    run_online_operational,
)


DEFAULT_COVERAGE_NAME = "pncp-contratacao-rj-modalidades-v1"
CONTRACTING_MODALITIES = tuple(range(1, 15))
MINIMUM_SHARD_DELAY_SECONDS = 30


@dataclass(frozen=True)
class CoverageMemberAction:
    modality: int
    member_key: str
    action: str
    reason: str
    job_id: str
    window_id: str


@dataclass(frozen=True)
class ContractingCoveragePlan:
    database: str
    coverage_name: str
    date: str
    coverage_window_id: str
    ready: bool
    members: tuple[CoverageMemberAction, ...]

    @property
    def pending_count(self) -> int:
        return sum(
            member.action in {"RUN", "RESUME", "REPROCESS"}
            for member in self.members
        )


def coverage_members(
    modalities: Iterable[int] = CONTRACTING_MODALITIES,
) -> tuple[str, ...]:
    selected = tuple(sorted(set(int(value) for value in modalities)))
    if not selected or any(
        value not in CONTRACTING_MODALITIES for value in selected
    ):
        raise IncrementalConfigurationError(
            "Cobertura requer modalidades entre 1 e 14"
        )
    return tuple(f"modalidade:{value}" for value in selected)


def contracting_job_spec(
    modality: int,
    coverage_name: str = DEFAULT_COVERAGE_NAME,
) -> SyncJobSpec:
    member = f"modalidade:{modality}"
    return SyncJobSpec(
        source=f"pncp_incremental_contratacao_rj_m{modality:02d}",
        mode=INCREMENTAL_MODE,
        base_id="pncp",
        resource_type="contratacao",
        endpoint=CONTRATACAO_UPDATE_ENDPOINT,
        uf="RJ",
        modality=modality,
        filters={
            "codigoModalidadeContratacao": modality,
            "uf": "RJ",
            "strategy": ENDPOINT_STRATEGY,
            "adapter_contract_version": 1,
            "reconciliation_cadence_days": None,
            "reconciliation_horizon_days": None,
            "coverage_name": coverage_name,
            "coverage_member": member,
        },
    )


def _readonly_connection(database: Path) -> sqlite3.Connection:
    resolved = database.expanduser().resolve()
    if not resolved.is_file():
        raise IncrementalConfigurationError(
            "Banco de cobertura não existe"
        )
    connection = sqlite3.connect(
        f"file:{resolved}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    required = {
        "sync_job",
        "sync_window",
        "sync_coverage_plan",
        "sync_coverage_window",
        "sync_coverage_member",
    }
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required.difference(tables))
    if missing:
        connection.close()
        raise IncrementalConfigurationError(
            "Banco não possui checkpoint v2 completo: " + ", ".join(missing)
        )
    return connection


def plan_contracting_coverage(
    database: Path,
    run_date: str,
    *,
    coverage_name: str = DEFAULT_COVERAGE_NAME,
    modalities: Iterable[int] = CONTRACTING_MODALITIES,
    reprocess_completed: bool = False,
) -> ContractingCoveragePlan:
    day = normalized_date(run_date)
    selected = tuple(sorted(set(int(value) for value in modalities)))
    members = coverage_members(selected)
    coverage_spec = CoveragePlanSpec(
        coverage_name,
        "pncp",
        "contratacao",
        "RJ",
        members,
    )
    coverage_window = CoverageWindowSpec(
        coverage_spec.plan_id, day, day
    )
    connection = _readonly_connection(database)
    try:
        running = connection.execute(
            "SELECT COUNT(*) FROM ingestion_run WHERE status='RUNNING'"
        ).fetchone()[0]
        frozen = connection.execute(
            "SELECT * FROM sync_coverage_plan WHERE name=? AND base_id='pncp' "
            "AND resource_type='contratacao' AND uf='RJ'",
            (coverage_name,),
        ).fetchone()
        if frozen is not None:
            frozen_members = tuple(sorted(json.loads(frozen["members_json"])))
            if frozen["id"] != coverage_spec.plan_id or frozen_members != tuple(
                sorted(members)
            ):
                raise IncrementalConfigurationError(
                    "Manifesto de cobertura já foi congelado com outros membros"
                )

        rows = {
            row["member_key"]: row
            for row in connection.execute(
                "SELECT * FROM sync_coverage_member "
                "WHERE coverage_window_id=?",
                (coverage_window.window_id,),
            )
        }
        if rows and set(rows) != set(members):
            raise IncrementalConfigurationError(
                "Janela de cobertura possui conjunto de membros divergente"
            )

        actions = []
        for modality, member_key in zip(selected, members):
            job = contracting_job_spec(modality, coverage_name)
            window = SyncWindowSpec(job.job_id, day, day)
            member = rows.get(member_key)
            expected_window = connection.execute(
                "SELECT * FROM sync_window WHERE id=?",
                (window.window_id,),
            ).fetchone()
            action = "RUN"
            reason = "membro ainda não iniciado"
            if member is not None and member["status"] == "COMPLETED":
                if (
                    member["job_id"] != job.job_id
                    or member["sync_window_id"] != window.window_id
                ):
                    action = "BLOCK"
                    reason = "membro concluído aponta para outro job ou janela"
                else:
                    action = "REPROCESS" if reprocess_completed else "SKIP"
                    reason = (
                        "membro concluído dentro da sobreposição"
                        if reprocess_completed
                        else "membro já concluído"
                    )
            elif member is not None and member["sync_window_id"] is not None:
                sync_window = connection.execute(
                    "SELECT * FROM sync_window WHERE id=?",
                    (member["sync_window_id"],),
                ).fetchone()
                if (
                    member["job_id"] != job.job_id
                    or member["sync_window_id"] != window.window_id
                    or sync_window is None
                ):
                    action = "BLOCK"
                    reason = "membro pendente possui vínculo inconsistente"
                elif sync_window["status"] == "FAILED":
                    action = "RESUME"
                    reason = "tentativa anterior falhou"
                elif sync_window["status"] == "PENDING":
                    action = "RUN"
                    reason = "janela criada sem tentativa ativa"
                elif sync_window["status"] == "RUNNING":
                    action = "BLOCK"
                    reason = "janela possui execução em andamento"
                else:
                    action = "BLOCK"
                    reason = "janela concluída sem confirmar o membro"
            elif expected_window is not None:
                if expected_window["status"] == "FAILED":
                    action = "RESUME"
                    reason = "janela anterior falhou antes do vínculo"
                elif expected_window["status"] == "RUNNING":
                    action = "BLOCK"
                    reason = "janela não vinculada possui execução ativa"
                elif expected_window["status"] == "COMPLETED":
                    action = "BLOCK"
                    reason = "janela concluída não confirmou a cobertura"
                elif expected_window["attempt_count"]:
                    action = "RESUME"
                    reason = "janela pendente possui tentativa anterior"
                else:
                    reason = "janela criada sem tentativa nem vínculo"
            if running and action in {"RUN", "RESUME", "REPROCESS"}:
                action = "BLOCK"
                reason = "existe ingestion_run RUNNING no banco"
            actions.append(
                CoverageMemberAction(
                    modality,
                    member_key,
                    action,
                    reason,
                    job.job_id,
                    window.window_id,
                )
            )
    finally:
        connection.close()
    result = tuple(actions)
    return ContractingCoveragePlan(
        str(database.expanduser().resolve()),
        coverage_name,
        day,
        coverage_window.window_id,
        not any(item.action == "BLOCK" for item in result),
        result,
    )


def execute_contracting_coverage(
    plan: ContractingCoveragePlan,
    *,
    confirmed_database: Path,
    log_path: Path,
    inter_shard_seconds: int = MINIMUM_SHARD_DELAY_SECONDS,
    runner: Callable = run_online_operational,
    sleeper: Callable[[float], None] = time.sleep,
):
    database = Path(plan.database).resolve()
    if confirmed_database.expanduser().resolve() != database:
        raise IncrementalConfigurationError(
            "Confirmação operacional diverge do banco planejado"
        )
    if database.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise IncrementalConfigurationError(
            "Cobertura operacional não deve usar banco em /tmp"
        )
    if not log_path.is_absolute() or log_path.suffix.lower() != ".jsonl":
        raise IncrementalConfigurationError(
            "Log da cobertura deve ser absoluto e terminar em .jsonl"
        )
    if inter_shard_seconds < MINIMUM_SHARD_DELAY_SECONDS:
        raise IncrementalConfigurationError(
            "Intervalo entre modalidades deve ser de pelo menos 30 segundos"
        )
    if not plan.ready:
        raise IncrementalConfigurationError(
            "Plano possui membros bloqueados e não pode ser executado"
        )

    selected = tuple(item for item in plan.members if item.action != "SKIP")
    if any(
        item.action not in {"RUN", "RESUME", "REPROCESS"}
        for item in selected
    ):
        raise IncrementalConfigurationError(
            "Plano possui ação operacional desconhecida"
        )
    all_members = coverage_members(
        item.modality for item in plan.members
    )
    reports = []
    for index, item in enumerate(selected):
        if index:
            sleeper(float(inter_shard_seconds))
        config = IncrementalRunConfig(
            resource="contratacao",
            start_date=plan.date,
            end_date=plan.date,
            uf="RJ",
            modality=item.modality,
            database_url=f"sqlite:///{database}",
            job_name=f"pncp_incremental_contratacao_rj_m{item.modality:02d}",
            worker_id=f"coverage-operational-m{item.modality:02d}",
            resume=item.action == "RESUME",
            reprocess=item.action == "REPROCESS",
            coverage_name=plan.coverage_name,
            coverage_member=item.member_key,
            coverage_members=all_members,
            log_path=log_path,
        )
        reports.append(runner(config, database))
    return tuple(reports)


__all__ = [
    "CONTRACTING_MODALITIES",
    "DEFAULT_COVERAGE_NAME",
    "MINIMUM_SHARD_DELAY_SECONDS",
    "ContractingCoveragePlan",
    "CoverageMemberAction",
    "contracting_job_spec",
    "coverage_members",
    "execute_contracting_coverage",
    "plan_contracting_coverage",
]
