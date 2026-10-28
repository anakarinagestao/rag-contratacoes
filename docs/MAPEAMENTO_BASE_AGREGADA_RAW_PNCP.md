# Mapeamento da Base Agregada até a Camada RAW

## Contratações Públicas do Estado do Rio de Janeiro

**Projeto:** Grande Desafio — IA Generativa & RAG  
**Cenário:** Contratações Públicas do Estado do Rio de Janeiro  
**Fonte mapeada:** Portal Nacional de Contratações Públicas (PNCP)  
**Natureza da base:** Agregada  
**Etapa documentada:** Fonte externa → coleta → persistência RAW  
**Data do mapeamento:** 21/09/2026

---

## 1. Objetivo deste documento

Este documento descreve integralmente o caminho implementado para a Base
Agregada desde a API pública do PNCP até a persistência dos objetos originais
na camada RAW do projeto.

O mapeamento responde:

- o que é a Base Agregada no projeto;
- qual fonte foi conectada;
- qual endpoint está sendo utilizado;
- como o `corpus_manifest.yaml` controla a coleta;
- como a URL é construída;
- como a resposta do PNCP é percorrida;
- como os registros são identificados;
- como o hash e a idempotência funcionam;
- como os dados são gravados no SQLite;
- quais tabelas e campos foram criados;
- quais testes e validações reais foram realizados;
- quais limitações e pendências ainda existem.

Este documento termina na RAW. Não fazem parte do escopo atual:

- normalização dos itens;
- criação de tabelas analíticas;
- cálculo de médias, totais ou indicadores;
- chunking;
- embeddings;
- BM25;
- indexação;
- Adaptive RAG;
- geração de respostas.

---

## 2. O que é a Base Agregada

A Base Agregada é a natureza de evidência destinada a responder perguntas
quantitativas e analíticas, por exemplo:

- quanto foi planejado;
- quantos itens existem;
- qual é o valor médio;
- qual é o total por órgão, categoria ou período;
- qual é a diferença entre valor planejado e homologado;
- como os valores evoluem ao longo do tempo.

Todo número agregado deve preservar seu contexto:

```text
valor + unidade + período + órgão + recorte + fonte + atualização
```

No cenário do projeto, a Base Agregada foi definida como:

```text
Plano de Contratações Anual (PCA)
+
valores homologados por item
```

### 2.1 Situação atual

A implementação atual cobre duas provas controladas: PCA com seus itens
planejados e resultados homologados por item de uma contratação real. A
cobertura ainda não representa todo o recorte do Estado do Rio de Janeiro.

Os valores existentes no PCA são valores planejados ou estimados. Eles não
devem ser tratados como valores efetivamente homologados, contratados ou
pagos.

```text
PCA                = planejamento
resultado de item  = homologação
contrato           = compromisso formal
pagamento          = execução financeira
```

---

## 3. O que é a camada RAW

A camada RAW é a porta de entrada do corpus. Sua responsabilidade é guardar
o objeto recebido da fonte com fidelidade e rastreabilidade.

Na RAW:

- o payload original é preservado;
- os campos do PNCP não são renomeados;
- os itens não são separados em tabelas analíticas;
- os valores não são recalculados;
- nenhuma média ou total é produzido;
- metadados de origem e auditoria são adicionados;
- hashes permitem detectar repetições e mudanças;
- versões diferentes de um mesmo objeto são preservadas.

Princípio utilizado:

```python
payload = record
```

Não é realizado um recorte como:

```python
payload = {
    "id": record["idPcaPncp"],
    "ano": record["anoPca"],
}
```

---

## 4. Fonte externa utilizada

### 4.1 Portal

```text
https://pncp.gov.br
```

### 4.2 API utilizada

```text
API pública de consulta
https://pncp.gov.br/api/consulta
```

### 4.3 Documentação

