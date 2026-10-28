# Ingestão incremental PNCP — offline e piloto online controlado

## Situação atual

`src/fetch_corpus.py` continua sendo o orquestrador histórico e mantém o
checkpoint legado `sync_checkpoint`. Seu comportamento operacional não foi
substituído.

`src/fetch_incremental.py` é um ponto de entrada separado. Nesta etapa ele:

- exige a escolha explícita entre `--offline` e `--online-pilot`;
- no modo offline, exige uma fixture JSON local;
- no modo online, aceita somente `contratacao`, UF RJ, uma modalidade entre
  1 e 14 e uma janela de exatamente um dia;
- aceita somente banco SQLite em `/tmp`;
- utiliza exclusivamente os checkpoints v2;
- reutiliza a persistência RAW, a identidade canônica e o versionamento já
  existentes;
- nunca usa um banco padrão implícito.

A coleta real permanece limitada a um piloto de contratações. PCA, contratos,
atas e recursos dependentes possuem adaptadores validados, mas ainda não são
selecionáveis pelo CLI online.

O transporte comum e os adaptadores de endpoint já possuem respostas tipadas
e preservam a diferença entre 200, 204 e 404 contextual. Contratações,
contratos, atas e o fluxo de descoberta + plano integral do PCA são exercitados
por transporte simulado e por um piloto real isolado em `/tmp`.

`src/ingestion/adapter_runner.py` integra esses adaptadores aos checkpoints v2,
à persistência RAW e ao watermark. Esse runner não cria cliente HTTP e recusa
bancos e logs fora de `/tmp`. A rota online cria o cliente somente depois de
validar todas as travas do piloto.

## Execução segura

Exemplo funcional com a fixture sintética do repositório:

```bash
python3 -m src.fetch_incremental \
  --resource contratacao \
  --uf RJ \
  --start-date 2023-04-01 \
  --end-date 2023-04-30 \
  --modality 6 \
  --offline \
  --fixture tests/fixtures/incremental/contratacao_offline.json \
  --db-path /tmp/pncp_incremental_offline.db
```

Também é possível chamar o arquivo diretamente a partir da raiz:

```bash
python3 src/fetch_incremental.py --help
```

Não existe banco padrão. Caminhos relativos ou fora de `/tmp` são recusados.

Piloto online real, limitado a um dia e uma modalidade:

```bash
python3 -m src.fetch_incremental \
  --resource contratacao \
  --uf RJ \
  --start-date 2023-04-01 \
  --end-date 2023-04-01 \
  --modality 6 \
  --online-pilot \
  --db-path /tmp/pncp_incremental_pilot.db
```

O banco precisa ser novo ou uma cópia que tenha recebido as migrações
`001`–`005`. Requisições e ingestão geram logs JSONL ao lado do banco.

## Retomada e reprocessamento

Uma janela que falhou ou perdeu o lease exige retomada explícita:

```bash
python3 -m src.fetch_incremental \
  --resource contratacao \
  --uf RJ \
  --start-date 2023-04-01 \
  --end-date 2023-04-30 \
  --offline \
  --resume \
  --fixture tests/fixtures/incremental/contratacao_offline.json \
  --db-path /tmp/pncp_incremental_offline.db
```

Uma janela concluída somente pode ser executada novamente com
`--reprocess`. Retomada e reprocessamento são mutuamente exclusivos.

## Monitoramento e retomada automática

Os templates em `ops/systemd/` executam o piloto sempre com `--resume` e
`Restart=on-failure`, aguardando 30 minutos entre falhas. `--resume` também é
válido para uma janela ainda inexistente; por isso o mesmo comando serve para
iniciar e recuperar. Os templates são exemplos e não são instalados ou ativados
automaticamente.

O acompanhamento pode ser feito pelo journal e pelos logs JSONL em `/tmp`:

```bash
journalctl --user -u pncp-incremental-pilot.service -f
tail -f /tmp/pncp_incremental_pilot.http.jsonl
tail -f /tmp/pncp_incremental_pilot.incremental.jsonl
```

## Planejamento de janelas e sobreposição

`plan_closed_windows()` gera janelas inclusivas e determinísticas sem usar o
maior timestamp recebido como cursor. A política padrão revisita dois dias já
confirmados e limita cada janela a um dia. Tanto `overlap_days` quanto
`max_window_days` são configuráveis e validados. Janelas que atravessam o
watermark confirmado são marcadas explicitamente para reprocessamento.

## Jobs, janelas e watermark

O `job_id` é derivado deterministicamente de:

