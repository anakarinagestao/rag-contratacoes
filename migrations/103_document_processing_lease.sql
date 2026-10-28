-- Controle aditivo da fila documental: recupera workers interrompidos e
-- impede conclusão por um worker que perdeu a posse da tarefa.
ALTER TABLE document_processing_jobs ADD COLUMN error_code VARCHAR(100);
ALTER TABLE document_processing_jobs ADD COLUMN lease_owner VARCHAR(200);
ALTER TABLE document_processing_jobs ADD COLUMN lease_token VARCHAR(36);
ALTER TABLE document_processing_jobs ADD COLUMN lease_expires_at DATETIME;
ALTER TABLE document_processing_jobs ADD COLUMN iniciado_em DATETIME;
ALTER TABLE document_processing_jobs ADD COLUMN concluido_em DATETIME;

CREATE UNIQUE INDEX IF NOT EXISTS uq_document_processing_active_catalog
    ON document_processing_jobs(catalog_entry_id)
    WHERE status IN ('PENDENTE', 'EM_PROCESSAMENTO');
CREATE INDEX IF NOT EXISTS ix_document_processing_jobs_lease_token
    ON document_processing_jobs(lease_token);
CREATE INDEX IF NOT EXISTS ix_document_processing_jobs_lease_expires_at
    ON document_processing_jobs(lease_expires_at);
