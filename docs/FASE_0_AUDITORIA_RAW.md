# PNCP/RJ — Fase 0: auditoria e encerramento seguro da coleta RAW

Data da auditoria e do encerramento: 2026-10-19
Branch auditada: `feature/ingestao-incremental-pncp`
Commit de referência: `3a2586dac3dd73b9223a7b40015a9212554f7ee4`
Banco operacional: `data/debug/pncp_contracts_2023_t1_all.db`

## 1. Resumo executivo

A camada RAW foi auditada e encerrada administrativamente para preparar a
normalização e a futura ativação incremental. O banco operacional passou por
verificação de integridade antes e depois do encerramento, sem corrupção e sem
violações de chave estrangeira.

O encerramento realizou somente estas mudanças controladas:

- interrupção do supervisor histórico `pncp-historical-queue.service`;
- criação de backup consistente antes da escrita administrativa;
- encerramento como `FAILED` de quatro tentativas parciais antigas que
  permaneciam em `RUNNING`;
- atualização do inventário congelado de órgãos/unidades do RJ;
- documentação das lacunas conhecidas e das restrições de cobertura.

Nenhuma coleta foi iniciada. Nenhuma requisição ao PNCP foi executada. Nenhuma
migração incremental foi aplicada. Nenhum payload RAW foi removido ou
sobrescrito.

## 2. Estado do repositório e ambiente

O repositório principal estava na branch
`feature/ingestao-incremental-pncp`, com alterações incrementais ainda não
commitadas. Também foram identificados:

- worktree `feature/coleta-atas-registro-precos`, com alterações locais;
- worktree de auditoria incremental em detached HEAD;
- arquivos de configuração, políticas e migrações incrementais ainda não
  consolidados no Git.

O banco operacional ainda utiliza o esquema legado com as tabelas:

- `raw_asset`;
- `ingestion_run`;
- `sync_checkpoint`;
- `collection_target_failure`.

As migrações incrementais `001` a `005` não foram aplicadas ao banco real.

## 3. Atividade de escrita e coletores

Antes do encerramento, não existia coletor Python ativo nem descritor ou lock
visível no banco. O serviço `pncp-historical-queue.service` estava ativo, porém
em espera porque a branch atual não era `feature/agregate-pncp-raw`.

O serviço foi interrompido explicitamente antes do backup e permaneceu
`inactive`. Uma nova inspeção não encontrou processo de coleta nem arquivo do
banco aberto por outro processo.

O supervisor não deve ser reativado nem a branch de coleta histórica deve ser
usada com esse banco sem uma decisão operacional explícita.

## 4. Execuções antigas em RUNNING

Quatro tentativas parciais estavam incorretamente em `RUNNING`:

| Run ID | Fonte | Registros RAW vinculados | Recuperação posterior |
|---|---|---:|---|
| `f35e2acb-5b45-47f6-afd6-f607c2caa977` | `pncp_contratos_rj_2024_t1` | 412 | Sim |
| `998997bd-eb4c-4a82-9c01-597c40f181c4` | `pncp_contratos_rj_2024_t1` | 195 | Sim |
| `47bad984-01e5-417e-8ff9-6792e50a15c2` | `pncp_contratos_rj_2024_t1` | 59 | Sim |
| `606dce7d-e824-4790-b147-5025ecce3209` | `pncp_contratos_rj_2024_t4` | 240 | Sim |

As quatro foram classificadas como tentativas parciais abandonadas. Uma
transação única alterou somente `status`, `finished_at` e `error_message`. O
estado final é `FAILED`, acompanhado da seguinte justificativa:

> Fase 0: tentativa parcial abandonada; encerramento administrativo em
> 2026-10-19 após confirmação de cobertura posterior bem-sucedida. Registros
> RAW preservados.

Os contadores originais foram preservados. A quantidade de execuções
`RUNNING` passou de 4 para 0 e `FAILED` passou de 87 para 91.

## 5. Execuções FAILED

Antes do encerramento administrativo, as 87 falhas foram classificadas assim:

| Grupo | Quantidade | Interpretação |
|---|---:|---|
| A — recuperada | 83 | Escopo recuperado por execução posterior, checkpoint e inventário |
| B — lacuna confirmada | 4 | Endpoint de empenho não observado com sucesso |
| C — inconclusiva | 0 | Nenhum caso restante |

