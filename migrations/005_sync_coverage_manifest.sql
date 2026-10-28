-- Migração manual e aditiva para cobertura composta por shards/alvos.
-- Aplicar somente após backup, validação e interrupção dos escritores.
BEGIN IMMEDIATE;

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

COMMIT;
