-- Metadados da carga incremental da camada normalizada.
-- Aplicar somente no banco normalizado; nunca no banco RAW.

PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS normalization_reconciliation (
    normalization_run_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    staged_count INTEGER NOT NULL CHECK (staged_count >= 0),
    inserted_count INTEGER NOT NULL CHECK (inserted_count >= 0),
    updated_count INTEGER NOT NULL CHECK (updated_count >= 0),
    unchanged_count INTEGER NOT NULL CHECK (unchanged_count >= 0),
    target_count INTEGER NOT NULL CHECK (target_count >= 0),
    PRIMARY KEY (normalization_run_id, table_name),
    FOREIGN KEY (normalization_run_id) REFERENCES normalization_run(id)
);

CREATE INDEX IF NOT EXISTS idx_normalization_reconciliation_table
    ON normalization_reconciliation(table_name, normalization_run_id);
CREATE INDEX IF NOT EXISTS idx_lineage_entity_current
    ON normalization_lineage(normalized_table, entity_key, is_current);

COMMIT;