- nome lógico do job;
- modo incremental;
- recurso;
- estratégia;
- endpoint e filtros do adaptador;
- UF;
- modalidade;
- filtros da configuração.

O intervalo não faz parte do job: ele identifica a `sync_window`. Assim, o
mesmo job pode possuir várias janelas fechadas e inclusivas. O watermark usa o
valor explícito da fixture ou, na ausência dele, o fim determinístico da janela;
nunca usa o horário local de conclusão como posição da fonte.

## Estratégias por recurso

| Recurso | Suporte offline | Estratégia | Situação para API real |
|---|---:|---|---|
| `pca` | Sim | `endpoint` | adaptador testado; CLI online ainda bloqueado |
| `contratacao` | Sim | `endpoint` | piloto online explícito habilitado somente em `/tmp` |
| `contrato` | Sim | `endpoint` | adaptador HTTP testado com transporte simulado; não ativado |
| `ata` / `ata_registro_preco` | Sim | `endpoint` | adaptador HTTP testado com transporte simulado; não ativado |
| `contratacao_item` | Sim | `relationship` | adaptador por alvo testado; não ativado |
| `resultado_item` | Sim | `relationship` | adaptador por alvo testado; não ativado |
| `empenho` | Sim | `relationship` | adaptador por alvo testado; não ativado |

Nenhum endpoint dependente foi habilitado genericamente no CLI.

## Formato das fixtures

Recursos com estratégia `endpoint` usam páginas:

```json
{
  "strategy": "endpoint",
  "watermark_value": "2023-04-30T23:59:59Z",
  "pages": [
    {
      "cursor": "1",
      "records": [
        {
          "external_id": "identidade-canônica-do-registro",
          "payload": {"campo": "valor"}
        }
      ]
    }
  ]
}
```

Recursos com estratégia `relationship` agrupam páginas por alvo estável:

```json
{
  "strategy": "relationship",
  "targets": [
    {
      "target": {"numero_controle": "compra-pai"},
      "pages": [
        {
          "cursor": "1",
          "records": [
            {
              "external_id": "compra-pai|1",
              "payload": {"numeroItem": 1}
            }
          ]
        }
      ]
    }
  ]
}
```

O formato acima permanece compatível e considera cada entrada simultaneamente
uma descoberta do pai e uma resposta simulada. Para testar filhos tardios sem
fingir uma nova alteração do pai, use o contrato explícito:

```json
{
  "strategy": "relationship",
  "discoveries": [],
  "responses": [
    {
      "target": {"numero_controle": "compra-pai", "numero_item": 1},
      "outcome": "FOUND",
      "pages": [
        {
          "records": [
            {
              "external_id": "compra-pai|1|1",
              "payload": {"sequencialResultado": 1}
            }
          ]
        }
      ]
    }
  ]
}
```

`discoveries` representa pais novos ou alterados. `responses` representa o que
o adaptador encontrou ao consultar os alvos que a fila durável declarou devidos.
Os resultados aceitos são:

- `FOUND`: um ou mais registros;
- `EMPTY`: consulta válida sem registros, incluindo `200 []` ou `204`;
- `NOT_FOUND`: ausência contextual, como o `404` observado para empenhos.

Erros de transporte ou HTTP não elegíveis não são resultados vazios: eles
falham a janela e não avançam o reagendamento do alvo.

## Reconciliação de recursos dependentes

Itens, resultados e empenhos são mantidos em
`sync_reconciliation_target`. Uma resposta vazia conclui somente a tentativa
atual; o alvo recebe uma próxima data e pode ser consultado novamente sem que o
pai tenha mudado. A janela anterior continua imutável.

Baseline operacional congelada em `config/pncp_incremental_policy.json`:

| Recurso | Cadência | Horizonte |
|---|---:|---:|
| Item | 1 dia | 365 dias |
| Resultado | 1 dia | 365 dias |
| Empenho | 7 dias | 730 dias |

Esses valores estão aprovados para piloto, não constituem SLA do PNCP. Devem
ser reavaliados após 30 dias de observação. Podem ser sobrescritos por
`--reconciliation-cadence-days` e
`--reconciliation-horizon-days`. A conclusão em `sync_target_progress` e o
reagendamento da fila ocorrem na mesma transação.

`external_id` é obrigatório para evitar que a fixture invente regras de
identidade diferentes das regras dos coletores reais.

`discover_dependency_targets()` extrai somente pais `NEW/UPDATED` de uma
execução concluída. Replays `UNCHANGED` não criam nova descoberta; continuam
cobertos pela reconciliação periódica.

## Renovação de lease

A renovação é cooperativa e ocorre no mesmo worker, entre os eventos da fonte.
Não existe thread adicional. Os parâmetros são:

