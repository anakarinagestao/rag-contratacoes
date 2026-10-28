# PNCP/RJ — Fase 6: planejador de cobertura operacional

Data: 2026-10-19

Branch: `feature/ingestao-incremental-pncp`

## 1. Objetivo

Orquestrar as modalidades 1 a 14 de contratação sem repetir shards já
concluídos, sem retomar uma janela ativa e sem avançar o watermark composto
antes de todos os membros terminarem.

O planejador é somente leitura por padrão. A execução exige opção explícita,
confirmação redundante do banco e log operacional absoluto.

## 2. Componentes

```text
src/ingestion/coverage_orchestrator.py
src/fetch_incremental_coverage.py
ops/systemd/pncp-incremental-coverage.env.example
ops/systemd/pncp-incremental-coverage.service.example
```

O orquestrador calcula deterministicamente o `job_id` e o `window_id` de cada
modalidade usando a mesma configuração do runner. Ele não depende de nomes de
arquivos, texto dos logs ou ordem de inserção no SQLite.

## 3. Estados e ações

| Estado encontrado | Ação |
|---|---|
| membro nunca iniciado | `RUN` |
| janela `FAILED` | `RESUME` |
| membro `COMPLETED` com job e janela esperados | `SKIP` |
| janela `RUNNING` | `BLOCK` |
| janela concluída sem confirmar o membro | `BLOCK` |
| vínculo com outro job ou janela | `BLOCK` |
| qualquer `ingestion_run` global em `RUNNING` | `BLOCK` |

Uma janela falha ainda não vinculada ao manifesto também é reconhecida e
retomada. Uma janela concluída fora da cobertura não é anexada
retroativamente: ela bloqueia e exige auditoria.

## 4. Proteções de execução

- simulação somente leitura como comportamento padrão;
- `--execute` obrigatório para escrita;
- repetição exata do banco em `--confirm-operational-database`;
- banco operacional proibido dentro de `/tmp`;
- log absoluto com extensão `.jsonl`;
- intervalo mínimo de 30 segundos entre shards;
- parada imediata no primeiro erro;
- nova execução recalcula `RUN`, `RESUME`, `SKIP` e `BLOCK`;
- cobertura sempre congelada com modalidades 1 a 14.

## 5. Simulação no banco oficial

Comando executado sem escrita:

```bash
python3 -m src.fetch_incremental_coverage \
  --date 2026-10-16 \
  --db-path "/caminho/absoluto/data/debug/pncp_contracts_2023_t1_all.db"
```

Resultado:

```text
ready: true
RUN: 13
RESUME: 0
SKIP: 1
BLOCK: 0
SKIP: modalidade 6
RUN: modalidades 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13 e 14
```

A simulação não criou jobs, janelas, execuções ou registros RAW.

## 6. Execução supervisionada

O comando preparado, ainda não executado no banco oficial, é:

```bash
python3 -u -m src.fetch_incremental_coverage \
  --date 2026-10-16 \
  --db-path "/caminho/absoluto/data/debug/pncp_contracts_2023_t1_all.db" \
  --confirm-operational-database "/caminho/absoluto/data/debug/pncp_contracts_2023_t1_all.db" \
  --log-path "/caminho/absoluto/logs/pncp_incremental_operational.jsonl" \
  --inter-shard-seconds 30 \
  --execute
```

Se houver falha, o mesmo comando deve ser executado novamente. Os membros
concluídos serão ignorados e o membro falho receberá `--resume` internamente.

## 7. Recuperação com systemd

A unidade de exemplo executa o lote como `oneshot`. Em caso de falha, usa
`Restart=on-failure` e aguarda 30 minutos. Ao reiniciar, o planejador consulta
o SQLite e retoma somente o necessário.

Ela não possui timer e não deve ser habilitada no boot. A data fica congelada
no arquivo de ambiente para impedir mudança acidental do recorte durante uma
recuperação.

## 8. Testes

```text
testes direcionados: 58 passed
suíte completa: 262 passed
git diff --check: sem erros
systemd-analyze verify: sem erro da unidade do projeto
```

## 9. Decisão

**PLANEJADOR APROVADO.**

A cobertura de `2026-10-16` foi posteriormente autorizada e concluída. Os
resultados estão registrados em
`FASE_7_COBERTURA_OFICIAL_CONTRATACOES.md`.
