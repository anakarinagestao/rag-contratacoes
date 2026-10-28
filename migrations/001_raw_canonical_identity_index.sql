-- Migração manual: não é executada automaticamente em bancos existentes.
-- Pré-requisitos: backup SQLite validado, nenhum coletor ativo e quick_check=ok.
BEGIN IMMEDIATE;

CREATE INDEX IF NOT EXISTS idx_raw_asset_canonical_identity
    ON raw_asset(base_id, resource_type, external_id);

COMMIT;
