-- Camada normalizada PNCP/RJ v1.
--
-- Esta migração cria um banco normalizado independente. Os identificadores
-- da RAW são preservados como linhagem textual, sem FOREIGN KEY para
-- raw_asset, pois RAW e normalizada podem residir em arquivos SQLite distintos.
-- Não aplicar diretamente no snapshot RAW oficial.

PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS normalization_run (
    id TEXT PRIMARY KEY,
    source_snapshot_sha256 TEXT NOT NULL,
    transform_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    records_seen INTEGER NOT NULL DEFAULT 0 CHECK (records_seen >= 0),
    records_written INTEGER NOT NULL DEFAULT 0 CHECK (records_written >= 0),
    records_skipped INTEGER NOT NULL DEFAULT 0 CHECK (records_skipped >= 0),
    records_rejected INTEGER NOT NULL DEFAULT 0 CHECK (records_rejected >= 0),
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS normalization_coverage (
    resource_type TEXT PRIMARY KEY,
    coverage_status TEXT NOT NULL
        CHECK (coverage_status IN ('COMPLETE', 'PARTIAL', 'KNOWN_GAPS')),
    period_start TEXT,
    period_end TEXT,
    expected_parent_count INTEGER CHECK (expected_parent_count >= 0),
    processed_parent_count INTEGER CHECK (processed_parent_count >= 0),
    record_count INTEGER NOT NULL DEFAULT 0 CHECK (record_count >= 0),
    known_gap_count INTEGER NOT NULL DEFAULT 0 CHECK (known_gap_count >= 0),
    inventory_id TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orgao (
    cnpj TEXT PRIMARY KEY CHECK (length(cnpj) = 14),
    razao_social TEXT NOT NULL,
    poder_id TEXT,
    esfera_id TEXT,
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unidade (
    orgao_cnpj TEXT NOT NULL,
    codigo_unidade TEXT NOT NULL,
    nome_unidade TEXT,
    codigo_ibge TEXT,
    municipio_nome TEXT,
    uf_sigla TEXT NOT NULL CHECK (length(uf_sigla) = 2),
    uf_nome TEXT,
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    PRIMARY KEY (orgao_cnpj, codigo_unidade),
    FOREIGN KEY (orgao_cnpj) REFERENCES orgao(cnpj)
);

CREATE TABLE IF NOT EXISTS fornecedor (
    tipo_pessoa TEXT NOT NULL,
    ni_fornecedor TEXT NOT NULL,
    nome_razao_social TEXT,
    porte_id INTEGER,
    porte_nome TEXT,
    natureza_juridica_id TEXT,
    natureza_juridica_nome TEXT,
    codigo_pais TEXT,
    identificador_pessoal INTEGER NOT NULL DEFAULT 0
        CHECK (identificador_pessoal IN (0, 1)),
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    PRIMARY KEY (tipo_pessoa, ni_fornecedor)
);

CREATE TABLE IF NOT EXISTS contratacao (
    numero_controle_pncp TEXT PRIMARY KEY,
    orgao_cnpj TEXT NOT NULL,
    codigo_unidade TEXT NOT NULL,
    ano_compra INTEGER NOT NULL,
    sequencial_compra INTEGER NOT NULL,
    numero_compra TEXT,
    processo TEXT,
    modalidade_id INTEGER NOT NULL,
    modalidade_nome TEXT,
    modo_disputa_id INTEGER,
    modo_disputa_nome TEXT,
    tipo_instrumento_codigo INTEGER,
    tipo_instrumento_nome TEXT,
    situacao_compra_id INTEGER,
    situacao_compra_nome TEXT,
    amparo_legal_codigo INTEGER,
    amparo_legal_nome TEXT,
    objeto_compra TEXT,
    informacao_complementar TEXT,
    srp INTEGER NOT NULL CHECK (srp IN (0, 1)),
    valor_total_estimado_centavos INTEGER,
    valor_total_homologado_centavos INTEGER,
    data_publicacao_pncp TEXT,
    data_abertura_proposta TEXT,
    data_encerramento_proposta TEXT,
    source_updated_at TEXT,
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    FOREIGN KEY (orgao_cnpj, codigo_unidade)
        REFERENCES unidade(orgao_cnpj, codigo_unidade)
);

CREATE TABLE IF NOT EXISTS contratacao_item (
    numero_controle_pncp TEXT NOT NULL,
    numero_item INTEGER NOT NULL,
    descricao TEXT,
    quantidade NUMERIC,
    unidade_medida TEXT,
    valor_unitario_estimado_centavos INTEGER,
    valor_total_estimado_centavos INTEGER,
    material_ou_servico TEXT,
    material_ou_servico_nome TEXT,
    criterio_julgamento_id INTEGER,
    criterio_julgamento_nome TEXT,
    categoria_item_id INTEGER,
    categoria_item_nome TEXT,
    situacao_item_id INTEGER,
    situacao_item_nome TEXT,
    tem_resultado INTEGER NOT NULL CHECK (tem_resultado IN (0, 1)),
    data_inclusao TEXT,
    source_updated_at TEXT,
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    PRIMARY KEY (numero_controle_pncp, numero_item),
    FOREIGN KEY (numero_controle_pncp)
        REFERENCES contratacao(numero_controle_pncp)
);

CREATE TABLE IF NOT EXISTS resultado_item (
    numero_controle_pncp TEXT NOT NULL,
    numero_item INTEGER NOT NULL,
    sequencial_resultado INTEGER NOT NULL,
    tipo_pessoa_fornecedor TEXT NOT NULL,
    ni_fornecedor TEXT NOT NULL,
    ordem_classificacao_srp INTEGER,
    quantidade_homologada NUMERIC,
    valor_unitario_homologado_centavos INTEGER,
    valor_total_homologado_centavos INTEGER,
    percentual_desconto NUMERIC,
    situacao_resultado_id INTEGER,
    situacao_resultado_nome TEXT,
    aplicacao_beneficio_me_epp INTEGER
        CHECK (aplicacao_beneficio_me_epp IN (0, 1)),
    data_resultado TEXT,
    data_cancelamento TEXT,
    motivo_cancelamento TEXT,
    source_updated_at TEXT,
    data_quality_status TEXT NOT NULL DEFAULT 'VALID'
        CHECK (data_quality_status IN ('VALID', 'SUSPECT_DATE', 'INVALID')),
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    PRIMARY KEY (
        numero_controle_pncp,
        numero_item,
        sequencial_resultado
    ),
    FOREIGN KEY (numero_controle_pncp, numero_item)
        REFERENCES contratacao_item(numero_controle_pncp, numero_item),
    FOREIGN KEY (tipo_pessoa_fornecedor, ni_fornecedor)
        REFERENCES fornecedor(tipo_pessoa, ni_fornecedor)
);

CREATE TABLE IF NOT EXISTS contrato (
    numero_controle_pncp TEXT PRIMARY KEY,
    numero_controle_compra_externo TEXT NOT NULL,
    contratacao_id TEXT,
    parent_resolution_status TEXT NOT NULL
        CHECK (parent_resolution_status IN ('RESOLVED', 'UNRESOLVED', 'OUT_OF_SCOPE')),
    orgao_cnpj TEXT NOT NULL,
    codigo_unidade TEXT NOT NULL,
    tipo_pessoa_fornecedor TEXT NOT NULL,
    ni_fornecedor TEXT,
    nome_fornecedor_informado TEXT,
    supplier_resolution_status TEXT NOT NULL DEFAULT 'RESOLVED'
        CHECK (supplier_resolution_status IN ('RESOLVED', 'MISSING_IDENTIFIER')),
    ano_contrato INTEGER,
    sequencial_contrato INTEGER,
    numero_contrato_empenho TEXT,
    processo TEXT,
    objeto_contrato TEXT,
    tipo_contrato_id INTEGER,
    tipo_contrato_nome TEXT,
    categoria_processo_id INTEGER,
    categoria_processo_nome TEXT,
    valor_inicial_centavos INTEGER,
    valor_global_centavos INTEGER,
    valor_acumulado_centavos INTEGER,
    valor_parcela_centavos INTEGER,
    numero_parcelas INTEGER,
    data_publicacao_pncp TEXT,
    data_assinatura TEXT,
    vigencia_inicio TEXT,
    vigencia_fim TEXT,
    source_updated_at TEXT,
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    CHECK (
        (parent_resolution_status = 'RESOLVED' AND contratacao_id IS NOT NULL)
        OR
        (parent_resolution_status IN ('UNRESOLVED', 'OUT_OF_SCOPE')
            AND contratacao_id IS NULL)
    ),
    CHECK (
        (supplier_resolution_status = 'RESOLVED' AND ni_fornecedor IS NOT NULL)
        OR
        (supplier_resolution_status = 'MISSING_IDENTIFIER' AND ni_fornecedor IS NULL)
    ),
    FOREIGN KEY (contratacao_id)
        REFERENCES contratacao(numero_controle_pncp),
    FOREIGN KEY (orgao_cnpj, codigo_unidade)
        REFERENCES unidade(orgao_cnpj, codigo_unidade),
    FOREIGN KEY (tipo_pessoa_fornecedor, ni_fornecedor)
        REFERENCES fornecedor(tipo_pessoa, ni_fornecedor)
);

CREATE TABLE IF NOT EXISTS empenho (
    numero_controle_contrato TEXT NOT NULL,
    sequencial_empenho INTEGER NOT NULL,
    numero_empenho TEXT,
    ano INTEGER,
    tipo_pessoa_credor TEXT NOT NULL,
    ni_credor_fornecedor TEXT NOT NULL,
    nome_credor_fornecedor TEXT,
    codigo_natureza_despesa TEXT,
    numero_plano_interno TEXT,
    situacao_empenho_id INTEGER,
    situacao_empenho_nome TEXT,
    data_emissao TEXT,
    data_situacao TEXT,
    valor_total_centavos INTEGER,
    codigo_emenda TEXT,
    coverage_status TEXT NOT NULL DEFAULT 'PARTIAL'
        CHECK (coverage_status IN ('PARTIAL', 'COMPLETE')),
    source_updated_at TEXT,
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    PRIMARY KEY (numero_controle_contrato, sequencial_empenho),
    FOREIGN KEY (numero_controle_contrato)
        REFERENCES contrato(numero_controle_pncp)
);

CREATE TABLE IF NOT EXISTS ata_registro_preco (
    numero_controle_pncp_ata TEXT PRIMARY KEY,
    numero_controle_compra_externo TEXT NOT NULL,
    contratacao_id TEXT,
    parent_resolution_status TEXT NOT NULL
        CHECK (parent_resolution_status IN ('RESOLVED', 'UNRESOLVED', 'OUT_OF_SCOPE')),
    orgao_cnpj TEXT NOT NULL,
    codigo_unidade TEXT NOT NULL,
    ano_ata INTEGER,
    numero_ata TEXT,
    objeto_contratacao TEXT,
    data_publicacao_pncp TEXT,
    data_assinatura TEXT,
    vigencia_inicio TEXT,
    vigencia_fim TEXT,
    cancelado INTEGER NOT NULL CHECK (cancelado IN (0, 1)),
    data_cancelamento TEXT,
    possibilidade_adesao INTEGER CHECK (possibilidade_adesao IN (0, 1)),
    source_updated_at TEXT,
    coverage_status TEXT NOT NULL DEFAULT 'PARTIAL'
        CHECK (coverage_status IN ('PARTIAL', 'COMPLETE')),
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    CHECK (
        (parent_resolution_status = 'RESOLVED' AND contratacao_id IS NOT NULL)
        OR
        (parent_resolution_status IN ('UNRESOLVED', 'OUT_OF_SCOPE')
            AND contratacao_id IS NULL)
    ),
    FOREIGN KEY (contratacao_id)
        REFERENCES contratacao(numero_controle_pncp),
    FOREIGN KEY (orgao_cnpj, codigo_unidade)
        REFERENCES unidade(orgao_cnpj, codigo_unidade)
);

CREATE TABLE IF NOT EXISTS pca (
    id_pca_pncp TEXT PRIMARY KEY,
    orgao_cnpj TEXT NOT NULL,
    codigo_unidade TEXT NOT NULL,
    ano_pca INTEGER NOT NULL,
    nome_unidade TEXT,
    data_publicacao_pncp TEXT,
    source_updated_at TEXT,
    source_format TEXT NOT NULL
        CHECK (source_format IN ('TOP_LEVEL', 'UASG_WRAPPER')),
    scope_status TEXT NOT NULL
        CHECK (scope_status IN ('IN_SCOPE', 'OUT_OF_SCOPE', 'MIXED', 'UNKNOWN')),
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    FOREIGN KEY (orgao_cnpj, codigo_unidade)
        REFERENCES unidade(orgao_cnpj, codigo_unidade)
);

CREATE TABLE IF NOT EXISTS pca_item (
    id_pca_pncp TEXT NOT NULL,
    numero_item INTEGER NOT NULL,
    descricao TEXT,
    quantidade_estimada NUMERIC,
    unidade_fornecimento TEXT,
    valor_unitario_planejado_centavos INTEGER,
    valor_total_planejado_centavos INTEGER,
    valor_orcamento_exercicio_centavos INTEGER,
    data_desejada TEXT,
    grupo_contratacao_codigo TEXT,
    grupo_contratacao_nome TEXT,
    categoria_item_nome TEXT,
    codigo_item TEXT,
    classificacao_superior_codigo TEXT,
    classificacao_superior_nome TEXT,
    source_updated_at TEXT,
    current_source_raw_asset_id TEXT NOT NULL,
    current_source_payload_hash TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    PRIMARY KEY (id_pca_pncp, numero_item),
    FOREIGN KEY (id_pca_pncp) REFERENCES pca(id_pca_pncp)
);

CREATE TABLE IF NOT EXISTS pca_contratacao_link (
    id_pca_pncp TEXT NOT NULL,
    numero_item_pca INTEGER NOT NULL,
    numero_controle_compra TEXT NOT NULL,
    numero_item_compra INTEGER NOT NULL,
    association_method TEXT NOT NULL
        CHECK (association_method IN ('EXPLICIT', 'RULE', 'MANUAL')),
    confidence NUMERIC NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL
        CHECK (status IN ('CANDIDATE', 'CONFIRMED', 'REJECTED')),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    PRIMARY KEY (
        id_pca_pncp,
        numero_item_pca,
        numero_controle_compra,
        numero_item_compra
    ),
    FOREIGN KEY (id_pca_pncp, numero_item_pca)
        REFERENCES pca_item(id_pca_pncp, numero_item),
    FOREIGN KEY (numero_controle_compra, numero_item_compra)
        REFERENCES contratacao_item(numero_controle_pncp, numero_item)
);

CREATE TABLE IF NOT EXISTS normalization_lineage (
    normalized_table TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    raw_asset_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    normalization_run_id TEXT NOT NULL,
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    observed_at TEXT NOT NULL,
    PRIMARY KEY (normalized_table, entity_key, raw_asset_id),
    FOREIGN KEY (normalization_run_id) REFERENCES normalization_run(id)
);

CREATE TABLE IF NOT EXISTS normalization_issue (
    id TEXT PRIMARY KEY,
    normalization_run_id TEXT NOT NULL,
    raw_asset_id TEXT,
    resource_type TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'ERROR')),
    field_name TEXT,
    observed_value TEXT,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (normalization_run_id) REFERENCES normalization_run(id)
);

CREATE INDEX IF NOT EXISTS idx_unidade_uf_municipio
    ON unidade(uf_sigla, municipio_nome);
CREATE INDEX IF NOT EXISTS idx_contratacao_orgao_data
    ON contratacao(orgao_cnpj, data_publicacao_pncp);
CREATE INDEX IF NOT EXISTS idx_contratacao_modalidade_data
    ON contratacao(modalidade_id, data_publicacao_pncp);
CREATE INDEX IF NOT EXISTS idx_item_descricao
    ON contratacao_item(descricao);
CREATE INDEX IF NOT EXISTS idx_resultado_fornecedor
    ON resultado_item(tipo_pessoa_fornecedor, ni_fornecedor);
CREATE INDEX IF NOT EXISTS idx_resultado_data
    ON resultado_item(data_resultado);
CREATE INDEX IF NOT EXISTS idx_contrato_compra
    ON contrato(numero_controle_compra_externo);
CREATE INDEX IF NOT EXISTS idx_contrato_fornecedor
    ON contrato(tipo_pessoa_fornecedor, ni_fornecedor);
CREATE INDEX IF NOT EXISTS idx_empenho_data
    ON empenho(data_emissao);
CREATE INDEX IF NOT EXISTS idx_ata_compra
    ON ata_registro_preco(numero_controle_compra_externo);
CREATE INDEX IF NOT EXISTS idx_pca_orgao_ano
    ON pca(orgao_cnpj, ano_pca);
CREATE INDEX IF NOT EXISTS idx_pca_item_grupo
    ON pca_item(grupo_contratacao_codigo);
CREATE INDEX IF NOT EXISTS idx_lineage_raw_asset
    ON normalization_lineage(raw_asset_id);
CREATE INDEX IF NOT EXISTS idx_lineage_entity_current
    ON normalization_lineage(normalized_table, entity_key, is_current);
CREATE INDEX IF NOT EXISTS idx_issue_resource_code
    ON normalization_issue(resource_type, issue_code);

COMMIT;
