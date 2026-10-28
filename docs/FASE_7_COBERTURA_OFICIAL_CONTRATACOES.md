# PNCP/RJ — Fase 7: cobertura oficial de contratações

Data: 2026-10-19

Branch: `feature/ingestao-incremental-pncp`

Janela: `2026-10-16`

## 1. Resultado executivo

A primeira cobertura incremental composta de contratações foi concluída no
banco oficial. As modalidades 1 a 14 estão `COMPLETED`, cada uma possui seu
watermark individual e o watermark composto avançou somente depois da
confirmação do último membro.

Não houve falha, retry, `429`, retomada ou execução presa. O planejador passou
de `13 RUN + 1 SKIP` para `14 SKIP` depois da conclusão.

## 2. Preflight

Antes da escrita foram confirmados:

- branch limpa;
- cinco serviços de coleta inativos;
- nenhum coletor PNCP ativo;
- nenhum processo com o banco aberto;
- WAL com zero bytes;
- 23 GB livres;
- zero `ingestion_run` em `RUNNING`;
- zero violações em `PRAGMA foreign_key_check`;
- `PRAGMA integrity_check = ok`;
- cobertura em `1 COMPLETED + 13 PENDING`.

## 3. Backup pré-cobertura

```text
arquivo:
data/debug/backups/pncp_contracts_2023_t1_all_pre_phase6_coverage_20261019.sqlite.gz

tamanho: 506.481.840 bytes
SHA-256 do arquivo compactado:
78b3dc9e5bd2a6546c66e406998666ec6a4baefb80b1d48bf9cb3cbad4a5530e

SHA-256 do banco e do conteúdo descompactado:
4eb41d5195368da995cd2e5fb2e73156e4c266021af274ba454f2a49e9433e00
```

O backup foi gravado primeiro com extensão temporária. O nome definitivo foi
atribuído somente depois de `gzip -t` e da comparação integral dos hashes.

## 4. Execução supervisionada

O orquestrador:

1. ignorou a modalidade 6, já concluída na Fase 4;
2. executou as outras 13 modalidades em ordem;
3. aguardou 30 segundos entre cada shard;
4. interromperia o lote no primeiro erro;
5. confirmou cada membro individualmente;
6. confirmou a cobertura composta somente na modalidade 14.

Tempo aproximado do lote: 6 minutos e 30 segundos.

## 5. Resultado por modalidade

| Modalidade | Recebidos | `NEW` | `UPDATED` | `UNCHANGED` | Status |
|---:|---:|---:|---:|---:|---|
| 1 | 0 | 0 | 0 | 0 | `SUCCESS` |
| 2 | 0 | 0 | 0 | 0 | `SUCCESS` |
| 3 | 0 | 0 | 0 | 0 | `SUCCESS` |
| 4 | 16 | 0 | 8 | 8 | `SUCCESS` |
| 5 | 1 | 0 | 1 | 0 | `SUCCESS` |
| 6 | 240 | 0 | 142 | 98 | `SUCCESS`, Fase 4 |
| 7 | 3 | 0 | 2 | 1 | `SUCCESS` |
| 8 | 235 | 0 | 60 | 175 | `SUCCESS` |
| 9 | 90 | 0 | 14 | 76 | `SUCCESS` |
| 10 | 0 | 0 | 0 | 0 | `SUCCESS` |
| 11 | 0 | 0 | 0 | 0 | `SUCCESS` |
| 12 | 10 | 0 | 6 | 4 | `SUCCESS` |
| 13 | 0 | 0 | 0 | 0 | `SUCCESS` |
| 14 | 0 | 0 | 0 | 0 | `SUCCESS` |
| **Total** | **595** | **0** | **233** | **362** | **14/14** |

O lote desta fase, sem contar a modalidade 6 executada anteriormente,
processou 355 observações: 91 atualizações e 264 registros inalterados.

## 6. HTTP

Cobertura inteira, incluindo a modalidade 6 da Fase 4:

```text
requisições: 23
respostas: 23
HTTP 200: 16
HTTP 204: 7
erros: 0
```

Somente o lote desta fase:

```text
respostas: 18
HTTP 200: 11
HTTP 204: 7
HTTP 429: 0
```

## 7. Watermarks e idempotência operacional

```text
coverage_plan_id:
coverage-9d9e35d30a589dfcc489958cf6699a9ca90b331a560637b9507c4e2039770d58

coverage_window_id:
coverage-window-8a9bc010c1065de51fff54c18f9c371d80ce4c730b4626607ece271113e85813

status: COMPLETED
watermark: 2026-10-16T23:59:59.999999
```

Depois da execução, uma nova simulação retornou:

```text
ready: true
SKIP: 14
RUN: 0
RESUME: 0
BLOCK: 0
```

Portanto, repetir o comando do planejador sem mudar a data não consulta a API
nem cria novas observações.

## 8. Estado final da RAW

| Métrica | Antes | Depois | Diferença |
|---|---:|---:|---:|
| `raw_asset` total | 1.391.316 | 1.391.407 | +91 |
| `contratacao` | 180.293 | 180.384 | +91 |
| observações da cobertura | 240 | 595 | +355 |

Os 91 acréscimos representam novas versões de entidades existentes. Nenhuma
entidade foi classificada como `NEW` porque o histórico já continha as chaves.

Banco final:

```text
tamanho: 4.629.544.960 bytes
SHA-256:
5ad4b05d066de2585263db09c1d495f5903913583c33a802443c6739c1a5e551
```

## 9. Validação final

```text
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: zero linhas
ingestion_run RUNNING: 0
sync_coverage_member COMPLETED: 14
sync_coverage_window COMPLETED: 1
records_failed: 0
```

Banco, backup e logs permanecem ignorados pelo Git.

## 10. Decisão

**PRIMEIRA COBERTURA OFICIAL DE CONTRATAÇÕES APROVADA.**

O planejamento seguinte foi concluído em modo somente leitura e está descrito
em `FASE_8_PLANEJAMENTO_JANELAS_SEGUINTES.md`.