```text
Swagger:
https://pncp.gov.br/api/consulta/swagger-ui/index.html

OpenAPI JSON:
https://pncp.gov.br/api/consulta/v3/api-docs
```

O caminho `/v3/api-docs` identifica a documentação OpenAPI. O endpoint de
negócio utilizado pelo projeto continua versionado como `/v1`.

### 4.4 API que não deve ser confundida

```text
/api/pncp
ou
/pncp-api
```

Esses caminhos pertencem à API de manutenção/integração. A coleta atual
utiliza a API pública de consulta e não depende de credenciais.

---

## 5. Recursos disponíveis na API pública de consulta

A especificação consultada expõe as seguintes famílias:

| Família | Endpoints |
|---|---|
| PCA | `/v1/pca/`, `/v1/pca/atualizacao`, `/v1/pca/usuario` |
| Atas | `/v1/atas`, `/v1/atas/atualizacao` |
| Contratações | `/v1/contratacoes/publicacao`, `/v1/contratacoes/atualizacao`, `/v1/contratacoes/proposta` |
| Detalhe de contratação | `/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}` |
| Contratos/Empenhos | `/v1/contratos`, `/v1/contratos/atualizacao` |
| Instrumentos de cobrança | `/v1/instrumentoscobranca/inclusao` |

O campo `resource_type` não é fornecido oficialmente pelo PNCP. Ele é um
classificador interno do projeto.

Na implementação atual:

```text
resource_type = pca
```

---

## 6. Contrato atual do corpus

O arquivo `corpus_manifest.yaml` é a fonte de verdade da composição do
corpus. Ele descreve o que coletar, de onde coletar e como percorrer a fonte.

Configuração atual:

```yaml
version: 1
sources:
  - id: pncp_pca_rj
    base: https://pncp.gov.br
    base_id: pncp
    natureza: agregada
    resource_type: pca
    endpoint: /api/consulta/v1/pca/atualizacao
    params:
      dataInicio: "20250401"
      dataFim: "20250430"
      cnpj: "42498733000148"
    pagination:
      page_param: pagina
      page_size_param: tamanhoPagina
      page_size: 10
      max_pages: 2
    request:
      timeout_seconds: 30
      rate_limit_seconds: 0.2
    item_path: data
    key_fields:
      - idPcaPncp
```

### 6.1 Dicionário do manifesto

| Campo | Valor atual | Finalidade |
|---|---|---|
| `version` | `1` | Versão do formato do manifesto |
| `sources` | lista | Fontes integrantes do corpus |
| `id` | `pncp_pca_rj` | Identificador interno da fonte |
| `base` | `https://pncp.gov.br` | Domínio da fonte |
| `base_id` | `pncp` | Identificador da base externa |
| `natureza` | `agregada` | Natureza da evidência |
| `resource_type` | `pca` | Tipo semântico persistido |
| `endpoint` | `/api/consulta/v1/pca/atualizacao` | Recurso consultado |
| `params` | datas e CNPJ | Recorte determinístico da coleta |
| `page_param` | `pagina` | Nome do parâmetro de página |
| `page_size_param` | `tamanhoPagina` | Nome do tamanho da página |
| `page_size` | `10` | Quantidade solicitada por página |
| `max_pages` | `2` | Limite temporário de páginas |
| `timeout_seconds` | `30` | Timeout da requisição |
| `rate_limit_seconds` | `0.2` | Espera entre requisições |
| `item_path` | `data` | Caminho da lista no JSON de resposta |
| `key_fields` | `idPcaPncp` | Identidade externa do PCA |

### 6.2 Limites do recorte atual

```text
Período:       01/04/2025 a 30/04/2025
CNPJ:          42498733000148
Páginas:       no máximo 2
Itens/página: 10
Natureza:      agregada
Recurso:       pca
```

O identificador `pncp_pca_rj` representa a intenção do corpus, mas o
endpoint não possui filtro direto por UF. A cobertura efetiva do RJ dependerá
de uma lista validada de CNPJs de órgãos fluminenses.