As quatro lacunas conhecidas são alvos de empenho com sequenciais `1032`,
`1060`, `1070` e `1120`, todos com HTTP 500. Elas ficam registradas como
`known_gap`. Não se deve inferir que esses contratos não possuam empenhos.

A tabela `collection_target_failure` possui 20.296 ocorrências, todas
`RESOLVED`. Não havia falha pendente nessa fila no encerramento.

## 6. Integridade SQLite e backup

As verificações foram executadas com `mode=ro` e `PRAGMA query_only=ON`:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

Resultado antes e depois da correção:

- `integrity_check`: `ok`;
- violações de chave estrangeira: 0;
- total RAW final: 1.391.174;
- execuções `RUNNING`: 0;
- falhas pendentes em `collection_target_failure`: 0.

Backup anterior à correção:

`data/debug/backups/pncp_contracts_2023_t1_all_pre_fase0_closure_20261019.db`

O backup foi criado pela API de backup do SQLite e também passou no
`integrity_check` e no `foreign_key_check`.

Hashes SHA-256:

| Artefato | SHA-256 |
|---|---|
| Banco antes da correção | `cd0fc87c71400c0c6c5d09927b7e89ddcf98f139e7fb995b590d496ae4ccdb51` |
| Backup consistente | `f7324bf02473e7fffb3f26c56860cd24e514be17d3cd0ad0847bedf7e142b0b4` |
| Banco após a correção | `0aa81991aaeb19787d0bffe354636a7b34ff4997d903d1de8201e68245d87460` |

O hash físico da cópia não precisa coincidir com o arquivo de origem porque
a API de backup recompõe as páginas SQLite. A equivalência foi verificada por
integridade, esquema e contagens.

## 7. Inventário RAW final

| Recurso | Registros RAW |
|---|---:|
| Contratações | 180.151 |
| Itens de contratação | 557.211 |
| Resultados homologados | 417.675 |
| Contratos | 151.898 |
| Empenhos | 1.900 |
| PCAs | 1.578 |
| Atas de registro de preços | 80.761 |
| **Total** | **1.391.174** |

Não existem `external_id` vazios. Cada par `(source, external_id)` possui uma
única versão no banco atual e todos os `previous_version_id` estão vazios.
Portanto, este snapshot histórico não materializa cadeias de versão RAW.

## 8. Inventário de escopo do RJ

O arquivo `config/pncp_rj_scope_inventory.json` foi regenerado a partir das
contratações RAW em modo somente leitura.

| Campo | Antes | Depois |
|---|---:|---:|
| Pares órgão/unidade | 2.105 | 2.116 |
| Órgãos distintos | 613 | 617 |
| `inventory_id` | `inventory-094f916a...` | `inventory-fec7430e...` |

ID completo atual:

`inventory-fec7430eec6a854f91858dc1996a504bc9a50818f4de20dfa79b7b8d1ff2897f`

As modalidades esperadas continuam sendo 1 a 14. Foram observadas 1 e 3 a 14.
A modalidade 2 permanece no plano esperado, mas está registrada como sem
observação na RAW, e não como cobertura comprovada.

Validação por recurso:

| Recurso | Pares observados | Dentro do inventário | Fora do inventário |
|---|---:|---:|---:|
| Contratos | 1.335 | 1.335 | 0 |
| Atas | 164 | 164 | 0 |
| PCA | 839 | 292 | 547 |

Os 547 pares de PCA fora do inventário não foram incorporados automaticamente
porque o inventário representa o recorte comprovado pelas contratações do RJ.
PCAs podem retornar conteúdo mais amplo por CNPJ. Dos 1.578 PCAs, 567 ficaram
integralmente dentro do inventário e 1.011 possuem ao menos um par externo.

## 9. Cobertura temporal

Contratações, itens e contratos possuem evidência de cobertura trimestral de
2023T1 a 2026T3. O quarto trimestre de 2026 é parcial, encerrado em
2026-10-17.

PCA possui planos de 2023, 2024, 2025 e 2026. Por ser anual, o PCA não deve ser
interpretado como coleta trimestral.

Atas possuem publicações observadas entre 2023-06-09 e 2026-10-16, combinando
busca ampla e complementação direta por contratações SRP. Isso não prova
cobertura universal de todas as atas possíveis do RJ.

Empenhos permanecem com cobertura parcial, concentrada nos alvos recentes.

Foram observados 21 valores de ano suspeitos em `dataResultado`: 2013, 2020,
2021, 2022, 2027 e 2202. Eles devem ser tratados por regra de qualidade na
normalização, sem alterar o payload RAW.

