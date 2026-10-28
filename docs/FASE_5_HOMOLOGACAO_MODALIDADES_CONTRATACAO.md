# PNCP/RJ — Fase 5: homologação das modalidades de contratação

Data: 2026-10-19

Branch: `feature/ingestao-incremental-pncp`

Escopo: piloto online em banco descartável dentro de `/tmp`

## 1. Objetivo

Validar as modalidades 1 a 14 no endpoint incremental de contratações antes
de ampliar a cobertura do banco oficial. Cada modalidade recebeu job,
checkpoint e watermark independentes para a data `2026-10-16` e UF `RJ`.

Nenhum dado desse piloto foi escrito no banco oficial.

## 2. Resultado por modalidade

| Modalidade | Registros | Resultado |
|---:|---:|---|
| 1 | 0 | `COMPLETED`, HTTP 204 |
| 2 | 0 | `COMPLETED`, HTTP 204 |
| 3 | 0 | `COMPLETED`, HTTP 204 |
| 4 | 16 | `COMPLETED` |
| 5 | 1 | `COMPLETED` |
| 6 | 238 | `COMPLETED` apó `--resume` |
| 7 | 3 | `COMPLETED` |
| 8 | 235 | `COMPLETED` |
| 9 | 90 | `COMPLETED` |
| 10 | 0 | `COMPLETED`, HTTP 204 |
| 11 | 0 | `COMPLETED`, HTTP 204 |
| 12 | 10 | `COMPLETED` |
| 13 | 0 | `COMPLETED`, HTTP 204 |
| 14 | 0 | `COMPLETED`, HTTP 204 |
| **Total** | **593** | **14/14 concluídas** |

## 3. Recuperação real de HTTP 429

Depois de chamadas consecutivas das modalidades 1 a 5, a modalidade 6 recebeu
três respostas HTTP `429` na página 2. O PNCP não enviou `Retry-After`; os
retries locais curtos se esgotaram e a execução terminou `FAILED`.

Apó uma espera de 60 segundos, a mesma janela foi executada com `--resume`:

```text
records_seen: 238
NEW: 188
UNCHANGED: 50
status: SUCCEEDED
resumed: true
```

As 50 observações da primeira página foram reconhecidas como `UNCHANGED`.
Nenhuma versão foi duplicada e o watermark só avançou depois da conclusão.

Decisão operacional: execuções sequenciais de shards devem respeitar no
mínimo 30 segundos entre modalidades. Em caso de `429` sem `Retry-After`, a
unidade de recuperação aguarda 30 minutos antes de chamar `--resume`.

## 4. Auditoria do banco temporário

```text
sync_job: 14
sync_window COMPLETED: 14
sync_window FAILED/RUNNING: 0
sync_watermark: 14
raw_asset: 593
raw_observation: 593
identidades com raiz inconsistente: 0
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: zero linhas
```

O log HTTP registrou:

```text
requisições: 27
respostas: 27
HTTP 200: 17
HTTP 204: 7
HTTP 429: 3
```

O log de versionamento possui 643 eventos porque também registra as 50
decisões emitidas na tentativa que falhou. A transação do banco preservou
somente as 593 observações finais válidas.

## 5. Decisão

**MODALIDADES 1 A 14 HOMOLOGADAS NO PILOTO ONLINE.**

A homologação prova os formatos `200` e `204`, a paginação, o isolamento por
job e a retomada depois de `429`. Ela não autoriza executar as 13 modalidades
pendentes no banco oficial sem um plano controlado de ordem, intervalo e
monitoramento.

Próximos passos:

1. implementar um planejador operacional que materialize uma modalidade por
   vez e aplique o intervalo entre shards;
2. manter a janela diária e a sobreposição de dois dias;
3. executar a primeira cobertura composta oficial de forma supervisionada;
4. somente depois instalar um timer recorrente.