---

## 7. Montagem da requisição

O cliente combina:

```text
base + endpoint + params + paginação
```

Exemplo da primeira página:

```text
https://pncp.gov.br/api/consulta/v1/pca/atualizacao
?dataInicio=20250401
&dataFim=20250430
&cnpj=42498733000148
&pagina=1
&tamanhoPagina=10
```

Em uma única linha:

```text
https://pncp.gov.br/api/consulta/v1/pca/atualizacao?dataInicio=20250401&dataFim=20250430&cnpj=42498733000148&pagina=1&tamanhoPagina=10
```

### 7.1 Erro diagnosticado na configuração anterior

| Anterior | Correto |
|---|---|
| `dataInicial` | `dataInicio` |
| `dataFinal` | `dataFim` |
| `2025-04-01` | `20250401` |
| `uf=RJ` | filtro não suportado nesse endpoint |

A configuração anterior resultava em HTTP 400 por ausência do parâmetro
obrigatório `dataInicio`.

---

## 8. Formato da resposta do PNCP

O endpoint retorna um envelope paginado conceitualmente equivalente a:

```json
{
  "data": [
    {
      "idPcaPncp": "42498733000148-0-000002/2025",
      "anoPca": 2025,
      "orgaoEntidadeCnpj": "42498733000148",
      "orgaoEntidadeRazaoSocial": "...",
      "codigoUnidade": "...",
      "nomeUnidade": "...",
      "dataPublicacaoPNCP": "...",
      "dataAtualizacaoGlobalPCA": "...",
      "itens": []
    }
  ],
  "totalRegistros": 174,
  "totalPaginas": 18,
  "numeroPagina": 1,
  "paginasRestantes": 17,
  "empty": false
}
```

O manifesto declara:

```yaml
item_path: data
```

Logo, cada objeto de `response["data"]` é encaminhado integralmente para a
ingestão RAW.

---

## 9. Dicionário do payload PCA observado

### 9.1 Campos do PCA

| Campo | Interpretação |
|---|---|
| `idPcaPncp` | Identificador do PCA no PNCP |
| `anoPca` | Ano do Plano de Contratações Anual |
| `orgaoEntidadeCnpj` | CNPJ do órgão/entidade |
| `orgaoEntidadeRazaoSocial` | Razão social do órgão/entidade |
| `codigoUnidade` | Código da unidade |
| `nomeUnidade` | Nome da unidade |
| `dataPublicacaoPNCP` | Data de publicação no PNCP |
| `dataAtualizacaoGlobalPCA` | Data de atualização global do PCA |
| `itens` | Lista original de itens incluída no registro |

### 9.2 Campos dos itens observados

| Campo | Interpretação |
|---|---|
| `numeroItem` | Número sequencial do item |
| `codigoItem` | Código do item |
| `descricaoItem` | Descrição do item planejado |
| `quantidadeEstimada` | Quantidade estimada |
| `valorUnitario` | Valor unitário planejado/estimado |
| `valorTotal` | Valor total planejado/estimado |
| `valorOrcamentoExercicio` | Valor previsto para o exercício |
| `unidadeFornecimento` | Unidade de fornecimento |
| `unidadeRequisitante` | Unidade que requisitou o item |
| `dataDesejada` | Data desejada para a contratação |
| `dataInclusao` | Data de inclusão |
| `dataAtualizacao` | Data de atualização |
| `categoriaItemPcaNome` | Categoria do item do PCA |
| `grupoContratacaoCodigo` | Código do grupo de contratação |
| `grupoContratacaoNome` | Nome do grupo de contratação |
| `classificacaoCatalogoId` | Identificador da classificação do catálogo |
| `nomeClassificacaoCatalogo` | Nome da classificação do catálogo |
| `classificacaoSuperiorCodigo` | Código da classificação superior |
| `classificacaoSuperiorNome` | Nome da classificação superior |
| `pdmCodigo` | Código PDM, quando informado |
| `pdmDescricao` | Descrição PDM, quando informada |

