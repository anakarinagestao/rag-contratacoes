# PNCP/RJ — Normalização Fase 3: base oficial local

Data: 2026-10-20

Branch: `feature/ingestao-incremental-pncp`

## 1. Resultado executivo

A RAW cumulativa atualizada até o fim de 19/10/2026 foi transformada em uma
base normalizada oficial e independente:

`data/normalized/pncp_rj_normalized.db`

A RAW foi aberta em modo somente leitura. O build ocorreu primeiro em `/tmp`
e o arquivo só foi promovido depois da reconciliação, do
`PRAGMA integrity_check` e do `PRAGMA foreign_key_check`.

## 2. Proteção da entrada

RAW de origem:

```text
data/debug/pncp_contracts_2023_t1_all.db
tamanho: 4.633.493.504 bytes
SHA-256:
ca23b2a31a5ae6e2fa73aa3874c3f9a5d0051b6d98f435084b2176dc5ce286a0
```

Backup criado antes da normalização:

```text
data/debug/backups/
pncp_contracts_2023_t1_all_pre_normalized_official_20261020.sqlite.gz

tamanho: 507.230.257 bytes
SHA-256 compactado:
3694a2d6449c6c2757d30dd7899d1f29c2f8f20ea9008f57c249e6d2e78c5989
```

O conteúdo descompactado reproduziu exatamente o SHA-256 da RAW.

## 3. Execução inicial

```text
run_id: 24047d56-e51c-499e-aa1d-0921a46cebdc
transform_version: normalized-v1
status: SUCCESS
versões RAW vigentes examinadas: 1.391.442
entidades normalizadas: 1.664.401
fora do escopo: 1.011
rejeitadas: 0
problemas auditáveis: 1.083
```

A RAW possui 1.392.071 linhas de versão. O seletor escolheu 1.391.442
versões correntes e preservou as 629 versões anteriores exclusivamente na
camada RAW.

## 4. Contagens oficiais

| Tabela | Registros |
|---|---:|
| `orgao` | 618 |
| `unidade` | 2.117 |
| `fornecedor` | 42.766 |
| `contratacao` | 180.419 |
| `contratacao_item` | 557.211 |
| `resultado_item` | 417.675 |
| `contrato` | 151.898 |
| `empenho` | 1.900 |
| `ata_registro_preco` | 80.761 |
| `pca` | 567 |
| `pca_item` | 228.469 |
| **Entidades** | **1.664.401** |

`pca_contratacao_link` permanece vazia. Nenhuma associação entre item
planejado e contratação foi presumida sem uma regra de negócio aprovada.

## 5. Linhagem e qualidade

```text
linhagens totais: 2.853.103
linhagens correntes: 1.664.401
entidades com linhagem corrente duplicada: 0
OUT_OF_SCOPE: 1.011 PCAs
SUSPECT_DATE: 72 resultados
rejeições: 0
```

Relacionamentos de contratos:

```text
compra-pai resolvida: 148.226
compra-pai fora do recorte: 3.672
fornecedor resolvido: 151.894
fornecedor sem identificador: 4
```

## 6. Cobertura declarada

- contratações, itens, resultados e contratos: `COMPLETE` em relação ao
  snapshot RAW informado;
- PCA: `COMPLETE` em relação ao snapshot e ao inventário RJ;
- atas: `PARTIAL`, pois a coleta foi híbrida e direcionada;
- empenhos: `PARTIAL`, com quatro lacunas conhecidas;
- ausência de ata ou empenho não prova inexistência no PNCP.

## 7. Reconciliação financeira

| Recurso | Total |
|---|---:|
| Resultados homologados | R$ 533.771.492.800,03 |
| Valor global dos contratos | R$ 787.533.670.110,70 |
| Empenhos observados | R$ 131.953.649,08 |
| PCA no escopo RJ | R$ 191.389.328.133,31 |

Os totais são idênticos aos valores homologados no piloto integral, porque
as janelas incrementais seguintes alteraram apenas contratações.

## 8. Privacidade e publicação

A base possui 4.877 fornecedores marcados com
`identificador_pessoal = 1`. O NI é mantido apenas para integridade relacional
e não pode ser exposto em chunks, embeddings, prompts ou respostas RAG.

Quatro contratos de fornecedor estrangeiro permanecem com
`supplier_resolution_status = MISSING_IDENTIFIER`, conforme o payload da
fonte.

**A release pública da normalizada está bloqueada** até existir uma política
de acesso/mascaramento ou um artefato sanitizado. Banco e backup local estão
protegidos por `data/normalized/` no `.gitignore`.

## 9. Banco e backup normalizados

Banco oficial:

```text
tamanho: 2.196.258.816 bytes
SHA-256:
2392c086ac681534e0f2d792ae51785ffb4adae189175be3a45aaac99bd4ffdd
```

Backup:

```text
data/normalized/backups/pncp_rj_normalized_20261020.sqlite.gz
tamanho: 475.552.035 bytes
SHA-256 compactado:
f29fcd0b8b8e6e189d29da188c8c92d4e339885c95ffb16ce7d4dd8af41f29e6
```

O conteúdo descompactado possui o mesmo SHA-256 do banco oficial.

## 10. Teste incremental idempotente

O modo `update` foi executado sobre uma cópia descartável do banco oficial,
usando a mesma RAW:

```text
inserted: 0
updated: 0
unchanged: 1.664.401
skipped: 1.011
rejected: 0
```

A cópia pós-merge apresentou `quick_check = ok`, zero violações de FK,
1.664.401 linhagens correntes e nenhuma duplicidade. O arquivo temporário foi
removido depois da validação; banco e backup oficiais permaneceram intactos.

A suíte completa do projeto permaneceu aprovada: `263 passed` nessa fase.

## 11. Decisão

**BASE NORMALIZADA OFICIAL LOCAL APROVADA.**

A estratégia de sanitização foi implementada e validada depois desta
promoção. A política, as evidências e os limites de publicação estão em
`docs/FASE_4_EXPORTACAO_NORMALIZADA_SANITIZADA.md`.
