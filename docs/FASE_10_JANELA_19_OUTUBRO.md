# PNCP/RJ — Fase 10: janela incremental de 19 de outubro

Data: 2026-10-20

Branch: `feature/ingestao-incremental-pncp`

Banco operacional: `data/debug/pncp_contracts_2023_t1_all.db`

## 1. Resultado executivo

A janela integralmente fechada de 19/10/2026 foi executada depois da
conclusão e auditoria das janelas de 16 a 18/10. As modalidades 1 a 14
terminaram com `14/14 COMPLETED`, sem falhas, e o watermark composto avançou
para `2026-10-19T23:59:59.999999`.

Foram recebidos 661 registros: 267 entidades novas e 394 novas versões de
entidades existentes.

## 2. Proteções antes da execução

- cinco serviços de coleta confirmados como inativos;
- `PRAGMA quick_check = ok`;
- zero violações em `PRAGMA foreign_key_check`;
- zero `ingestion_run` em `RUNNING`;
- watermark anterior em `2026-10-18T23:59:59.999999`;
- plano somente leitura com `ready: true` e `14 RUN`;
- backup validado da Fase 9 mantido como ponto de recuperação.

## 3. Resultado por modalidade

| Modalidade | Recebidos | `NEW` | `UPDATED` | `UNCHANGED` | Falhas |
|---:|---:|---:|---:|---:|---:|
| 1 | 0 | 0 | 0 | 0 | 0 |
| 2 | 0 | 0 | 0 | 0 | 0 |
| 3 | 0 | 0 | 0 | 0 | 0 |
| 4 | 17 | 4 | 13 | 0 | 0 |
| 5 | 0 | 0 | 0 | 0 | 0 |
| 6 | 277 | 57 | 220 | 0 | 0 |
| 7 | 2 | 0 | 2 | 0 | 0 |
| 8 | 275 | 136 | 139 | 0 | 0 |
| 9 | 82 | 65 | 17 | 0 | 0 |
| 10 | 0 | 0 | 0 | 0 | 0 |
| 11 | 0 | 0 | 0 | 0 | 0 |
| 12 | 7 | 4 | 3 | 0 | 0 |
| 13 | 0 | 0 | 0 | 0 | 0 |
| 14 | 1 | 1 | 0 | 0 | 0 |
| **Total** | **661** | **267** | **394** | **0** | **0** |

## 4. Transporte HTTP

```text
requisições: 25
respostas: 25
HTTP 200: 18
HTTP 204: 7
retries: 0
falhas: 0
```

Não houve `404`, `422`, `429`, timeout ou retomada. As respostas `204` foram
tratadas como consultas válidas sem conteúdo.

## 5. Estado da RAW

| Métrica | Antes | Depois | Diferença |
|---|---:|---:|---:|
| `raw_asset` total | 1.391.410 | 1.392.071 | +661 |
| `contratacao` | 180.387 | 181.048 | +661 |
| `raw_observation` | 606 | 1.267 | +661 |

As 394 linhas atualizadas possuem `previous_version_id` válido. A auditoria
não encontrou nenhum elo apontando para uma versão inexistente.

## 6. Watermark e idempotência

```text
coverage_window_id:
coverage-window-216889ac8ffa601a66e48b81b752b3e6221e66e4dc8a5501c2e6d59e37f3b627

status: COMPLETED
membros: 14/14 COMPLETED
watermark: 2026-10-19T23:59:59.999999
```

Depois da conclusão, uma nova simulação retornou `14 SKIP`. Portanto, uma
execução comum para a mesma data não volta a consultar a API.

## 7. Integridade final

```text
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: zero linhas
ingestion_run RUNNING: 0
suíte automatizada: 263 passed
tamanho: 4.633.493.504 bytes
SHA-256:
ca23b2a31a5ae6e2fa73aa3874c3f9a5d0051b6d98f435084b2176dc5ce286a0
```

Banco e logs continuam ignorados pelo Git.

## 8. Decisão

**JANELA DE 19/10 APROVADA NO BANCO OPERACIONAL.**

Na data desta execução, 19/10 é o último dia integralmente fechado. O
incremental de contratações está atualizado até esse limite, sem lacunas de
cobertura entre 16 e 19/10.
