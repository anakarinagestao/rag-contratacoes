# Catálogo documental PNCP v2

O catálogo registra os arquivos anunciados pelo PNCP sem baixar ou processar
seu conteúdo. A identidade lógica permanece:

```text
(tipo_entidade, numero_controle_pncp, sequencial_documento)
```

O inventário é identificado pelo SHA-256 do conjunto ordenado de manifestos e
de seus conteúdos. A tabela de membros preserva as observações históricas e o
estado atual informa se a fonte continua disponível.

## Validação segura em uma cópia

Pare a API e qualquer processo que escreva no SQLite. Use a API de backup do
SQLite, pois copiar somente `app.db` pode ignorar transações presentes no WAL:

```bash
sqlite3 app.db ".backup '/tmp/pncp-app-backup.db'"
cp --reflink=auto /tmp/pncp-app-backup.db /tmp/pncp-catalog-test.db
sqlite3 /tmp/pncp-catalog-test.db < migrations/102_document_catalog_v2.sql
```

Reconcilie todos os manifestos locais somente na cópia:

```bash
.venv/bin/python -m \
  src.base_estruturada.pipelines.pipeline_persistencia_catalogo_documentos \
  --todos-manifestos-locais \
  --tipo todos \
  --snapshot-completo \
  --database-url sqlite:////tmp/pncp-catalog-test.db
```

Valide antes e depois:

```bash
sqlite3 -readonly /tmp/pncp-catalog-test.db \
  "PRAGMA integrity_check; PRAGMA foreign_key_check;"
```

`--snapshot-completo` exige `--todos-manifestos-locais --tipo todos`. Essa
proteção impede que um recorte mensal marque os demais documentos como
removidos.

## Resultado de referência

O ensaio completo de 22/10/2026 reconciliou 90 manifestos, 329.750 entidades e
549.171 referências documentais, sem duplicidades de chave de negócio e sem
violações de chave estrangeira. O ensaio foi realizado em `/tmp`; o caminho da
cópia não faz parte do contrato operacional.
