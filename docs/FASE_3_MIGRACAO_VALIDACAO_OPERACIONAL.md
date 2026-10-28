# PNCP/RJ — Fase 3: migração e validação operacional

Data: 2026-10-19
Branch: `feature/ingestao-incremental-pncp`
Escopo: validação exclusiva em cópia isolada; banco oficial somente leitura

## 1. Resultado executivo

A Fase 3 foi aprovada em uma cópia integral da RAW. As migrações `001` a
`005` foram aplicadas em ordem, a integridade SQLite foi preservada e o piloto
online controlado confirmou respostas reais `200`, `204` e `404`, paginação e
reprocessamento idempotente.

O piloto também revelou que uma janela com lacuna temporal era rejeitada apenas
depois de consumir a API e persistir as versões RAW. A cópia afetada foi
descartada, o runner passou a validar a continuidade antes do transporte e uma
nova cópia limpa confirmou que a lacuna não cria janela, execução, observação
ou versão RAW.

O banco oficial **não foi migrado nem alterado**. A aplicação das migrações
nele e a ativação automática pertencem à Fase 4.

## 2. Travas de segurança

Antes da cópia foram confirmados:

- branch limpa e sincronizada com o remoto;
- serviços `pncp-historical-queue` e `pncp-resultados-auto-resume` inativos;
- nenhum processo de coleta PNCP ativo;
- nenhum processo com o banco oficial aberto;
- zero execuções `RUNNING` em `ingestion_run`;
- `PRAGMA integrity_check = ok`;
- zero linhas em `PRAGMA foreign_key_check`.

As operações de migração e piloto usaram somente arquivos sob `/tmp`.

## 3. Banco e backup

Banco oficial auditado:

```text
data/debug/pncp_contracts_2023_t1_all.db
tamanho: 4.543.479.808 bytes
SHA-256: 0aa81991aaeb19787d0bffe354636a7b34ff4997d903d1de8201e68245d87460
```

Backup pré-Fase 3:

```text
data/debug/backups/pncp_contracts_2023_t1_all_pre_phase3_20261019.sqlite.gz
tamanho compactado: aproximadamente 580 MiB
```

O arquivo passou em `gzip -t`. O fluxo descompactado produziu o mesmo SHA-256
do banco oficial. O diretório de backups está ignorado pelo Git.

## 4. Linha de base anterior à migração

| Métrica | Valor |
|---|---:|
| Tabelas | 4 |
| Índices | 12 |
| `raw_asset` | 1.391.174 |
| `ingestion_run` | 154 |
| `sync_checkpoint` | 51 |
| Execuções `RUNNING` | 0 |

Contagens RAW preservadas antes do piloto:

| Recurso | Registros |
|---|---:|
| `ata_registro_preco` | 80.761 |
| `contratacao` | 180.151 |
| `contratacao_item` | 557.211 |
| `contrato` | 151.898 |
| `empenho` | 1.900 |
| `pca` | 1.578 |
| `resultado_item` | 417.675 |

## 5. Migrações validadas

Foram aplicadas na cópia, nesta ordem:

1. `001_raw_canonical_identity_index.sql`;
2. `002_sync_window_checkpoint.sql`;
3. `003_raw_observation.sql`;
4. `004_sync_reconciliation_target.sql`;
5. `005_sync_coverage_manifest.sql`.

Estruturas adicionadas:

- `sync_job`;
- `sync_window`;
- `sync_target_progress`;
- `sync_watermark`;
- `raw_observation`;
- `sync_reconciliation_target`;
- `sync_coverage_plan`;
- `sync_coverage_window`;
- `sync_coverage_member`;
- `sync_coverage_watermark`.

Os sete índices explícitos esperados também foram encontrados. Todas as
novas tabelas iniciaram vazias e as contagens históricas permaneceram
idênticas. Apó a migração:

```text
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: zero linhas
```

## 6. Piloto online real

### 6.1 HTTP 204

Recorte:

```text
recurso: contratacao
UF: RJ
modalidade: 6
data: 2023-04-01
```

O PNCP respondeu `204`. A execução terminou com `SUCCESS`, zero registros e
watermark em `2023-04-01T23:59:59.999999`.

### 6.2 HTTP 200 e paginação

Recorte:

```text
recurso: contratacao
UF: RJ
modalidade: 6
data: 2026-10-16
```

Resultado da primeira execução na cópia limpa:

| Métrica | Valor |
|---|---:|
| Páginas | 5 |
| Registros recebidos | 244 |
| `NEW` | 0 |
| `UPDATED` | 146 |
| `UNCHANGED` | 98 |
| Falhas | 0 |

As 146 atualizações refletem diferenças reais entre o estado atual da API e
o snapshot histórico. A RAW da cópia passou de 1.391.174 para 1.391.320 versões
e recebeu 244 observações.

### 6.3 Reprocessamento idempotente

A mesma janela foi executada novamente com `--reprocess`:

```text
records_seen: 244
NEW: 0
UPDATED: 0
UNCHANGED: 244
raw_asset depois: 1.391.320
raw_observation depois: 244
```

Nenhuma versão ou observação foi duplicada.

### 6.4 HTTP 404 contextual

O endpoint de empenhos do contrato público
`64011311000139-2-000029/2026` respondeu `404` com a mensagem de que nenhum
empenho foi encontrado. O adaptador retornou:

```text
record_count: 0
reconciliation_outcome: NOT_FOUND
```

O `404` permaneceu restrito a esse relacionamento; ele não foi generalizado
como vazio para feeds globais.

## 7. Achado e correção do preflight

Na primeira cópia, uma tentativa de saltar de `2023-04-01` para `2026-10-16`
no mesmo job consumiu cinco páginas e persistiu 146 atualizações antes de a
continuidade rejeitar o watermark. O banco oficial não foi afetado.

A correção adicionou `SyncRepository.validate_window_candidate()` e moveu a
validação para antes de:

- criar a janela;
- adquirir o lease;
- iniciar `ingestion_run`;
- consumir o adaptador HTTP.

A validação final de `complete_window()` foi mantida para proteger a
transação contra concorrência.

Na nova cópia limpa, o teste real de lacuna preservou:

```text
linhas no log HTTP antes: 2
linhas no log HTTP depois: 2
sync_window: 1
ingestion_run do job: 1
raw_observation: 0
raw_asset: 1.391.174
```

## 8. Retry, retomada e testes

Não foi provocada falha artificial contra o serviço público do PNCP. Retry e
retomada foram validados deterministicamente pelos testes:

- respeito a `Retry-After`;
- contagem correta de tentativas;
- retomada de janela falha reiniciando endpoint paginado na página 1.

Resultado final:

```text
3 testes operacionais específicos: passed
suíte completa: 248 passed
git diff --check: sem erros
```

## 9. Integridade final da cópia limpa

Depois do piloto e do reprocessamento:

```text
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: zero linhas
execuções RUNNING: 0
janelas COMPLETED: 2
```

## 10. Decisão e pendências

**FASE 3 APROVADA EM CÓPIA ISOLADA.**

Pendências deliberadamente reservadas para a Fase 4:

1. interromper e reconfirmar todos os escritores;
2. produzir um backup imediatamente anterior à migração oficial;
3. aplicar `001` a `005` no banco oficial;
4. repetir integridade, FKs e contagens no banco oficial;
5. habilitar inicialmente apenas uma janela controlada;
6. configurar serviço, logs, monitoramento e retomada automática;
7. manter PCA, contratos, atas e relacionamentos fora do CLI online até sua
   ativação explícita por recurso.
