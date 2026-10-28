PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS document_catalog_inventories (
    id VARCHAR(64) PRIMARY KEY,
    manifest_set_hash VARCHAR(64) NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    authoritative_full_snapshot BOOLEAN NOT NULL DEFAULT 0,
    status VARCHAR NOT NULL
        CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    manifest_count INTEGER NOT NULL DEFAULT 0,
    documents_seen INTEGER NOT NULL DEFAULT 0,
    documents_cataloged INTEGER NOT NULL DEFAULT 0,
    documents_updated INTEGER NOT NULL DEFAULT 0,
    documents_missing INTEGER NOT NULL DEFAULT 0,
    error_code VARCHAR,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_reconciled_at DATETIME,
    completed_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_document_catalog_inventories_status
    ON document_catalog_inventories(status);

CREATE TABLE IF NOT EXISTS document_catalog_states (
    catalog_entry_id INTEGER PRIMARY KEY,
    availability_status VARCHAR NOT NULL
        CHECK (availability_status IN ('AVAILABLE', 'REMOVED')),
    current_fingerprint VARCHAR(64) NOT NULL,
    first_seen_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    missing_since DATETIME,
    last_inventory_id VARCHAR(64) NOT NULL,
    FOREIGN KEY (catalog_entry_id)
        REFERENCES document_catalog_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (last_inventory_id)
        REFERENCES document_catalog_inventories(id)
);

CREATE INDEX IF NOT EXISTS idx_document_catalog_states_availability
    ON document_catalog_states(availability_status);
CREATE INDEX IF NOT EXISTS idx_document_catalog_states_last_inventory
    ON document_catalog_states(last_inventory_id);

CREATE TABLE IF NOT EXISTS document_catalog_inventory_members (
    inventory_id VARCHAR(64) NOT NULL,
    catalog_entry_id INTEGER NOT NULL,
    metadata_fingerprint VARCHAR(64) NOT NULL,
    observed_at DATETIME NOT NULL,
    PRIMARY KEY (inventory_id, catalog_entry_id),
    FOREIGN KEY (inventory_id)
        REFERENCES document_catalog_inventories(id) ON DELETE CASCADE,
    FOREIGN KEY (catalog_entry_id)
        REFERENCES document_catalog_entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_document_catalog_members_fingerprint
    ON document_catalog_inventory_members(metadata_fingerprint);