Esses campos continuam aninhados em `payload.itens`. Eles ainda não foram
normalizados.

---

## 10. Fluxo técnico completo até a RAW

```text
corpus_manifest.yaml
        |
        v
Manifest.load()
        |
        v
SourceConfig
        |
        v
PNCPClient.build_url()
        |
        v
PNCPClient.fetch_page()
        |
        v
GET PNCP /v1/pca/atualizacao
        |
        v
JSON paginado
        |
        v
PCACollector.collect()
        |
        v
response["data"]
        |
        v
CollectedRecord
        |
        +--> source_id
        +--> payload original
        +--> source_url
        +--> cursor/página
        +--> ordinal
        |
        v
asset_key(payload, key_fields)
        |
        v
external_id = idPcaPncp
        |
        v
payload_hash(payload)
        |
        v
NEW / UPDATED / UNCHANGED
        |
        +--> UNCHANGED: não insere nova RAW
        |
        +--> NEW ou UPDATED
                |
                v
        raw_asset.insert_raw()
                |
                v
             SQLite
```

---

## 11. Mapeamento dos componentes de código

| Arquivo | Responsabilidade na RAW |
|---|---|
| `corpus_manifest.yaml` | Define fonte, recorte, endpoint e paginação |
| `src/schema.py` | Carrega o manifesto e define `SourceConfig`, `CollectedRecord` e `IngestionResult` |
| `src/fetch_corpus.py` | Orquestra o comando `fetch`/`update` |
| `src/ingestion/pncp_client.py` | Monta URL e executa o GET |
| `src/ingestion/pncp_collector.py` | Percorre páginas e extrai `response["data"]` |
| `src/ingestion/hashing.py` | Canonicaliza o JSON e calcula SHA-256 |
| `src/ingestion/change_detector.py` | Gera chave e classifica mudanças |
| `src/ingest.py` | Coordena run, detecção, persistência e checkpoint |
| `app/core/database/connection.py` | Abre o SQLite e habilita chaves estrangeiras |
| `app/core/database/init_db.py` | Cria as tabelas e os índices RAW |
| `app/core/database/repositories/raw_repository.py` | Consulta hashes e insere RAWs imutáveis |
| `app/core/database/repositories/run_repository.py` | Registra início, sucesso e falha da execução |
| `app/core/database/repositories/checkpoint_repository.py` | Salva progresso após execução bem-sucedida |
| `src/obs.py` | Emite eventos JSON estruturados |

---

## 12. Contrato intermediário: `CollectedRecord`

Cada objeto do array `data` é envolvido em:

```python
CollectedRecord(
    source_id="pncp_pca_rj",
    payload=registro_original,
    source_url=url_completa_da_pagina,
    cursor="1",
    ordinal=1,
)
```

| Campo | Finalidade |
|---|---|
| `source_id` | Relaciona o registro à fonte do manifesto |
| `payload` | Preserva o objeto original |
| `source_url` | Mantém a URL exata da consulta |
| `cursor` | Registra a página de origem |
| `ordinal` | Registra a ordem de coleta |

O `PCACollector` interrompe a coleta se um registro não possuir
`idPcaPncp`.

---

## 13. Identidade, hash e estados de mudança

### 13.1 Identidade

```text
external_id = payload["idPcaPncp"]
```

Exemplo real:

```text
42498733000148-0-000002/2025
```

### 13.2 Hash

O hash considera o objeto completo, com chaves ordenadas e serialização
canônica:

```text
SHA-256(JSON canônico do payload completo)
```

Trocar a ordem das chaves não altera o hash. Alterar um valor ou um item
altera o hash.

### 13.3 Estados

