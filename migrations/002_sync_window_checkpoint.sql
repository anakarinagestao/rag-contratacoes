-- Migração manual e aditiva. Não converte nem modifica sync_checkpoint.
-- Aplicar somente após backup, validação e interrupção dos escritores.
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS sync_job (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('historical', 'incremental')),
    base_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    uf TEXT,
    modality INTEGER,
    config_hash TEXT NOT NULL UNIQUE,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_window (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
    progress_cursor TEXT,
    active_run_id TEXT,
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (job_id, window_start, window_end),
    CHECK (window_start <= window_end),
    FOREIGN KEY (job_id) REFERENCES sync_job(id),
    FOREIGN KEY (active_run_id) REFERENCES ingestion_run(id)
);

CREATE TABLE IF NOT EXISTS sync_target_progress (
    window_id TEXT NOT NULL,
    target_key TEXT NOT NULL,
    target_fingerprint TEXT NOT NULL,
    processed_fingerprint TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
    active_run_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (window_id, target_key),
    FOREIGN KEY (window_id) REFERENCES sync_window(id),
    FOREIGN KEY (active_run_id) REFERENCES ingestion_run(id)
);

CREATE TABLE IF NOT EXISTS sync_watermark (
    job_id TEXT PRIMARY KEY,
    watermark_value TEXT NOT NULL,
    tie_breaker TEXT NOT NULL DEFAULT '',
    last_window_id TEXT NOT NULL,
    confirmed_run_id TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES sync_job(id),
    FOREIGN KEY (last_window_id) REFERENCES sync_window(id),
    FOREIGN KEY (confirmed_run_id) REFERENCES ingestion_run(id)
);

CREATE INDEX IF NOT EXISTS idx_sync_window_job_status
    ON sync_window(job_id, status, window_start, window_end);
CREATE INDEX IF NOT EXISTS idx_sync_window_lease
    ON sync_window(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_sync_target_status
    ON sync_target_progress(window_id, status, target_key);

COMMIT;
