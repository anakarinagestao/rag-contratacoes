# PNCP/RJ — Fase 4: ativação incremental operacional

Data: 2026-10-19

Branch: `feature/ingestao-incremental-pncp`

Banco: `data/debug/pncp_contracts_2023_t1_all.db`

## 1. Resultado executivo

A Fase 4 foi aprovada para o escopo operacional inicial: contratações do
RJ, uma modalidade e uma janela de um dia. O banco oficial recebeu as
migrações `001` a `005`, preservou a integridade e processou uma janela real
da modalidade 6 com checkpoint v2, watermark, cobertura e observações RAW.

O CLI agora separa explicitamente três modos:

- `--offline`: fixture e banco em `/tmp`;
- `--online-pilot`: API real e banco em `/tmp`;
- `--online-operational`: API real, banco existente fora de `/tmp`, caminho
  confirmado duas vezes e log JSONL absoluto obrigatório.

PCA, contratos, atas e recursos dependentes continuam bloqueados no transporte
online. Não foi criado um agendador recorrente amplo: somente a recuperação
automática de uma janela controlada está preparada. Isso evita promover um
piloto de uma modalidade a uma cobertura estadual inexistente.

## 2. Preflight e backup

Antes da migração foram confirmados:

- nenhum coletor PNCP em execução;
- serviços histórico, resultados e piloto inativos;
- nenhum processo com o SQLite aberto;
- zero execuções `RUNNING`;
- `PRAGMA integrity_check = ok`;
- contagem RAW de `1.391.174` versões.

Backup pré-migração:

```text
data/debug/backups/pncp_contracts_2023_t1_all_pre_phase4_20261019.sqlite.gz
tamanho: 607.186.966 bytes
SHA-256 do arquivo compactado:
ccb50930baeb3413fee5ff8ffbd85fa6cacf59f2f395796f31d036613085da6c
```

O arquivo passou em `gzip -t`. Seu conteúdo descompactado reproduziu o hash do
banco anterior à migração:

```text
0aa81991aaeb19787d0bffe354636a7b34ff4997d903d1de8201e68245d87460
```

O backup permanece fora do Git.

## 3. Migração oficial

Foram aplicadas em ordem:

1. `001_raw_canonical_identity_index.sql`;
2. `002_sync_window_checkpoint.sql`;
3. `003_raw_observation.sql`;
4. `004_sync_reconciliation_target.sql`;
5. `005_sync_coverage_manifest.sql`.

Antes da primeira execução, as dez tabelas novas estavam vazias. As contagens
históricas permaneceram inalteradas e as validações retornaram:

```text
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: zero linhas
```

## 4. Travas do modo operacional

O modo operacional exige simultaneamente:

1. `--online-operational` explícito;
2. banco absoluto, existente e com extensão `.db` ou `.sqlite`;
3. repetição exata em `--confirm-operational-database`;
4. log absoluto terminado em `.jsonl`;
5. recurso `contratacao`;
6. UF `RJ`;
7. uma única data;
8. uma modalidade entre 1 e 14;
9. continuidade temporal validada antes de consumir a API.

Os modos offline e piloto continuam incapazes de escrever fora de `/tmp`.

## 5. Primeira janela operacional

Escopo:

```text
recurso: contratacao
UF: RJ
modalidade: 6
data: 2026-10-16
job: pncp_incremental_contratacao_rj_m06
```

Resultado:

| Métrica | Valor |
|---|---:|
| Páginas HTTP | 5 |
| Respostas HTTP 200 | 5 |
| Registros recebidos | 240 |
| `NEW` | 0 |
| `UPDATED` | 142 |
| `UNCHANGED` | 98 |
| Falhas | 0 |
| Observações RAW | 240 |
| Renovações de lease | 0 |

Identificadores:

```text
job_id: job-db75b3db834aaab8d29eb52d020d2fd0d6c05d6c9572a1f04b094143a6d92b0d
window_id: window-294aa1f6b44e802539360fd61459e962b30b91f71d5aaf6b1ef175a7f795e7b4
run_id: 5833a0ea-1921-4e1c-bdd5-2df1f25fcd16
watermark: 2026-10-16T23:59:59.999999
cursor final: página 5
```

A janela terminou `COMPLETED`, a execução terminou `SUCCESS` e não restou
nenhuma execução `RUNNING`.

## 6. Cobertura composta

Foi congelado o plano com os membros `modalidade:1` a `modalidade:14`. Nesta
janela:

- `modalidade:6`: `COMPLETED`;
- demais 13 modalidades: `PENDING`;
- janela composta: `PENDING`;
- watermark composto: ainda ausente.

Esse estado é intencional. O watermark estadual só poderá avançar quando
todos os membros da mesma janela estiverem completos.

## 7. Estado RAW depois da janela

| Recurso | Versões RAW |
|---|---:|
| `ata_registro_preco` | 80.761 |
| `contratacao` | 180.293 |
| `contratacao_item` | 557.211 |
| `contrato` | 151.898 |
| `empenho` | 1.900 |
| `pca` | 1.578 |
| `resultado_item` | 417.675 |
| **Total** | **1.391.316** |

O acréscimo de 142 versões corresponde exatamente aos registros classificados
como `UPDATED`. Registros `UNCHANGED` geraram observação, mas não nova versão.

Validação final:

```text
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: zero linhas
execuções RUNNING: 0
```

## 8. Logs e recuperação automática

Logs locais, ignorados pelo Git:

```text
logs/pncp_incremental_operational.jsonl
logs/pncp_incremental_operational.http.jsonl
```

O primeiro registra as 240 decisões de versionamento. O segundo registra cinco
requisições e cinco respostas HTTP, todas `200`.

Foram adicionados exemplos para uma unidade `systemd` oneshot:

```text
ops/systemd/pncp-incremental-operational.service.example
ops/systemd/pncp-incremental-operational.env.example
```

A unidade usa `Restart=on-failure`, espera 30 minutos e reinicia a mesma janela
com `--resume`. Ela deve ser iniciada manualmente para uma janela nova e não
deve ser habilitada no boot, porque uma janela já concluída rejeita `--resume`.

Monitoramento:

```bash
journalctl --user -u pncp-incremental-operational.service -f
```

## 9. Testes

Resultado depois da inclusão do modo operacional:

```text
testes direcionados: 49 passed
suíte completa: 253 passed
git diff --check: sem erros
```

O ambiente não possui `ruff` instalado; por isso a validação usada foi a
suíte completa, compilação/importação pelos testes e revisão do diff.

## 10. Decisão e próximo passo

**FASE 4 APROVADA PARA ATIVAÇÃO CONTROLADA DE CONTRATAÇÕES.**

A ativação recorrente completa ainda depende de:

1. homologar as modalidades restantes em janelas controladas;
2. criar um planejador que gere datas e membros sem reutilizar janela concluída;
3. habilitar separadamente os adaptadores online de PCA, contratos e atas;
4. validar em produção as reconciliações de itens, resultados e empenhos;
5. somente então instalar um timer recorrente.

Essas pendências não invalidam o banco migrado nem a primeira janela
operacional. Elas limitam corretamente o escopo já homologado.
