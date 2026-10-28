from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
import uuid

from src.ingestion.checkpoint import (
    CoveragePlanSpec,
    CoverageWindowSpec,
    SyncJobSpec,
    SyncWindowSpec,
    normalized_date,
    normalized_watermark,
)
from src.obs import utc_now


class LeaseConflictError(RuntimeError):
    pass


class LeaseExpiredError(RuntimeError):
    pass


class WindowCompletedError(RuntimeError):
    pass


class WindowContinuityError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowLease:
    window_id: str
    owner: str
    token: str
    expires_at: str


def _utc_datetime(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime | str | None = None) -> str:
    return _utc_datetime(value).isoformat(timespec="microseconds")


class SyncRepository:
    """Estado v2 de sincronização, independente do checkpoint legado."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    @contextmanager
    def _immediate(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def register_job(self, spec: SyncJobSpec):
        now = utc_now()
        self.connection.execute(
            "INSERT OR IGNORE INTO sync_job "
            "(id, source, mode, base_id, resource_type, endpoint, uf, modality, "
            "config_hash, config_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec.job_id,
                spec.source,
                spec.mode,
                spec.base_id,
                spec.resource_type,
                spec.endpoint,
                spec.uf,
                spec.modality,
                spec.configuration_hash,
                spec.configuration_json,
                now,
            ),
        )
        self.connection.commit()
        row = self.get_job(spec.job_id)
        if row is None or row["config_json"] != spec.configuration_json:
            raise ValueError("Identidade do job conflita com outra configuração")
        return row

    def get_job(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM sync_job WHERE id = ?", (job_id,)
        ).fetchone()

    def register_coverage_plan(self, spec: CoveragePlanSpec):
        now = utc_now()
        uf = str(spec.uf or "")
        members_json = json.dumps(
            sorted(spec.members),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO sync_coverage_plan "
            "(id, name, base_id, resource_type, uf, members_json, config_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec.plan_id,
                spec.name,
                spec.base_id,
                spec.resource_type,
                uf,
                members_json,
                spec.configuration_json,
                now,
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM sync_coverage_plan WHERE name=? AND base_id=? "
            "AND resource_type=? AND uf=?",
            (spec.name, spec.base_id, spec.resource_type, uf),
        ).fetchone()
        if row is None or row["config_json"] != spec.configuration_json:
            raise ValueError(
                "Manifesto de cobertura já foi congelado com outros membros"
            )
        return row

    def create_coverage_window(self, spec: CoverageWindowSpec):
        plan = self.connection.execute(
            "SELECT * FROM sync_coverage_plan WHERE id=?", (spec.plan_id,)
        ).fetchone()
        if plan is None:
            raise ValueError("Plano de cobertura não registrado")
        members = json.loads(plan["members_json"])
        now = utc_now()
        with self._immediate():
            self.connection.execute(
                "INSERT OR IGNORE INTO sync_coverage_window "
                "(id, plan_id, window_start, window_end, status, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)",
                (spec.window_id, spec.plan_id, spec.start, spec.end, now, now),
            )
            for member in members:
                self.connection.execute(
                    "INSERT OR IGNORE INTO sync_coverage_member "
                    "(coverage_window_id, member_key, status, updated_at) "
                    "VALUES (?, ?, 'PENDING', ?)",
                    (spec.window_id, member, now),
                )
        return self.get_coverage_window(spec.window_id)

    def get_coverage_window(self, coverage_window_id: str):
        return self.connection.execute(
            "SELECT * FROM sync_coverage_window WHERE id=?",
            (coverage_window_id,),
        ).fetchone()

    def get_coverage_watermark(self, plan_id: str):
        return self.connection.execute(
            "SELECT * FROM sync_coverage_watermark WHERE plan_id=?",
            (plan_id,),
        ).fetchone()

    def attach_coverage_member(
        self,
        coverage_window_id: str,
        member_key: str,
        job_id: str,
        sync_window_id: str,
    ):
        job = self.get_job(job_id)
        sync_window = self.get_window(sync_window_id)
        if job is None or sync_window is None:
            raise ValueError("Membro de cobertura requer job e janela válidos")
        if sync_window["job_id"] != job_id:
            raise ValueError("Janela do membro não pertence ao job informado")
        member = self.connection.execute(
            "SELECT * FROM sync_coverage_member WHERE coverage_window_id=? "
            "AND member_key=?",
            (coverage_window_id, member_key),
        ).fetchone()
        if member is None:
            raise ValueError("Membro não pertence ao manifesto de cobertura")
        if (
            member["job_id"] not in (None, job_id)
            or member["sync_window_id"] not in (None, sync_window_id)
        ):
            raise ValueError("Membro de cobertura já está ligado a outro shard")
        self.connection.execute(
            "UPDATE sync_coverage_member SET job_id=?, sync_window_id=?, "
            "updated_at=? WHERE coverage_window_id=? AND member_key=?",
            (job_id, sync_window_id, utc_now(), coverage_window_id, member_key),
        )
        self.connection.commit()
        return self.connection.execute(
            "SELECT * FROM sync_coverage_member WHERE coverage_window_id=? "
            "AND member_key=?",
            (coverage_window_id, member_key),
        ).fetchone()

    def create_window(self, spec: SyncWindowSpec):
        if self.get_job(spec.job_id) is None:
            raise ValueError("Job não registrado para a janela")
        now = utc_now()
        self.connection.execute(
            "INSERT OR IGNORE INTO sync_window "
            "(id, job_id, window_start, window_end, status, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)",
            (
                spec.window_id,
                spec.job_id,
                spec.start,
                spec.end,
                now,
                now,
            ),
        )
        self.connection.commit()
        return self.get_window(spec.window_id)

    def get_window(self, window_id: str):
        return self.connection.execute(
            "SELECT * FROM sync_window WHERE id = ?", (window_id,)
        ).fetchone()

    def acquire_window(
        self,
        window_id: str,
        owner: str,
        lease_seconds: float = 300,
        run_id: str | None = None,
        now: datetime | str | None = None,
        allow_completed: bool = False,
    ) -> WindowLease:
        if not owner.strip():
            raise ValueError("Lease requer proprietário")
        if lease_seconds <= 0:
            raise ValueError("Lease deve possuir duração positiva")
        current_time = _utc_datetime(now)
        expires_at = current_time + timedelta(seconds=lease_seconds)
        token = str(uuid.uuid4())
        with self._immediate():
            row = self.get_window(window_id)
            if row is None:
                raise ValueError("Janela não encontrada")
            if row["status"] == "COMPLETED" and not allow_completed:
                raise WindowCompletedError("Janela já concluída")
            if row["status"] == "RUNNING" and row["lease_expires_at"]:
                previous_expiry = _utc_datetime(row["lease_expires_at"])
                if previous_expiry > current_time:
                    raise LeaseConflictError(
                        f"Janela pertence ao worker {row['lease_owner']}"
                    )
            self.connection.execute(
                "UPDATE sync_window SET status='RUNNING', active_run_id=?, "
                "lease_owner=?, lease_token=?, lease_expires_at=?, "
                "attempt_count=attempt_count+1, error_message=NULL, "
                "completed_at=NULL, updated_at=? WHERE id=?",
                (
                    run_id,
                    owner,
                    token,
                    expires_at.isoformat(timespec="microseconds"),
                    current_time.isoformat(timespec="microseconds"),
                    window_id,
                ),
            )
        return WindowLease(
            window_id,
            owner,
            token,
            expires_at.isoformat(timespec="microseconds"),
        )

    def attach_run(
        self,
        window_id: str,
        lease_token: str,
        run_id: str,
        now: datetime | str | None = None,
    ) -> None:
        current_time = _utc_datetime(now)
        with self._immediate():
            self._require_lease(window_id, lease_token, current_time)
            run = self.connection.execute(
                "SELECT status FROM ingestion_run WHERE id=?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "RUNNING":
                raise ValueError("A janela só aceita uma execução RUNNING")
            self.connection.execute(
                "UPDATE sync_window SET active_run_id=?, updated_at=? WHERE id=?",
                (run_id, current_time.isoformat(timespec="microseconds"), window_id),
            )

    def renew_lease(
        self,
        window_id: str,
        lease_token: str,
        lease_seconds: float = 300,
        now: datetime | str | None = None,
    ) -> WindowLease:
        current_time = _utc_datetime(now)
        expires_at = current_time + timedelta(seconds=lease_seconds)
        with self._immediate():
            row = self._require_lease(window_id, lease_token, current_time)
            self.connection.execute(
                "UPDATE sync_window SET lease_expires_at=?, updated_at=? "
                "WHERE id=?",
                (
                    expires_at.isoformat(timespec="microseconds"),
                    current_time.isoformat(timespec="microseconds"),
                    window_id,
                ),
            )
        return WindowLease(
            window_id,
            row["lease_owner"],
            lease_token,
            expires_at.isoformat(timespec="microseconds"),
        )

    def register_target(
        self, window_id: str, target_key: str, fingerprint: str
    ):
        if not target_key or not fingerprint:
            raise ValueError("Alvo requer identidade e fingerprint")
        window = self.get_window(window_id)
        if window is None:
            raise ValueError("Janela não encontrada")
        if window["status"] == "COMPLETED":
            raise WindowCompletedError(
                "Alvos de uma janela concluída são imutáveis"
            )
        now = utc_now()
        self.connection.execute(
            "INSERT INTO sync_target_progress "
            "(window_id, target_key, target_fingerprint, status, created_at, "
            "updated_at) VALUES (?, ?, ?, 'PENDING', ?, ?) "
            "ON CONFLICT(window_id, target_key) DO UPDATE SET "
            "target_fingerprint=excluded.target_fingerprint, "
            "status=CASE WHEN sync_target_progress.target_fingerprint <> "
            "excluded.target_fingerprint THEN 'PENDING' "
            "ELSE sync_target_progress.status END, "
            "active_run_id=CASE WHEN sync_target_progress.target_fingerprint <> "
            "excluded.target_fingerprint THEN NULL "
            "ELSE sync_target_progress.active_run_id END, "
            "error_message=CASE WHEN sync_target_progress.target_fingerprint <> "
            "excluded.target_fingerprint THEN NULL "
            "ELSE sync_target_progress.error_message END, "
            "completed_at=CASE WHEN sync_target_progress.target_fingerprint <> "
            "excluded.target_fingerprint THEN NULL "
            "ELSE sync_target_progress.completed_at END, updated_at=excluded.updated_at",
            (window_id, target_key, fingerprint, now, now),
        )
        self.connection.commit()
        return self.get_target(window_id, target_key)

    def register_reconciliation_target(
        self,
        job_id: str,
        target_key: str,
        target: dict,
        fingerprint: str,
        discovered_on: date | datetime | str,
        *,
        cadence_days: int,
        horizon_days: int,
    ):
        """Inclui um alvo na fila durável sem reabrir janelas antigas."""

        if self.get_job(job_id) is None:
            raise ValueError("Job não registrado para reconciliação")
        if not target_key or not fingerprint:
            raise ValueError("Alvo de reconciliação requer identidade")
        if cadence_days <= 0:
            raise ValueError("Cadência de reconciliação deve ser positiva")
        if horizon_days < 0:
            raise ValueError("Horizonte de reconciliação não pode ser negativo")
        discovered = date.fromisoformat(normalized_date(discovered_on))
        eligible_until = discovered + timedelta(days=horizon_days)
        target_json = json.dumps(
            target, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        now = utc_now()
        self.connection.execute(
            "INSERT INTO sync_reconciliation_target "
            "(job_id, target_key, target_json, target_fingerprint, "
            "cadence_days, eligible_until, next_due_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id, target_key) DO UPDATE SET "
            "target_json=excluded.target_json, "
            "target_fingerprint=excluded.target_fingerprint, "
            "cadence_days=excluded.cadence_days, "
            "eligible_until=CASE WHEN excluded.eligible_until > "
            "sync_reconciliation_target.eligible_until "
            "THEN excluded.eligible_until "
            "ELSE sync_reconciliation_target.eligible_until END, "
            "next_due_at=CASE WHEN sync_reconciliation_target.target_fingerprint "
            "<> excluded.target_fingerprint THEN excluded.next_due_at "
            "ELSE sync_reconciliation_target.next_due_at END, "
            "updated_at=excluded.updated_at",
            (
                job_id,
                target_key,
                target_json,
                fingerprint,
                cadence_days,
                eligible_until.isoformat(),
                discovered.isoformat(),
                now,
                now,
            ),
        )
        self.connection.commit()
        return self.get_reconciliation_target(job_id, target_key)

    def get_reconciliation_target(self, job_id: str, target_key: str):
        return self.connection.execute(
            "SELECT * FROM sync_reconciliation_target "
            "WHERE job_id=? AND target_key=?",
            (job_id, target_key),
        ).fetchone()

    def list_due_reconciliation_targets(
        self, job_id: str, as_of: date | datetime | str
    ):
        due_date = normalized_date(as_of)
        return self.connection.execute(
            "SELECT * FROM sync_reconciliation_target WHERE job_id=? "
            "AND next_due_at <= ? AND eligible_until >= ? "
            "ORDER BY next_due_at, target_key",
            (job_id, due_date, due_date),
        ).fetchall()

    def list_reconciliation_targets(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM sync_reconciliation_target WHERE job_id=? "
            "ORDER BY target_key",
            (job_id,),
        ).fetchall()

    def get_target(self, window_id: str, target_key: str):
        return self.connection.execute(
            "SELECT * FROM sync_target_progress "
            "WHERE window_id=? AND target_key=?",
            (window_id, target_key),
        ).fetchone()

    def claim_target(
        self,
        window_id: str,
        target_key: str,
        lease_token: str,
        now: datetime | str | None = None,
    ) -> bool:
        current_time = _utc_datetime(now)
        with self._immediate():
            window = self._require_lease(window_id, lease_token, current_time)
            self._require_active_run(window)
            target = self.get_target(window_id, target_key)
            if target is None:
                raise ValueError("Alvo não registrado")
            if (
                target["status"] == "COMPLETED"
                and target["processed_fingerprint"]
                == target["target_fingerprint"]
            ):
                return False
            self.connection.execute(
                "UPDATE sync_target_progress SET status='RUNNING', "
                "active_run_id=?, error_message=NULL, updated_at=? "
                "WHERE window_id=? AND target_key=?",
                (
                    window["active_run_id"],
                    current_time.isoformat(timespec="microseconds"),
                    window_id,
                    target_key,
                ),
            )
        return True

    def complete_target(
        self,
        window_id: str,
        target_key: str,
        lease_token: str,
        now: datetime | str | None = None,
    ) -> None:
        current_time = _utc_datetime(now)
        with self._immediate():
            window = self._require_lease(
                window_id, lease_token, current_time
            )
            self._require_active_run(window)
            target = self.get_target(window_id, target_key)
            if target is None or target["status"] != "RUNNING":
                raise ValueError("Somente um alvo RUNNING pode ser concluído")
            self._mark_target_completed(
                window_id,
                target_key,
                target["target_fingerprint"],
                current_time,
                window["active_run_id"],
            )

    def confirm_target(
        self,
        window_id: str,
        target_key: str,
        fingerprint: str,
        lease_token: str,
        cursor: str | None = None,
        now: datetime | str | None = None,
    ) -> None:
        """Confirma o alvo somente após seus RAWs terem sido persistidos."""

        current_time = _utc_datetime(now)
        with self._immediate():
            window = self._require_lease(
                window_id, lease_token, current_time
            )
            self._require_active_run(window)
            self._confirm_target_locked(
                window,
                target_key,
                fingerprint,
                current_time,
            )
            if cursor is not None:
                self.connection.execute(
                    "UPDATE sync_window SET progress_cursor=?, updated_at=? "
                    "WHERE id=?",
                    (
                        str(cursor),
                        current_time.isoformat(timespec="microseconds"),
                        window_id,
                    ),
                )

    def confirm_reconciled_target(
        self,
        window_id: str,
        target_key: str,
        fingerprint: str,
        outcome: str,
        lease_token: str,
        cursor: str | None = None,
        now: datetime | str | None = None,
    ) -> None:
        """Confirma progresso e reagendamento na mesma transação."""

        normalized_outcome = str(outcome).upper()
        if normalized_outcome not in {"FOUND", "EMPTY", "NOT_FOUND"}:
            raise ValueError("Resultado de reconciliação inválido")
        current_time = _utc_datetime(now)
        with self._immediate():
            window = self._require_lease(
                window_id, lease_token, current_time
            )
            self._require_active_run(window)
            queued = self.get_reconciliation_target(
                window["job_id"], target_key
            )
            if queued is None:
                raise ValueError("Alvo não existe na fila de reconciliação")
            if queued["target_fingerprint"] != fingerprint:
                raise ValueError(
                    "Fingerprint da fila mudou durante o processamento"
                )
            self._confirm_target_locked(
                window,
                target_key,
                fingerprint,
                current_time,
            )
            checked_on = date.fromisoformat(window["window_end"])
            previous_check = queued["last_checked_at"]
            if previous_check is None or checked_on >= date.fromisoformat(
                previous_check
            ):
                next_due = checked_on + timedelta(
                    days=int(queued["cadence_days"])
                )
                empty_count = (
                    0
                    if normalized_outcome == "FOUND"
                    else int(queued["consecutive_empty"]) + 1
                )
                self.connection.execute(
                    "UPDATE sync_reconciliation_target SET "
                    "last_outcome=?, last_checked_at=?, "
                    "last_found_at=CASE WHEN ?='FOUND' THEN ? "
                    "ELSE last_found_at END, consecutive_empty=?, "
                    "next_due_at=?, last_window_id=?, updated_at=? "
                    "WHERE job_id=? AND target_key=?",
                    (
                        normalized_outcome,
                        checked_on.isoformat(),
                        normalized_outcome,
                        checked_on.isoformat(),
                        empty_count,
                        next_due.isoformat(),
                        window_id,
                        current_time.isoformat(timespec="microseconds"),
                        window["job_id"],
                        target_key,
                    ),
                )
            if cursor is not None:
                self.connection.execute(
                    "UPDATE sync_window SET progress_cursor=?, updated_at=? "
                    "WHERE id=?",
                    (
                        str(cursor),
                        current_time.isoformat(timespec="microseconds"),
                        window_id,
                    ),
                )

    def update_cursor(
        self,
        window_id: str,
        cursor: str,
        lease_token: str,
        now: datetime | str | None = None,
    ) -> None:
        current_time = _utc_datetime(now)
        with self._immediate():
            window = self._require_lease(
                window_id, lease_token, current_time
            )
            self._require_active_run(window)
            self.connection.execute(
                "UPDATE sync_window SET progress_cursor=?, updated_at=? "
                "WHERE id=?",
                (
                    str(cursor),
                    current_time.isoformat(timespec="microseconds"),
                    window_id,
                ),
            )

    def list_pending_targets(self, window_id: str):
        return self.connection.execute(
            "SELECT * FROM sync_target_progress WHERE window_id=? AND "
            "(status <> 'COMPLETED' OR processed_fingerprint IS NULL OR "
            "processed_fingerprint <> target_fingerprint) ORDER BY target_key",
            (window_id,),
        ).fetchall()

    def fail_window(
        self,
        window_id: str,
        lease_token: str,
        error: str,
        now: datetime | str | None = None,
    ) -> None:
        current_time = _utc_datetime(now)
        with self._immediate():
            self._require_lease(window_id, lease_token, current_time)
            now_text = current_time.isoformat(timespec="microseconds")
            self.connection.execute(
                "UPDATE sync_target_progress SET status='FAILED', "
                "error_message=?, updated_at=? "
                "WHERE window_id=? AND status='RUNNING'",
                (error, now_text, window_id),
            )
            self.connection.execute(
                "UPDATE sync_window SET status='FAILED', error_message=?, "
                "lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, "
                "updated_at=? WHERE id=?",
                (error, now_text, window_id),
            )

    def complete_window(
        self,
        window_id: str,
        lease_token: str,
        watermark_value: datetime | date | str,
        tie_breaker: str = "",
        coverage_window_id: str | None = None,
        coverage_member_key: str | None = None,
        now: datetime | str | None = None,
    ) -> None:
        current_time = _utc_datetime(now)
        watermark_text = normalized_watermark(watermark_value)
        with self._immediate():
            window = self._require_lease(
                window_id, lease_token, current_time
            )
            run_id = window["active_run_id"]
            run = self.connection.execute(
                "SELECT status FROM ingestion_run WHERE id=?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "SUCCESS":
                raise ValueError(
                    "Watermark exige execução vinculada e concluída com SUCCESS"
                )
            incomplete = self.connection.execute(
                "SELECT COUNT(*) FROM sync_target_progress WHERE window_id=? "
                "AND (status <> 'COMPLETED' OR processed_fingerprint IS NULL "
                "OR processed_fingerprint <> target_fingerprint)",
                (window_id,),
            ).fetchone()[0]
            if incomplete:
                raise ValueError("Janela possui alvos incompletos")
            current = self.connection.execute(
                "SELECT * FROM sync_watermark WHERE job_id=?",
                (window["job_id"],),
            ).fetchone()
            candidate = (watermark_text, str(tie_breaker))
            existing = (
                (current["watermark_value"], current["tie_breaker"])
                if current is not None
                else None
            )
            self._require_watermark_matches_window(window, watermark_text)
            if existing is None or candidate > existing:
                self._require_contiguous_coverage(window, current)
            now_text = current_time.isoformat(timespec="microseconds")
            self.connection.execute(
                "UPDATE sync_window SET status='COMPLETED', "
                "lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, "
                "error_message=NULL, completed_at=?, updated_at=? WHERE id=?",
                (now_text, now_text, window_id),
            )
            if existing is None or candidate > existing:
                self.connection.execute(
                    "INSERT INTO sync_watermark "
                    "(job_id, watermark_value, tie_breaker, last_window_id, "
                    "confirmed_run_id, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(job_id) DO UPDATE SET "
                    "watermark_value=excluded.watermark_value, "
                    "tie_breaker=excluded.tie_breaker, "
                    "last_window_id=excluded.last_window_id, "
                    "confirmed_run_id=excluded.confirmed_run_id, "
                    "updated_at=excluded.updated_at",
                    (
                        window["job_id"],
                        watermark_text,
                        str(tie_breaker),
                        window_id,
                        run_id,
                        now_text,
                    ),
                )
            if (coverage_window_id is None) != (coverage_member_key is None):
                raise ValueError(
                    "Cobertura composta requer window_id e member_key"
                )
            if coverage_window_id is not None and coverage_member_key is not None:
                self._confirm_coverage_member_locked(
                    window,
                    coverage_window_id,
                    coverage_member_key,
                    watermark_text,
                    now_text,
                )

    def validate_window_candidate(
        self,
        spec: SyncWindowSpec,
        watermark_value: datetime | date | str,
        tie_breaker: str = "",
    ) -> str:
        """Rejeita lacunas antes de iniciar trabalho ou acessar transporte.

        A mesma regra continua sendo verificada por ``complete_window`` dentro
        da transação final. Esta validação antecipada evita chamadas HTTP e
        execuções RAW que jamais poderiam confirmar o watermark.
        """

        if self.get_job(spec.job_id) is None:
            raise ValueError("Job não registrado para validação da janela")
        watermark_text = normalized_watermark(watermark_value)
        window = {
            "id": spec.window_id,
            "job_id": spec.job_id,
            "window_start": spec.start,
            "window_end": spec.end,
        }
        current = self.get_watermark(spec.job_id)
        candidate = (watermark_text, str(tie_breaker))
        existing = (
            (current["watermark_value"], current["tie_breaker"])
            if current is not None
            else None
        )
        self._require_watermark_matches_window(window, watermark_text)
        if existing is None or candidate > existing:
            self._require_contiguous_coverage(window, current)
        return watermark_text

    def _confirm_coverage_member_locked(
        self,
        sync_window,
        coverage_window_id: str,
        member_key: str,
        watermark_text: str,
        now_text: str,
    ) -> None:
        coverage = self.get_coverage_window(coverage_window_id)
        member = self.connection.execute(
            "SELECT * FROM sync_coverage_member WHERE coverage_window_id=? "
            "AND member_key=?",
            (coverage_window_id, member_key),
        ).fetchone()
        if coverage is None or member is None:
            raise ValueError("Membro de cobertura não registrado")
        if (
            member["job_id"] != sync_window["job_id"]
            or member["sync_window_id"] != sync_window["id"]
        ):
            raise ValueError("Membro de cobertura não pertence a esta janela")
        if (
            coverage["window_start"] != sync_window["window_start"]
            or coverage["window_end"] != sync_window["window_end"]
        ):
            raise ValueError("Intervalo do shard diverge da cobertura composta")
        self.connection.execute(
            "UPDATE sync_coverage_member SET status='COMPLETED', "
            "completed_at=?, updated_at=? WHERE coverage_window_id=? "
            "AND member_key=?",
            (now_text, now_text, coverage_window_id, member_key),
        )
        pending = self.connection.execute(
            "SELECT COUNT(*) FROM sync_coverage_member "
            "WHERE coverage_window_id=? AND status<>'COMPLETED'",
            (coverage_window_id,),
        ).fetchone()[0]
        if pending:
            return
        current = self.get_coverage_watermark(coverage["plan_id"])
        watermark_date = datetime.fromisoformat(watermark_text).date().isoformat()
        if watermark_date != coverage["window_end"]:
            raise WindowContinuityError(
                "Watermark composto diverge do fim da janela de cobertura"
            )
        if current is not None:
            covered_date = datetime.fromisoformat(
                current["watermark_value"]
            ).date()
            if date.fromisoformat(coverage["window_start"]) > covered_date + timedelta(days=1):
                raise WindowContinuityError(
                    "Cobertura composta deixa lacuna após o watermark"
                )
        blocker = self.connection.execute(
            "SELECT id FROM sync_coverage_window WHERE plan_id=? AND id<>? "
            "AND status<>'COMPLETED' AND (window_start < ? OR "
            "(window_start=? AND window_end < ?)) ORDER BY window_start LIMIT 1",
            (
                coverage["plan_id"],
                coverage_window_id,
                coverage["window_start"],
                coverage["window_start"],
                coverage["window_end"],
            ),
        ).fetchone()
        if blocker is not None:
            raise WindowContinuityError(
                "Janela composta anterior ainda não foi concluída"
            )
        self.connection.execute(
            "UPDATE sync_coverage_window SET status='COMPLETED', "
            "completed_at=?, updated_at=? WHERE id=?",
            (now_text, now_text, coverage_window_id),
        )
        if current is None or watermark_text > current["watermark_value"]:
            self.connection.execute(
                "INSERT INTO sync_coverage_watermark "
                "(plan_id, watermark_value, last_window_id, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(plan_id) DO UPDATE SET "
                "watermark_value=excluded.watermark_value, "
                "last_window_id=excluded.last_window_id, "
                "updated_at=excluded.updated_at",
                (
                    coverage["plan_id"],
                    watermark_text,
                    coverage_window_id,
                    now_text,
                ),
            )

    @staticmethod
    def _require_watermark_matches_window(window, watermark_text: str) -> None:
        watermark_date = (
            datetime.fromisoformat(watermark_text).date().isoformat()
        )
        if watermark_date != window["window_end"]:
            raise WindowContinuityError(
                "Watermark deve pertencer à data final da janela: "
                f"esperado={window['window_end']}, recebido={watermark_date}"
            )

    def _require_contiguous_coverage(self, window, current) -> None:
        covered_date = None
        if current is not None:
            covered_date = datetime.fromisoformat(
                current["watermark_value"]
            ).date()
            next_uncovered = covered_date + timedelta(days=1)
            window_start = date.fromisoformat(window["window_start"])
            if window_start > next_uncovered:
                raise WindowContinuityError(
                    "Janela deixa lacuna após o watermark confirmado: "
                    f"watermark={covered_date.isoformat()}, "
                    f"window_start={window['window_start']}"
                )

        parameters: list[str] = [
            window["job_id"],
            window["id"],
            window["window_start"],
            window["window_start"],
            window["window_end"],
        ]
        coverage_filter = ""
        if covered_date is not None:
            coverage_filter = "AND window_end > ? "
            parameters.append(covered_date.isoformat())
        blocker = self.connection.execute(
            "SELECT id, window_start, window_end, status FROM sync_window "
            "WHERE job_id=? AND id<>? AND status<>'COMPLETED' AND ("
            "window_start < ? OR (window_start = ? AND window_end < ?)) "
            f"{coverage_filter}"
            "ORDER BY window_start, window_end, id LIMIT 1",
            parameters,
        ).fetchone()
        if blocker is not None:
            raise WindowContinuityError(
                "Janela anterior ainda não foi concluída: "
                f"id={blocker['id']}, status={blocker['status']}, "
                f"intervalo={blocker['window_start']}..{blocker['window_end']}"
            )

    def get_watermark(self, job_id: str):
        return self.connection.execute(
            "SELECT * FROM sync_watermark WHERE job_id=?", (job_id,)
        ).fetchone()

    def recoverable_windows(
        self, job_id: str, now: datetime | str | None = None
    ):
        now_text = _utc_text(now)
        return self.connection.execute(
            "SELECT * FROM sync_window WHERE job_id=? AND ("
            "status IN ('PENDING', 'FAILED') OR "
            "(status='RUNNING' AND lease_expires_at <= ?)) "
            "ORDER BY window_start, window_end",
            (job_id, now_text),
        ).fetchall()

    def require_active_lease(
        self,
        window_id: str,
        lease_token: str,
        run_id: str,
        now: datetime | str | None = None,
    ):
        """Aplica fencing ao efeito protegido pela transação atual.

        A validação deve compartilhar a transação de escrita com o
        efeito protegido. Desse modo, outro worker não pode recuperar a janela
        entre a verificação do token e o commit desse efeito.
        """

        if not self.connection.in_transaction:
            raise RuntimeError(
                "Validação de lease para escrita exige transação ativa"
            )
        window = self._require_lease(
            window_id, lease_token, _utc_datetime(now)
        )
        self._require_active_run(window)
        if window["active_run_id"] != run_id:
            raise LeaseConflictError(
                "Lease não está vinculado a esta execução"
            )
        return window

    def _require_lease(
        self, window_id: str, lease_token: str, now: datetime
    ):
        row = self.get_window(window_id)
        if (
            row is None
            or row["status"] != "RUNNING"
            or row["lease_token"] != lease_token
        ):
            raise LeaseConflictError("Lease não pertence a esta execução")
        if not row["lease_expires_at"] or _utc_datetime(
            row["lease_expires_at"]
        ) <= now:
            raise LeaseExpiredError("Lease da janela expirou")
        return row

    def _mark_target_completed(
        self,
        window_id: str,
        target_key: str,
        fingerprint: str,
        now: datetime,
        run_id: str | None,
    ) -> None:
        now_text = now.isoformat(timespec="microseconds")
        self.connection.execute(
            "UPDATE sync_target_progress SET target_fingerprint=?, "
            "processed_fingerprint=?, status='COMPLETED', error_message=NULL, "
            "active_run_id=?, completed_at=?, updated_at=? "
            "WHERE window_id=? AND target_key=?",
            (
                fingerprint,
                fingerprint,
                run_id,
                now_text,
                now_text,
                window_id,
                target_key,
            ),
        )

    def _confirm_target_locked(
        self,
        window,
        target_key: str,
        fingerprint: str,
        current_time: datetime,
    ) -> None:
        existing = self.get_target(window["id"], target_key)
        now_text = current_time.isoformat(timespec="microseconds")
        if existing is None:
            self.connection.execute(
                "INSERT INTO sync_target_progress "
                "(window_id, target_key, target_fingerprint, "
                "processed_fingerprint, status, active_run_id, created_at, "
                "updated_at, completed_at) "
                "VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?, ?, ?)",
                (
                    window["id"],
                    target_key,
                    fingerprint,
                    fingerprint,
                    window["active_run_id"],
                    now_text,
                    now_text,
                    now_text,
                ),
            )
            return
        if existing["target_fingerprint"] != fingerprint:
            raise ValueError(
                "Fingerprint do alvo mudou durante o processamento"
            )
        self._mark_target_completed(
            window["id"],
            target_key,
            fingerprint,
            current_time,
            window["active_run_id"],
        )

    def _require_active_run(self, window) -> None:
        run_id = window["active_run_id"]
        run = self.connection.execute(
            "SELECT status FROM ingestion_run WHERE id=?", (run_id,)
        ).fetchone()
        if run is None or run["status"] != "RUNNING":
            raise ValueError(
                "Confirmação de progresso exige ingestion_run RUNNING"
            )
