# Migrações controladas das camadas RAW e normalizada

Os arquivos deste diretório não são executados automaticamente em bancos
existentes. Essa decisão evita que a abertura de um banco histórico altere uma
base real sem revisão e backup.

## Backup seguro antes de uma migração

1. Confirme que não existe coletor gravando no banco.
2. Use a API de backup do SQLite para criar um arquivo novo em outro caminho.
3. Execute `PRAGMA quick_check` e `PRAGMA foreign_key_check` na cópia.
4. Gere SHA-256 do original e da cópia e registre tamanho e data.
5. Aplique a migração primeiro em outra cópia descartável.
6. Compare tabelas, contagens por recurso e chaves estrangeiras.

## 001_raw_canonical_identity_index.sql

Adiciona somente um índice não único sobre os campos já existentes:

```text
(base_id, resource_type, external_id)
```

O índice não altera payloads, checkpoints ou identificadores. Ele torna
eficiente a procura da mesma entidade quando ela é observada por coletores com
nomes de `source` diferentes.

## Identidades preservadas

- Entidade canônica: `(base_id, resource_type, external_id)`.
- Fonte/job: `source`.
- Execução: `ingestion_run_id`.
- Versão RAW: `raw_asset.id`.
- Conteúdo: `payload_hash` calculado sobre o JSON canônico.
- Momento da observação no PNCP: `source_updated_at`.

O `external_id` continua sendo produzido pelos coletores a partir das chaves
de negócio verificadas:

| Recurso | `external_id` |
|---|---|
| PCA | `idPcaPncp` |
| Contratação | `numeroControlePNCP` |
| Item | `numeroControlePNCP` + `numeroItem` |
| Resultado | `numeroControlePNCPCompra` + `numeroItem` + `sequencialResultado` |
| Contrato | `numeroControlePNCP` |
| Empenho | `numeroControlePNCPContrato` + `sequencialEmpenho` |
| Ata | `numeroControlePNCPAta` |

Por isso, `external_id` isolado não é tratado como globalmente único. Em
bancos históricos, onde `source_updated_at` ainda está vazio, o repositório
consulta o campo equivalente dentro do payload original. Se a API não fornecer
nenhuma data de atualização, uma repetição exata é tratada como replay
idempotente; sem um marcador temporal externo, ela não pode ser distinguida de
uma reversão exata posterior.

Exemplo de backup consistente (substitua os dois caminhos):

```bash
python3 -c 'import sqlite3; origem=sqlite3.connect("file:ORIGEM.db?mode=ro", uri=True); destino=sqlite3.connect("COPIA.db"); origem.backup(destino); destino.close(); origem.close()'
```

Não copie diretamente um arquivo `.db` enquanto houver um escritor ativo,
pois o estado pode também depender dos arquivos `-wal` e `-shm`.

## 002_sync_window_checkpoint.sql

Adiciona o controle de sincronização v2 sem converter nem renomear
`sync_checkpoint`:

- `sync_job`: identidade determinística da configuração, incluindo modo,
  recurso, endpoint, UF, modalidade e filtros;
- `sync_window`: intervalo diário fechado e inclusivo, estado, cursor e lease;
- `sync_target_progress`: progresso por identidade estável do alvo e seu
  fingerprint de configuração;
- `sync_watermark`: posição confirmada da fonte, com desempate para registros
  que possuem o mesmo timestamp.

O modelo legado continua sendo lido e escrito exclusivamente pelo fluxo
histórico atual. O modelo v2 não interpreta `last_page`, e sua ativação exige
uma migração explícita do banco e a seleção explícita do novo orquestrador.
Isso permite validar cada fonte separadamente antes da transição.

### Reversão

Enquanto nenhuma coleta v2 tiver sido ativada, a reversão consiste em remover,
em uma cópia e nesta ordem, `sync_watermark`, `sync_target_progress`,
`sync_window` e `sync_job`. Depois da ativação, esses registros são evidência de
auditoria; portanto, devem ser exportados e preservados antes de qualquer
remoção. A reversão nunca exige alterar `raw_asset`, `ingestion_run` ou
`sync_checkpoint`.

