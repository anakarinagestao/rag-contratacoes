from __future__ import annotations

import sqlite3

from .connection import connect_database


SCHEMA = """
CREATE TABLE IF NOT EXISTS ingestion_run (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    window_start TEXT,
    window_end TEXT,
    pages_processed INTEGER NOT NULL DEFAULT 0,
    records_received INTEGER NOT NULL DEFAULT 0,
    records_inserted INTEGER NOT NULL DEFAULT 0,
    records_updated INTEGER NOT NULL DEFAULT 0,
    records_unchanged INTEGER NOT NULL DEFAULT 0,
    records_failed INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_checkpoint (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    last_successful_sync TEXT,
    last_page INTEGER NOT NULL DEFAULT 0,
    last_run_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    updated_at TEXT NOT NULL,
    UNIQUE (source, resource_type),
    FOREIGN KEY (last_run_id) REFERENCES ingestion_run(id)
);

CREATE TABLE IF NOT EXISTS raw_asset (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    base_id TEXT NOT NULL,
    natureza TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    external_id TEXT NOT NULL,
    query_params TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    source_published_at TEXT,
    source_updated_at TEXT,
    fetched_at TEXT NOT NULL,
    corpus_version TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    ingestion_run_id TEXT NOT NULL,
    previous_version_id TEXT,
    processing_status TEXT NOT NULL DEFAULT 'RAW'
        CHECK (processing_status = 'RAW'),
    created_at TEXT NOT NULL,
    FOREIGN KEY (ingestion_run_id) REFERENCES ingestion_run(id),
    FOREIGN KEY (previous_version_id) REFERENCES raw_asset(id)
);

CREATE INDEX IF NOT EXISTS idx_raw_asset_identity
    ON raw_asset(source, resource_type, external_id);
CREATE INDEX IF NOT EXISTS idx_raw_asset_payload_hash
    ON raw_asset(payload_hash);
CREATE INDEX IF NOT EXISTS idx_raw_asset_ingestion_run
    ON raw_asset(ingestion_run_id);
CREATE INDEX IF NOT EXISTS idx_raw_asset_resource_type
    ON raw_asset(resource_type);
CREATE INDEX IF NOT EXISTS idx_raw_asset_corpus_version
    ON raw_asset(corpus_version);
"""

CANONICAL_IDENTITY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_raw_asset_canonical_identity
    ON raw_asset(base_id, resource_type, external_id);
"""

RAW_OBSERVATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_observation (
    observation_key TEXT PRIMARY KEY,
    raw_asset_id TEXT NOT NULL,
    base_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    cursor TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    source_timestamp_status TEXT NOT NULL
        CHECK (source_timestamp_status IN ('VALID', 'MISSING', 'INVALID')),
    source_timestamp_kind TEXT,
    source_updated_at_normalized TEXT,
    ingestion_run_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (raw_asset_id) REFERENCES raw_asset(id),
    FOREIGN KEY (ingestion_run_id) REFERENCES ingestion_run(id)
);

CREATE INDEX IF NOT EXISTS idx_raw_observation_identity
    ON raw_observation(base_id, resource_type, external_id);
CREATE INDEX IF NOT EXISTS idx_raw_observation_run
    ON raw_observation(ingestion_run_id);
CREATE INDEX IF NOT EXISTS idx_raw_observation_asset
    ON raw_observation(raw_asset_id);
"""

SYNC_V2_SCHEMA = """
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

CREATE TABLE IF NOT EXISTS sync_coverage_plan (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    uf TEXT NOT NULL,
    members_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (name, base_id, resource_type, uf)
);

CREATE TABLE IF NOT EXISTS sync_coverage_window (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'COMPLETED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (plan_id, window_start, window_end),
    CHECK (window_start <= window_end),
    FOREIGN KEY (plan_id) REFERENCES sync_coverage_plan(id)
);

CREATE TABLE IF NOT EXISTS sync_coverage_member (
    coverage_window_id TEXT NOT NULL,
    member_key TEXT NOT NULL,
    job_id TEXT,
    sync_window_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'COMPLETED')),
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (coverage_window_id, member_key),
    FOREIGN KEY (coverage_window_id) REFERENCES sync_coverage_window(id),
    FOREIGN KEY (job_id) REFERENCES sync_job(id),
    FOREIGN KEY (sync_window_id) REFERENCES sync_window(id)
);

CREATE TABLE IF NOT EXISTS sync_coverage_watermark (
    plan_id TEXT PRIMARY KEY,
    watermark_value TEXT NOT NULL,
    last_window_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES sync_coverage_plan(id),
    FOREIGN KEY (last_window_id) REFERENCES sync_coverage_window(id)
);

CREATE INDEX IF NOT EXISTS idx_sync_coverage_window_status
    ON sync_coverage_window(plan_id, status, window_start, window_end);
CREATE INDEX IF NOT EXISTS idx_sync_coverage_member_status
    ON sync_coverage_member(coverage_window_id, status, member_key);
"""


def init_db(connection: sqlite3.Connection) -> None:
    raw_table_existed = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'raw_asset'"
    ).fetchone() is not None
    connection.executescript(SCHEMA)
    # Bancos históricos não são migrados implicitamente. O novo índice é
    # criado automaticamente apenas em bancos novos; nos existentes, a migração
    # controlada deve ser aplicada depois de backup e validação.
    if not raw_table_existed:
        connection.executescript(CANONICAL_IDENTITY_INDEX_SQL)
        connection.executescript(RAW_OBSERVATION_SCHEMA)
        connection.executescript(SYNC_V2_SCHEMA)
    connection.commit()


def initialize_database(database_url: str | None = None) -> sqlite3.Connection:
    connection = connect_database(database_url)
    init_db(connection)
    return connection