| Estado | Regra | Persistência |
|---|---|---|
| `NEW` | Ainda não existe RAW para o `external_id` | Insere |
| `UPDATED` | O `external_id` existe, mas o hash é novo | Insere nova versão |
| `UNCHANGED` | O mesmo `external_id` e hash já existem | Não insere |

---

## 14. Particularidade da paginação do PCA

No recorte real testado, o mesmo `idPcaPncp` apareceu em páginas diferentes,
com subconjuntos diferentes de `itens`:

```text
página 1: PCA A + itens 1 a 10
página 2: PCA A + itens 11 a 20
```

Se a idempotência consultasse somente o último hash do PCA, uma nova
execução alternaria as páginas como falsas atualizações.

A correção implementada verifica se o hash já existe em todo o histórico do
mesmo `source`, `resource_type` e `external_id`:

```sql
SELECT 1
FROM raw_asset
WHERE source = ?
  AND resource_type = ?
  AND external_id = ?
  AND payload_hash = ?
LIMIT 1;
```

Com isso, cada bloco distinto é preservado uma vez e uma segunda coleta
idêntica resulta em `UNCHANGED`.

### 14.1 Consequência semântica atual

Na primeira carga, o primeiro bloco do PCA é `NEW` e o segundo bloco, por ter
o mesmo `external_id` e outro hash, é classificado como `UPDATED`.

Isso preserva os dados e garante idempotência, mas a semântica entre
"versão alterada" e "bloco paginado diferente" ainda merece uma decisão de
modelagem antes da ampliação definitiva do corpus.

---

## 15. Modelo de persistência RAW

### 15.1 Relacionamentos

```text
ingestion_run
      |
      | 1:N
      v
raw_asset
      |
      | previous_version_id (auto-relacionamento)
      v
raw_asset anterior

ingestion_run
      |
      | 1:1 por source/resource_type mais recente
      v
sync_checkpoint
```

### 15.2 Tabela `raw_asset`

| Campo | Função |
|---|---|
| `id` | UUID interno da RAW |
| `source` | ID da fonte do manifesto |
| `base_id` | Base externa (`pncp`) |
| `natureza` | Natureza (`agregada`) |
| `resource_type` | Tipo semântico (`pca`) |
| `endpoint` | URL exata consultada |
| `external_id` | `idPcaPncp` |
| `query_params` | Parâmetros do recorte |
| `payload` | JSON original completo |
| `payload_hash` | SHA-256 canônico |
| `source_published_at` | Data da fonte, ainda não preenchida pelo fluxo atual |
| `source_updated_at` | Atualização da fonte, ainda não preenchida pelo fluxo atual |
| `fetched_at` | Momento da coleta |
| `corpus_version` | Versão declarada na persistência |
| `sensitivity` | Classificação de sensibilidade |
| `ingestion_run_id` | Execução que inseriu o registro |
| `previous_version_id` | Ligação com a RAW anterior |
| `processing_status` | Sempre `RAW` nesta tabela |
| `created_at` | Data de criação da linha |

### 15.3 Tabela `ingestion_run`

| Campo | Função |
|---|---|
| `id` | UUID da execução |
| `source` | Fonte processada |
| `resource_type` | Tipo processado |
| `started_at` | Início |
| `finished_at` | Fim |
| `status` | `RUNNING`, `SUCCESS` ou `FAILED` |
| `window_start` / `window_end` | Janela temporal, ainda não preenchida no fluxo atual |
| `pages_processed` | Quantidade de páginas processadas |
| `records_received` | Registros recebidos |
| `records_inserted` | Registros `NEW` |
| `records_updated` | Registros `UPDATED` |
| `records_unchanged` | Registros `UNCHANGED` |
| `records_failed` | Falhas |
| `error_message` | Mensagem de erro, quando houver |
| `created_at` | Criação do run |

### 15.4 Tabela `sync_checkpoint`