## 003_raw_observation.sql

Adiciona `raw_observation` sem alterar nenhuma linha de `raw_asset`. A tabela
separa a observação feita por uma execução da versão RAW observada:

- `observation_key` identifica de forma determinística a mesma observação
  quando uma janela é reexecutada;
- `raw_asset_id` aponta para a versão append-only observada; várias janelas
  podem apontar para a mesma versão sem duplicar o payload;
- a janela distingue uma reversão posterior de um replay;
- cursor e ordinal ficam registrados para auditoria, mas não integram a chave,
  pois a paginação da API pode mudar entre execuções;
- status, tipo e valor normalizado do timestamp registram se a ordenação
  temporal é confiável.

Registros históricos permanecem válidos mesmo sem uma linha correspondente
em `raw_observation`. Depois da migração, cada coleta registra sua observação,
inclusive quando o conteúdo é `UNCHANGED`, sem atualizar ou remover qualquer
payload anterior.

## 004_sync_reconciliation_target.sql

Adiciona uma fila durável para recursos dependentes que não possuem watermark
temporal global: itens, resultados homologados e empenhos. A fila registra o
alvo canônico, sua configuração, cadência, horizonte e o último resultado da
consulta (`FOUND`, `EMPTY` ou `NOT_FOUND`).

A tabela não altera `raw_asset`, não reabre janelas concluídas e não substitui
`sync_target_progress`. Cada reconciliação devida é materializada como alvo de
uma nova janela; a conclusão do alvo e o reagendamento da fila devem ocorrer na
mesma transação.

## 005_sync_coverage_manifest.sql

Adiciona o manifesto imutável de cobertura composta. Um plano congela o
conjunto de membros esperado para um recurso e recorte, por exemplo todas as
modalidades de contratação ou todos os pares CNPJ/unidade de um inventário.

Cada janela composta acompanha seus membros separadamente. Os watermarks dos
jobs continuam representando somente seus shards; `sync_coverage_watermark`
só avança quando todos os membros esperados da janela terminam e a continuidade
temporal do plano foi comprovada. A migração não altera dados RAW nem
checkpoints existentes.

## 100_normalized_schema.sql

Cria uma base normalizada independente da RAW. As chaves e hashes RAW são
preservados como linhagem textual, sem chave estrangeira entre arquivos
SQLite. Este DDL nunca deve ser executado sobre o banco RAW.

## 101_normalization_reconciliation.sql

Adiciona ao banco normalizado a auditoria de cada reconciliação incremental,
com contagens de linhas de estágio, inseridas, atualizadas, inalteradas e
presentes no destino. Também cria o índice usado para trocar de forma eficiente
a versão de linhagem marcada como corrente. Esta migração não modifica a RAW.

## 102_document_catalog_v2.sql

Adiciona estruturas complementares ao catálogo existente, sem remover nem
reescrever `document_catalog_entries`:

- `document_catalog_inventories`: congela a identidade e o escopo de cada
  conjunto de manifestos;
- `document_catalog_inventory_members`: preserva a observação e o fingerprint
  dos metadados de cada documento no inventário;
- `document_catalog_states`: registra primeira/ultima observação,
  disponibilidade atual e eventual desaparecimento da fonte.

Um snapshot somente deve ser declarado autoritativo quando contiver todos os
manifestos locais esperados. Importações parciais nunca marcam documentos como
removidos. A migração não baixa arquivos, não executa OCR e não altera a RAW
ou a base normalizada.

## 103_document_processing_lease.sql

Adiciona controle recuperável à fila documental: lease com expiração,
token de fencing, timestamps operacionais, código de erro seguro e unicidade
parcial de tarefa ativa por documento. Deve ser aplicada somente depois de
backup e validação em cópia isolada.
