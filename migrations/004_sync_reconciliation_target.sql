-- Migração manual e aditiva para a fila de reconciliação.
-- Aplicar somente após backup, validação e interrupção dos escritores.
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS sync_reconciliation_target (
    job_id TEXT NOT NULL,
    target_key TEXT NOT NULL,
    target_json TEXT NOT NULL,
    target_fingerprint TEXT NOT NULL,
    cadence_days INTEGER NOT NULL CHECK (cadence_days > 0),
    eligible_until TEXT NOT NULL,
    next_due_at TEXT NOT NULL,
    last_outcome TEXT
        CHECK (last_outcome IN ('FOUND', 'EMPTY', 'NOT_FOUND')),
    last_checked_at TEXT,
    last_found_at TEXT,
    consecutive_empty INTEGER NOT NULL DEFAULT 0,
    last_window_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, target_key),
    FOREIGN KEY (job_id) REFERENCES sync_job(id),
    FOREIGN KEY (last_window_id) REFERENCES sync_window(id)
);

CREATE INDEX IF NOT EXISTS idx_sync_reconciliation_due
    ON sync_reconciliation_target(job_id, next_due_at, eligible_until, target_key);

COMMIT;