| Campo | Função |
|---|---|
| `id` | UUID do checkpoint |
| `source` | Fonte |
| `resource_type` | Tipo de recurso |
| `last_successful_sync` | Momento da última sincronização bem-sucedida |
| `last_page` | Última página concluída |
| `last_run_id` | Run que produziu o checkpoint |
| `status` | Estado do checkpoint |
| `updated_at` | Atualização |

O checkpoint somente é atualizado depois que o `ingestion_run` termina com
`SUCCESS`.

---

## 16. Paginação e modos de execução

### 16.1 `fetch`

O modo `fetch` inicia na página 1. Isso permite reler a mesma janela e
comparar os hashes para validar idempotência e mudanças.

### 16.2 `update`

O modo `update` inicia após a página registrada no checkpoint quando ele
existe.

### 16.3 Encerramento

O collector encerra quando:

- recebe uma página sem registros; ou
- atinge `max_pages`.

Na configuração atual, `max_pages: 2` encerra a coleta após duas páginas,
mesmo que o PNCP informe mais páginas.

---

## 17. Observabilidade implementada

Os eventos são emitidos em JSON e incluem, conforme a etapa:

| Evento | Finalidade |
|---|---|
| `pncp_request` | Registra método e URL solicitada |
| `pncp_response` | Registra sucesso/falha e latência |
| `pncp_page` | Registra página e quantidade recebida |
| `pncp_fetch` | Relaciona a página ao run |
| `change_detection` | Registra `NEW`, `UPDATED` ou `UNCHANGED` |
| `raw_persistence` | Registra inserção na RAW |
| `checkpoint_update` | Registra avanço do checkpoint |
| `ingestion_failure` | Registra falha da execução |

Os logs são escritos na saída padrão. A persistência em arquivo JSONL
padronizado ainda deve ser consolidada com a estratégia geral do projeto.

---

## 18. Tratamento de erros atual

O cliente transforma em `PNCPClientError`:

- erros HTTP;
- falhas de DNS/rede;
- timeout;
- resposta que não seja JSON válido;
- falha de decodificação.

Em caso de falha durante a ingestão:

- o `ingestion_run` recebe `FAILED`;
- a mensagem é registrada;
- RAWs já inseridas permanecem no banco;
- o checkpoint de sucesso não avança;
- a exceção é propagada.

Limitações atuais:

- não existe retry com backoff;
- o timeout não está separado entre conexão e leitura;
- o status HTTP ainda não é registrado em todos os eventos de sucesso;
- o corpo resumido do erro HTTP não é registrado.

---

## 19. Comando de execução

Na raiz do repositório:

```bash
python3 -m src.fetch_corpus fetch \
  --manifest corpus_manifest.yaml \
  --source pncp_pca_rj \
  --database-url sqlite:///./app.db
```

O banco padrão é:

```text
app.db
```

Esse arquivo é um artefato local e não deve ser considerado parte
versionada do corpus no Git.

---

## 20. Consultas de validação no SQLite

### 20.1 Quantidade de RAWs

```sql
SELECT COUNT(*)
FROM raw_asset;
```

### 20.2 Quantidade por tipo

```sql
SELECT resource_type, COUNT(*) AS quantidade
FROM raw_asset
GROUP BY resource_type;
```

### 20.3 Resumo das RAWs

```sql
SELECT
    external_id,
    resource_type,
    processing_status,
    payload_hash,
    LENGTH(payload) AS tamanho_payload,
    ingestion_run_id
FROM raw_asset;
```

### 20.4 Itens aninhados do PCA

```sql
SELECT
    raw.external_id AS id_pca,
    json_extract(item.value, '$.numeroItem') AS numero_item,
    json_extract(item.value, '$.descricaoItem') AS descricao,
    json_extract(item.value, '$.quantidadeEstimada') AS quantidade,
    json_extract(item.value, '$.valorUnitario') AS valor_unitario,
    json_extract(item.value, '$.valorTotal') AS valor_total
FROM raw_asset AS raw,
     json_each(raw.payload, '$.itens') AS item
ORDER BY numero_item;
```

