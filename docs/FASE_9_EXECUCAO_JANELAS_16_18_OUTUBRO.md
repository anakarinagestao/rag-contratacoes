# PNCP/RJ — Fase 9: execução das janelas de 16 a 18 de outubro

Data: 2026-10-20

Branch: `feature/ingestao-incremental-pncp`

Banco operacional: `data/debug/pncp_contracts_2023_t1_all.db`

## 1. Resultado executivo

O plano aprovado na Fase 8 foi executado integralmente e na ordem prevista:

1. reprocessamento controlado de 16/10/2026;
2. primeira execução de 17/10/2026;
3. primeira execução de 18/10/2026.

Cada data cobriu as modalidades 1 a 14. As três coberturas terminaram com
`14/14 COMPLETED`, nenhum registro falhou e o watermark composto avançou
somente depois da conclusão de todos os membros de cada dia.

## 2. Preflight

Antes da primeira escrita foram confirmados:

- branch correta e árvore de trabalho limpa;
- serviços de coleta inativos;
- nenhum processo usando o banco;
- WAL com zero bytes;
- aproximadamente 22 GB livres;
- zero `ingestion_run` em `RUNNING`;
- `PRAGMA quick_check = ok`;
- zero violações em `PRAGMA foreign_key_check`;
- watermark composto em `2026-10-16T23:59:59.999999`.

## 3. Backup pré-execução

```text
arquivo:
data/debug/backups/pncp_contracts_2023_t1_all_pre_phase8_windows_20261020.sqlite.gz

tamanho: 506.631.950 bytes
SHA-256 do arquivo compactado:
b43c0dca14bedfdac1eee7cffd9a3b30d18ec8c0c3bc5151d15d74af80f8e647

SHA-256 do banco e do conteúdo descompactado:
5ad4b05d066de2585263db09c1d495f5903913583c33a802443c6739c1a5e551
```

O backup foi escrito primeiro como arquivo parcial. O nome definitivo foi
atribuído apenas depois de `gzip -t` e da comparação integral dos hashes do
banco original e do conteúdo descompactado.

## 4. Resultado das janelas

| Data | Ação | Recebidos | `NEW` | `UPDATED` | `UNCHANGED` | Falhas | Cobertura |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-10-16 | `REPROCESS` | 592 | 0 | 0 | 592 | 0 | 14/14 |
| 2026-10-17 | `RUN` | 8 | 0 | 0 | 8 | 0 | 14/14 |
| 2026-10-18 | `RUN` | 3 | 1 | 2 | 0 | 0 | 14/14 |
| **Total** |  | **603** | **1** | **2** | **600** | **0** | **42/42** |

O reprocessamento de 16/10 recebeu 592 registros, enquanto a primeira
execução daquele dia havia recebido 595. A ausência posterior de três itens
na resposta do PNCP não removeu nem substituiu dados históricos.

## 5. Versionamento observado em 18/10

A janela de 18/10 comprovou o fluxo com alterações reais:

- uma contratação nova foi persistida sem `previous_version_id`;
- duas contratações alteradas geraram novas linhas RAW;
- cada linha `UPDATED` aponta para a versão anterior correta;
- os hashes das versões novas e anteriores são diferentes;
- nenhum payload histórico foi sobrescrito.

Identificadores PNCP observados:

```text
NEW:
00394445000101-1-000263/2026

UPDATED:
06170517000105-1-000107/2026
42498733000148-1-002102/2026
```

## 6. Observações e idempotência

| Marco | `raw_asset` | `contratacao` | `raw_observation` |
|---|---:|---:|---:|
| Antes da Fase 9 | 1.391.407 | 180.384 | 595 |
| Após 16/10 reprocessado | 1.391.407 | 180.384 | 595 |
| Após 17/10 | 1.391.407 | 180.384 | 603 |
| Após 18/10 | 1.391.410 | 180.387 | 606 |

O reprocessamento da mesma janela não duplicou versões nem observações.
Uma data nova registrou novas observações mesmo quando o conteúdo já era
conhecido. Em 18/10, somente as três mudanças reais aumentaram a RAW.

Uma nova simulação do planejador retornou:

```text
2026-10-16: 14 SKIP
2026-10-17: 14 SKIP
2026-10-18: 14 SKIP
```

## 7. Transporte HTTP

| Data | Requisições | Respostas | HTTP 200 | HTTP 204 | Retries | Falhas |
|---|---:|---:|---:|---:|---:|---:|
| 2026-10-16 | 41 | 41 | 27 | 14 | 0 | 0 |
| 2026-10-17 | 14 | 14 | 2 | 12 | 0 | 0 |
| 2026-10-18 | 14 | 14 | 2 | 12 | 0 | 0 |
| **Total** | **69** | **69** | **31** | **38** | **0** | **0** |

As respostas `204` foram tratadas como consultas válidas sem conteúdo. Não
houve `404`, `422`, `429`, timeout ou necessidade de retomada.

## 8. Watermark e cobertura

```text
watermark composto final: 2026-10-18T23:59:59.999999
coberturas COMPLETED: 3
membros COMPLETED: 42
membros FAILED: 0
ingestion_run RUNNING: 0
```

## 9. Integridade final

```text
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: zero linhas
suíte automatizada: 263 passed
tamanho: 4.629.639.168 bytes
SHA-256:
5b06396f2a051bf6cf5ab106f2539416badca85a38a61d08998be0a560ec4e43
```

Banco, backup e logs continuam ignorados pelo Git.

## 10. Decisão

**JANELAS DE 16 A 18/10 APROVADAS NO BANCO OPERACIONAL.**

O incremental demonstrou cobertura composta, sobreposição idempotente,
registro separado de observações, criação de entidade nova e encadeamento de
versões atualizadas com dados reais do PNCP.

O próximo dia integralmente fechado é 19/10/2026. Ele deve ser primeiro
planejado em modo somente leitura e, depois, executado pela mesma cobertura
operacional.

A simulação somente leitura de 19/10 foi realizada no encerramento desta
fase e retornou:

```text
ready: true
RUN: 14
BLOCK: 0
```

Nenhuma chamada à API ou escrita no banco ocorreu nessa simulação.

A janela de 19/10 foi executada e aprovada posteriormente. As evidências
estão em `FASE_10_JANELA_19_OUTUBRO.md`.
