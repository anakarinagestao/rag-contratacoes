from __future__ import annotations

import sqlite3
import uuid
from typing import Mapping

from src.obs import utc_now


class RunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def start_run(
        self,
        source: str,
        resource_type: str,
        window_start: str | None = None,
        window_end: str | None = None,
    ) -> str:
        run_id = str(uuid.uuid4())
        now = utc_now()
        self.connection.execute(
            "INSERT INTO ingestion_run "
            "(id, source, resource_type, started_at, status, window_start, "
            "window_end, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                source,
                resource_type,
                now,
                "RUNNING",
                window_start,
                window_end,
                now,
            ),
        )
        self.connection.commit()
        return run_id

    def start(self, source: str, resource_type: str, **kwargs) -> str:
        """Alias de compatibilidade para start_run."""
        return self.start_run(source, resource_type, **kwargs)

    def finish_run(
        self,
        run_id: str,
        counts: Mapping[str, int],
        pages_processed: int = 0,
        status: str = "SUCCESS",
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            "UPDATE ingestion_run SET status = ?, finished_at = ?, "
            "pages_processed = ?, records_received = ?, records_inserted = ?, "
            "records_updated = ?, records_unchanged = ?, records_failed = ?, "
            "error_message = ? WHERE id = ?",
            (
                status,
                utc_now(),
                pages_processed,
                counts["records_seen"],
                counts["NEW"],
                counts["UPDATED"],
                counts["UNCHANGED"],
                counts.get("FAILED", 0),
                error,
                run_id,
            ),
        )
        self.connection.commit()

    def finish(
        self,
        run_id: str,
        counts: Mapping[str, int],
        pages_processed: int = 0,
        status: str = "SUCCESS",
        error: str | None = None,
    ) -> None:
        """Alias de compatibilidade para finish_run."""
        self.finish_run(run_id, counts, pages_processed, status, error)

    def fail_run(
        self,
        run_id: str,
        counts: Mapping[str, int],
        pages_processed: int = 0,
        error: str | None = None,
    ) -> None:
        self.finish_run(
            run_id,
            counts,
            pages_processed=pages_processed,
            status="FAILED",
            error=error,
        )