Essa consulta é apenas uma visualização da RAW. Ela não altera nem
normaliza o payload.

---

## 21. Evidência da validação real

### 21.1 Primeira execução

```text
status:        SUCCEEDED
records_seen:  2
NEW:           1
UPDATED:       1
UNCHANGED:     0
```

### 21.2 Segunda execução idêntica

```text
status:        SUCCEEDED
records_seen:  2
NEW:           0
UPDATED:       0
UNCHANGED:     2
```

### 21.3 Conteúdo

```text
RAWs persistidas:             2
Itens aninhados por RAW:      10
Itens aninhados observados:   20
external_id observado:        42498733000148-0-000002/2025
processing_status:            RAW
checkpoint:                   página 2 / SUCCESS
payload armazenado = original: true
```

### 21.4 Testes offline

```text
17 passed
```

Os 16 testes anteriores foram preservados e foi adicionado um teste para o
mesmo PCA presente em páginas diferentes.

---

## 22. Testes existentes relacionados à RAW

| Teste | O que valida |
|---|---|
| `test_connection.py` | Conexão SQLite, `row_factory` e chaves estrangeiras |
| `test_hashing.py` | Estabilidade e sensibilidade do SHA-256 |
| `test_change_detector.py` | `NEW`, `UPDATED`, `UNCHANGED` e chave externa |
| `test_pncp_client.py` | Construção da URL e tratamento de erros |
| `test_pca_collector.py` | Paginação e preservação do payload |
| `test_checkpoint_repository.py` | Checkpoint somente após sucesso |
| `test_raw_ingestion.py` | Append-only, auditoria, falha e idempotência |

Os testes offline não dependem da internet nem do PNCP.

---

## 23. Matriz de rastreabilidade até a RAW

| Requisito | Implementação atual | Estado |
|---|---|---|
| RF029 — manifesto declarativo | `corpus_manifest.yaml` | Parcial: uma fonte cadastrada |
| RF030 — metadados da fonte | `SourceConfig` + manifesto | Parcial: faltam licença, volume esperado e outros metadados |
| RF031 — seleção determinística | datas e CNPJ fixos | Atendido no recorte atual |
| RF032 — coleta automática | `src/fetch_corpus.py` | Atendido |
| RF033 — operação fetch | CLI `fetch` | Atendido |
| RF034 — operação update | CLI `update` | Parcial: não cria versão imutável completa do corpus |
| RF035 — inventário | `CORPUS_INVENTORY.md` | Pendente: arquivo vazio |
| RF036 — idempotência | hash histórico | Atendido no teste atual |
| RF037 — retomada | checkpoint | Parcial |
| RF038 — rate limit | `rate_limit_seconds` | Atendido |
| RF039 — registrar falhas | run e logs | Parcial: falha de uma fonte encerra o comando |
| RF040 — RAW versionada | `corpus_version` no banco | Parcial: diretórios/versões imutáveis ainda não implementados |
| RF041 — diff de versões | não implementado | Pendente |
| RF042 — inventário detalhado | não implementado | Pendente |
| RNF001 — reprodutibilidade | manifesto + comando | Parcialmente atendido |
| RNF006 — testes offline | 23 testes | Atendido |
| RNF009 — rastreabilidade | URL, source, run, hash | Atendido no recorte atual |

---

## 24. Limitações atuais

### 24.1 Cobertura

- somente um CNPJ;
- somente abril de 2025;
- somente duas páginas;
- PCA limitado a uma amostra controlada;
- resultados homologados limitados a uma contratação piloto;
- a integração já prioriza alvos vindos da RAW estruturada, mas conserva
  um alvo manual de fallback enquanto a consulta paginada estiver indisponível;
- a API de consulta de contratações apresentou timeout de leitura nos testes
  reais desta etapa;
- nenhuma lista oficial de órgãos do RJ;

### 24.2 Corpus

