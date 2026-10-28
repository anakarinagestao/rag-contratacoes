-- Migração manual e aditiva. Não altera os registros de raw_asset.
-- Aplicar somente em uma cópia validada e sem escritores ativos.
BEGIN IMMEDIATE;

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

COMMIT;