## 10. Relacionamentos e dependentes

| Relação | Filhos | Pais localizados | Sem pai no recorte |
|---|---:|---:|---:|
| Contratação → item | 557.211 | 557.211 | 0 |
| Item → resultado | 417.675 | 417.675 | 0 |
| Contratação → contrato | 151.898 | 148.226 | 3.672 |
| Contrato → empenho | 1.900 | 1.900 | 0 |
| Contratação → ata | 80.761 | 80.761 | 0 |

Existem 180.142 contratações com itens; nove não possuem item RAW. Os
resultados pertencem a 400.552 itens distintos. Os contratos pertencem a
66.313 contratações distintas. As atas pertencem a 17.111 contratações.

Os 3.672 contratos sem contratação-pai na RAW não foram descartados. O pai
pode estar fora do recorte de publicação. A normalização deverá preservar
esses contratos e marcar a relação como não resolvida.

Os 1.578 PCAs contêm 411.544 itens internos.

## 11. Situação dos empenhos

Existem 1.900 empenhos pertencentes a 1.889 contratos. Em relação aos 151.898
contratos da RAW, a cobertura observada é de aproximadamente 1,24%. No lote
recente de 3.427 contratos, 55,12% possuem empenhos observados.

A normalização v1 pode carregar esses dados somente com estas regras:

- `coverage_status = PARTIAL`;
- ausência de empenho não significa contrato sem empenho;
- indicadores globais devem excluir inferências negativas;
- os quatro alvos HTTP 500 permanecem como lacunas conhecidas;
- origem e período do lote devem ser preservados.

## 12. Riscos remanescentes

| ID | Gravidade | Risco | Tratamento |
|---|---|---|---|
| F0-03 | Alta | Quatro alvos de empenho com HTTP 500 | `known_gap`; recuperar em coleta futura controlada |
| F0-04 | Alta | Cobertura parcial de empenhos | Sinalização obrigatória na normalizada |
| F0-05 | Média | 3.672 contratos sem pai no recorte | Preservar e registrar relação não resolvida |
| F0-07 | Alta | Snapshot legado sem cadeias de versão | Não assumir histórico de versões |
| F0-08 | Média | Datas anômalas em resultados | Regra de qualidade, sem alterar RAW |
| F0-09 | Média | Worktrees ainda dirty | Revisar e organizar antes de commits |
| F0-10 | Média | Atas com cobertura direcionada | Documentar escopo e não alegar universalidade |

Os riscos F0-01, F0-02 e F0-06 foram resolvidos nesta etapa: supervisor
parado, execuções antigas encerradas e inventário atualizado.

## 13. Critérios de aceite

| Critério | Estado final |
|---|---|
| Ausência de escrita concorrente no encerramento | APROVADO |
| Quatro execuções antigas investigadas e encerradas | APROVADO |
| Execuções `FAILED` classificadas | APROVADO |
| Integridade SQLite verificada | APROVADO |
| Inventário RAW atualizado | APROVADO |
| Inventário de escopo congelado | APROVADO |
| Cobertura temporal documentada | APROVADO |
| Relacionamentos principais avaliados | APROVADO |
| Cobertura de empenhos documentada | APROVADO COM RESSALVA |
| Lacunas pendentes identificadas | APROVADO |
| Backup consistente e rollback disponível | APROVADO |

## 14. Condições para a próxima fase

A RAW pode servir de entrada imutável para o planejamento e para testes da
camada normalizada. A próxima fase deve:

1. operar inicialmente em outro banco ou em cópia isolada;
2. nunca criar tabelas normalizadas no snapshot RAW sem backup;
3. preservar `source_raw_asset_id` e a proveniência;
4. materializar exceções de relacionamento e cobertura;
5. tratar empenhos como parciais;
6. validar datas sem corrigir o JSON original;
7. aplicar as migrações incrementais primeiro em cópia;
8. manter o supervisor histórico desligado durante migrações e snapshots.

## 15. Decisão final

**FASE 0 APROVADA E ENCERRADA COM RESSALVAS DOCUMENTADAS.**

A camada RAW está íntegra, sem execuções `RUNNING`, sem fila de falhas
pendentes e com inventário de escopo atualizado. As lacunas de empenhos, atas,
contratos sem pai no recorte e datas anômalas não impedem o início controlado
da Fase 1, desde que sejam representadas explicitamente e não sejam usadas
para inferir ausência de dados no PNCP.