- `CORPUS_INVENTORY.md` está vazio;
- não há diff entre versões;
- não existe congelamento completo de `v1`;
- `corpus_version` ainda usa o valor padrão da ingestão;
- o manifesto não declara todo o contrato previsto em RF030.

### 24.3 Resiliência

- não existe retry limitado com backoff;
- timeout de conexão e leitura não estão separados;
- checkpoint por página precisa ser revisto para cargas completas e janelas
  atualizáveis.

### 24.4 Modelagem

- o mesmo PCA aparece em blocos paginados com hashes distintos;
- `UPDATED` também representa atualmente um novo bloco paginado;
- falta decidir se a identidade RAW definitiva deve considerar página,
  janela ou outro identificador de bloco;
- `source_published_at` e `source_updated_at` não são preenchidos.

### 24.5 Repositório

- `app.db` é artefato local e não deve ser enviado como corpus;
- as alterações desta etapa ainda precisam ser revisadas, commitadas e
  enviadas ao repositório remoto.

---

## 25. Próximos passos permitidos ainda na RAW

1. Aprovar o recorte oficial do corpus agregado.
2. Definir e validar a lista de CNPJs de órgãos do RJ.
3. Decidir a identidade definitiva dos blocos paginados do PCA.
4. Remover ou ampliar `max_pages` somente após essa decisão.
5. Executar uma carga completa controlada do PCA.
6. Implementar retry limitado para falhas transitórias.
7. Melhorar os campos de observabilidade HTTP.
8. Preencher datas de publicação e atualização da fonte nos metadados.
9. Implementar inventário e versão imutável do corpus.
10. Executar com sucesso a carga estruturada paginada, validar os campos de
    ligação e então eliminar a lista piloto manual.
11. Adicionar teste de rede opcional, sem incluí-lo na suíte offline padrão.
12. Reexecutar idempotência, checkpoint e igualdade de payload após cada
    ampliação.

Nenhum desses passos exige avançar para normalização, indicadores,
embeddings ou RAG.

---

## 26. Critério de conclusão da etapa RAW

A etapa RAW da Base Agregada poderá ser considerada concluída quando:

```text
[ ] recorte oficial aprovado
[ ] órgãos/CNPJs do RJ definidos
[ ] todas as páginas previstas coletadas
[x] fonte de valores homologados definida e validada em piloto real
[ ] payload original comprovadamente preservado
[ ] idempotência validada em carga completa
[ ] falha não avança checkpoint
[ ] retomada validada
[ ] retry transitório implementado
[ ] inventário gerado
[ ] versão do corpus congelada
[ ] diff de versões disponível para update
[ ] testes offline passando
[ ] teste real documentado
[ ] código e documentação revisados
```

---

## 27. Resumo executivo

O projeto já provou o fluxo:

```text
PNCP real
  → PCACollector
  → response["data"]
  → hash e detecção de mudança
  → SQLite/raw_asset

PNCP real
  → ItemResultCollector
  → alvos vindos da RAW de contratacao (fallback manual no piloto)
  → itens paginados da contratação
  → resultados dos itens com temResultado=true
  → hash e chave composta
  → SQLite/raw_asset
```

A prova real preservou o payload, gravou duas RAWs com vinte itens aninhados
de PCA e 38 resultados homologados referentes a 42 itens consultados. O total
homologado preservado foi R$ 317.227,64, igual ao exibido pelo portal para a
contratação piloto. A repetição da carga não duplicou dados.

Isso demonstra que a integração técnica funciona, mas não significa que o
corpus agregado esteja completo. A etapa atual é uma amostra controlada de
PCA e resultado de item. A integração para consumir automaticamente os alvos
da RAW estruturada foi implementada e testada offline, mas a carga real de
contratações ainda depende da recuperação do endpoint de consulta, que
apresentou timeout. Permanecem também as decisões de cobertura, identidade,
resiliência, inventário e versionamento listadas neste documento.
