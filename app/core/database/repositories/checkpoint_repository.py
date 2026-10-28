from __future__ import annotations

import sqlite3
import uuid

from src.obs import utc_now


class CheckpointRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_checkpoint(
        self, source: str, resource_type: str
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sync_checkpoint WHERE source = ? "
            "AND resource_type = ?",
            (source, resource_type),
        ).fetchone()

    def get(self, source: str, resource_type: str) -> sqlite3.Row | None:
        """Alias de compatibilidade para get_checkpoint."""
        return self.get_checkpoint(source, resource_type)

    def update_checkpoint(
        self,
        source: str,
        resource_type: str,
        run_id: str,
        last_page: int,
    ) -> None:
        run = self.connection.execute(
            "SELECT status FROM ingestion_run WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None or run["status"] != "SUCCESS":
            raise ValueError(
                "O checkpoint só pode avançar após o run terminar com SUCCESS"
            )
        now = utc_now()
        self.connection.execute(
            "INSERT INTO sync_checkpoint "
            "(id, source, resource_type, last_successful_sync, last_page, "
            "last_run_id, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, resource_type) DO UPDATE SET "
            "last_successful_sync=excluded.last_successful_sync, "
            "last_page=excluded.last_page, last_run_id=excluded.last_run_id, "
            "status=excluded.status, updated_at=excluded.updated_at",
            (
                str(uuid.uuid4()),
                source,
                resource_type,
                now,
                last_page,
                run_id,
                "SUCCESS",
                now,
            ),
        )
        self.connection.commit()

    def update_progress(
        self,
        source: str,
        resource_type: str,
        run_id: str,
        last_page: int,
    ) -> None:
        """Persiste o último alvo completo durante uma execução longa."""
        run = self.connection.execute(
            "SELECT status FROM ingestion_run WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None or run["status"] != "RUNNING":
            raise ValueError(
                "O progresso só pode avançar durante um run em execução"
            )
        now = utc_now()
        self.connection.execute(
            "INSERT INTO sync_checkpoint "
            "(id, source, resource_type, last_successful_sync, last_page, "
            "last_run_id, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, resource_type) DO UPDATE SET "
            "last_page=excluded.last_page, last_run_id=excluded.last_run_id, "
            "status=excluded.status, updated_at=excluded.updated_at",
            (
                str(uuid.uuid4()),
                source,
                resource_type,
                None,
                last_page,
                run_id,
                "RUNNING",
                now,
            ),
        )
        self.connection.commit()

    def mark_failed(
        self,
        source: str,
        resource_type: str,
        run_id: str,
    ) -> None:
        self.connection.execute(
            "UPDATE sync_checkpoint SET status = ?, updated_at = ? "
            "WHERE source = ? AND resource_type = ? AND last_run_id = ?",
            ("FAILED", utc_now(), source, resource_type, run_id),
        )
        self.connection.commit()

    def save_success(
        self,
        source: str,
        resource_type: str,
        run_id: str,
        last_page: int,
    ) -> None:
        """Alias de compatibilidade para update_checkpoint."""
        self.update_checkpoint(source, resource_type, run_id, last_page)