```text
--lease-seconds
--lease-renewal-seconds
```

O intervalo de renovação deve ser menor que a duração do lease. A duração do
lease também deve ser maior que o tempo máximo de uma única operação bloqueante.
Se uma operação individual ultrapassar o lease, a confirmação é recusada e a
janela permanece recuperável depois da expiração.

## Banco existente

Uma cópia de banco histórico precisa receber, manualmente e nessa ordem:

```text
migrations/001_raw_canonical_identity_index.sql
migrations/002_sync_window_checkpoint.sql
migrations/003_raw_observation.sql
migrations/004_sync_reconciliation_target.sql
migrations/005_sync_coverage_manifest.sql
```

Em 17/10/2026, as cinco migrações foram aplicadas a uma cópia integral do
banco operacional com 1.322.020 registros RAW. `PRAGMA integrity_check`
retornou `ok`, `PRAGMA foreign_key_check` não retornou violações e as contagens
RAW permaneceram iguais. O banco oficial não foi migrado.

## Versões RAW e observações

`raw_asset` armazena versões imutáveis. `raw_observation` registra cada janela
que observou uma versão, inclusive quando o resultado é `UNCHANGED`. Assim,
duas janelas podem apontar para o mesmo `raw_asset` sem duplicar o payload, e a
reexecução da mesma janela continua idempotente.

## Cobertura composta

Um watermark de job comprova somente aquele shard. Quando a cobertura depende
de várias modalidades, órgãos ou unidades, configure um manifesto congelado:

```text
--coverage-name contratacoes-rj-modalidades
--coverage-member modalidade:6
--coverage-members modalidade:1 modalidade:2 modalidade:3 modalidade:6
```

Todas as execuções da mesma janela devem repetir exatamente o mesmo nome e
conjunto de membros, mudando apenas `--coverage-member`. O conjunto não pode
ser ampliado silenciosamente depois da primeira janela. A execução informa
`coverage_status=PENDING` enquanto faltar algum membro e `COMPLETED` somente
quando o manifesto inteiro estiver coberto.

Para recursos particionados por órgão/unidade, `build_scope_inventory()`
constrói um inventário ordenado e deduplicado a partir de payloads de
contratação. CNPJ, unidade e UF são obrigatórios; registros fora do recorte ou
incompletos interrompem a construção. O hash `inventory_id` identifica o
conjunto congelado e `coverage_members` alimenta o manifesto composto.

O inventário congelado está em `config/pncp_rj_scope_inventory.json`:

- `inventory_id`:
  `inventory-fec7430eec6a854f91858dc1996a504bc9a50818f4de20dfa79b7b8d1ff2897f`;
- 2.116 pares órgão/unidade e 617 CNPJs;
- modalidades esperadas 1–14; a modalidade 2 não apareceu na RAW examinada,
  mas permanece como membro esperado;
- contratos e atas observados estão contidos no inventário;
- foram observados 839 pares órgão/unidade nos PCAs: 292 dentro do inventário
  e 547 fora dele. Dos 1.578 planos, 567 ficaram integralmente dentro do
  inventário e 1.011 possuem ao menos um par externo, pois a coleta antiga
  consultou por CNPJ. O adaptador incremental agora exige e valida também
  `codigoUnidade`.

A completude declarada é relativa à RAW histórica de contratações, não a um
cadastro externo oficial de todos os órgãos do estado.

As migrações não são aplicadas automaticamente em bancos existentes. Nunca use
o snapshot original como destino da simulação.

## Evidências do piloto real

Em uma cópia migrada em `/tmp` foram validados:

- HTTP 200 com 62 contratações em duas páginas;
- HTTP 204 como janela válida vazia;
- HTTP 404 de empenho como `NOT_FOUND` contextual;
- retry 503 → 200 em transporte HTTP local controlado;
- contratação → 38 itens, dos quais 28 novos em relação ao snapshot;
- item novo → resultado homologado novo;
- integridade e chaves estrangeiras válidas após todas as execuções.

## Pendências para ativação operacional

1. Obter uma fonte externa oficial para medir a completude absoluta do
   inventário de órgãos/unidades do RJ.
2. Acompanhar longitudinalmente um empenho alterado para verificar se o
   contrato-pai também muda; o 404 contextual já foi comprovado.
3. Criar a orquestração/scheduler que consome janelas planejadas e descobertas
   de dependência com retomada automática.
4. Obter autorização específica antes de aplicar `001`–`005` no banco oficial.
5. Habilitar online os demais recursos um por vez, repetindo o piloto isolado.
