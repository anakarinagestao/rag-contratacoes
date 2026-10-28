# PNCP incremental: endpoints, campos temporais e watermark

Auditoria realizada em 2026-10-10 para a Etapa 4B. Este documento não
habilita coleta HTTP incremental e não altera bancos, migrações ou
checkpoints operacionais.

## Como interpretar as evidências

- **DOC**: confirmado na especificação OpenAPI ou no manual oficial do PNCP.
- **GET**: confirmado por uma consulta real pequena, somente leitura, ao PNCP.
- **RAW**: observado no snapshot local `pncp_rj_raw_2023_t1.sqlite`, aberto em
  modo somente leitura.
- **CÓDIGO**: comportamento confirmado na implementação atual do projeto.
- **RECOMENDAÇÃO**: decisão proposta; não é uma garantia fornecida pelo PNCP.
- **NÃO CONFIRMADO**: a documentação e as consultas realizadas não bastam
  para afirmar o comportamento.

Fontes oficiais consultadas:

- OpenAPI da API de consulta: <https://pncp.gov.br/api/consulta/v3/api-docs>
- Swagger da API de consulta: <https://pncp.gov.br/api/consulta/swagger-ui/index.html>
- OpenAPI da API PNCP: <https://pncp.gov.br/pncp-api/v3/api-docs>
- Manual de Integração: <https://pncp.gov.br/manual/pt-br/latest/singlehtml/>

As bases operacionais publicadas pelas especificações são distintas:

- API de consulta: `https://pncp.gov.br/api/consulta`
- API PNCP por relacionamento/identificador: `https://pncp.gov.br/api/pncp`

O sufixo `/v3/api-docs` identifica a versão da documentação OpenAPI. Os
endpoints operacionais documentados continuam sob `/v1`.

## Resumo executivo

1. **PCA, contratação, contrato/empenho com força de contrato e ata possuem
   endpoints globais por data de atualização.** [DOC, GET]
2. **Itens, resultados homologados e empenhos vinculados a um contrato não
   possuem endpoint global de atualização na especificação examinada.** Eles
   são consultados pela identidade do pai. [DOC]
3. Para contratações, `dataAtualizacaoGlobal` considera alterações nos itens,
   resultados, documentos e imagens. Uma contratação retornada pelo endpoint
   de atualização deve, portanto, acionar a reconciliação de seus filhos.
   [DOC]
4. A API não documenta ordenação estável nem cursor `(timestamp, id)`. O
   watermark seguro é o **fim de uma janela fechada completamente processada**,
   com sobreposição nas execuções seguintes, e não o maior timestamp recebido.
   [DOC, RECOMENDAÇÃO]
5. O PCA tem um bloqueador específico: a paginação observada fragmenta os
   itens dentro do mesmo `idPcaPncp`, e duas páginas chegaram a repetir o item
   161. Persistir cada página como uma versão do PCA produziria versões
   artificiais e incompletas. [GET]
6. Os timestamps observados não contêm offset de timezone. A ata observada
   retornou somente data (`2023-03-01`), embora o OpenAPI declare `date-time`.
   A aplicação não deve inventar UTC para esses valores. [DOC, GET]

## Auditoria da implementação atual

### Coletor incremental e piloto online

`src/fetch_incremental.py` exige a escolha explícita entre `--offline`,
`--online-pilot` e `--online-operational`. Os dois primeiros exigem banco
dentro de `/tmp`. O modo operacional exige que o caminho absoluto de um banco
existente seja repetido em `--confirm-operational-database` e que um log JSONL
absoluto seja informado. Os dois modos online aceitam somente contratações do
RJ, uma modalidade e um dia.
`src/ingestion/incremental_runner.py` classifica os recursos assim:

| Estratégia atual | Recursos |
|---|---|
| Endpoint | `pca`, `contratacao`, `contrato`, `ata_registro_preco` |
| Relacionamento | `contratacao_item`, `resultado_item`, `empenho` |

Essa divisão foi confirmada pela documentação oficial. Somente o adaptador de
contratações está exposto nos modos online; os demais continuam bloqueados
no CLI, embora possuam testes simulados e evidências reais controladas.
[CÓDIGO]

Os coletores históricos são uma base de reaproveitamento, não adaptadores
incrementais prontos:

- `PNCPClient` já converte HTTP 204 em lista vazia, limita o corpo registrado
  nos erros e tenta novamente 429/5xx com backoff;
- `PNCPCollector` encerra pelo `totalPaginas` do envelope e registra o cursor
  de página;
- `PCACollector` usa o feed paginado somente para descobrir
  `idPcaPncp`, deduplica os identificadores e busca cada JSON integral no
  endpoint direto `/itens/plano` antes de emitir um registro RAW. Nenhum
  fragmento do feed é promovido a versão de negócio;
- `PurchaseItemCollector` atual faz uma chamada sem parâmetros de paginação
  por contratação. Como o endpoint oficial aceita paginação e uma consulta
  controlada comprovou que `tamanhoPagina=1` limita a resposta, o adaptador da
  Etapa 5 precisa decidir explicitamente entre a resposta completa padrão e a
  paginação segura;
- os coletores de contratos, atas e empenhos já validam o pai/escopo e somente
  confirmam o alvo depois de concluir suas páginas.

### Checkpoint e watermark v2

O modelo atual separa `sync_job`, `sync_window`, `sync_target_progress` e
`sync_watermark`. O watermark só é confirmado quando:

1. a execução associada terminou com `SUCCESS`;
2. nenhum alvo da janela ficou incompleto;
3. o worker ainda possui um lease válido;
4. o valor pertence à data final da janela;
5. não existe janela anterior ainda não coberta no mesmo job;
6. a nova janela é contígua ou sobreposta à cobertura já confirmada.

Falha de paginação deixa a janela recuperável e não avança o watermark. O
`tie_breaker` existe no esquema, mas não deve virar cursor HTTP enquanto o PNCP
não garantir ordenação compatível. [CÓDIGO]

Para recursos globais por endpoint, uma retomada repete a janela desde a
primeira página. O `progress_cursor` é evidência operacional, não uma fronteira
segura para pular páginas: registros podem mudar de página entre tentativas. A
identidade canônica e o hash tornam a repetição idempotente, e o watermark só
avança depois da nova varredura completa. Recursos por relacionamento mantêm
retomada por alvo estável já concluído. [CÓDIGO, RECOMENDAÇÃO]

Os coletores paginados validam `numeroPagina` e mantêm o primeiro
`totalPaginas` como contrato da tentativa. Uma página vazia anterior ao total
declarado é repetida com limite e backoff; se continuar vazia, se o total mudar
ou se o número retornado não corresponder ao solicitado, a coleta falha sem
confirmar o alvo/janela. Lista vazia sem envelope, `204` convertido em lista
vazia e página final declarada vazia continuam representando ausência legítima
de registros. [CÓDIGO]

### RAW e timestamps

`source_timestamps()` já procura os seguintes campos de atualização:

| Recurso | Campo preferencial | Fallback |
|---|---|---|
| PCA | `dataAtualizacaoGlobalPCA` | `dataAtualizacao` |
| Contratação | `dataAtualizacaoGlobal` | `dataAtualizacao` |
| Contrato | `dataAtualizacaoGlobal` | `dataAtualizacao` |
| Ata | `dataAtualizacaoGlobal` | `dataAtualizacao` |
| Item | `dataAtualizacao` | nenhum |
| Resultado | `dataAtualizacao` | nenhum |
| Empenho vinculado | `dataAtualizacao` | nenhum |

Os campos preferenciais coincidem com os schemas oficiais. O JSON original é
preservado e o hash considera o payload completo, portanto uma mudança de
conteúdo com timestamp igual ainda é detectada. [DOC, CÓDIGO]

Há três ressalvas antes da ativação online:

- `source_published_at` mistura conceitos: para itens e empenhos usa
  `dataInclusao`; para resultados prefere `dataResultado`, que é uma data do
  evento administrativo e não a publicação. Esse metadado não deve controlar
  progresso. Uma evolução futura deve separar publicação, inclusão e evento.
- `normalized_timestamp()` preserva texto inválido como texto comparável. Um
  valor malformado poderia influenciar a escolha da versão atual. Antes da
  operação online, valores inválidos devem ser preservados no JSON, registrados
  como anomalia e excluídos da ordenação temporal.
- Quando não há timestamp da fonte, a seleção atual recorre a `fetched_at`.
  Isso é compatível com o histórico, mas nunca deve ser apresentado como
  watermark do PNCP.

## Matriz de endpoints

| Recurso | Endpoint GET | Filtros principais | Campo temporal | Estratégia recomendada | Validação |
|---|---|---|---|---|---|
| PCA | `/api/consulta/v1/pca/atualizacao` | período, CNPJ, unidade, página | `dataAtualizacaoGlobalPCA` | janela por CNPJ/unidade; resolver paginação fragmentada antes de ativar | DOC, GET |
| Contratação | `/api/consulta/v1/contratacoes/atualizacao` | período, modalidade obrigatória, UF e outros | `dataAtualizacaoGlobal` | janela fechada por modalidade e UF | DOC, GET |
| Contrato/empenho com força de contrato | `/api/consulta/v1/contratos/atualizacao` | período, CNPJ, unidade, usuário | `dataAtualizacaoGlobal` | janela por CNPJ/unidade; validar RJ no payload | DOC, GET |
| Ata | `/api/consulta/v1/atas/atualizacao` | período, CNPJ, unidade, usuário | `dataAtualizacaoGlobal` | janela por CNPJ/unidade; validar RJ por inventário/vínculo | DOC, GET |
| Item da contratação | `/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens` | identidade do pai; paginação opcional | `dataAtualizacao` | progresso por alvo + reconciliação | DOC, GET |
| Resultado homologado | `/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens/{numeroItem}/resultados` | identidade do pai e item | `dataAtualizacao` | progresso por alvo + reconciliação | DOC, GET |
| Empenho vinculado ao contrato | `/api/pncp/v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/empenhos` | identidade do contrato; paginação opcional | `dataAtualizacao` | progresso por alvo + reconciliação | DOC; GET retornou 404 vazio |

### Endpoints de publicação não substituem os de atualização

- `/v1/contratacoes/publicacao` filtra `dataPublicacaoPncp`.
- `/v1/contratos` filtra data de publicação.
- `/v1/atas` filtra período de vigência, não atualização.
- `/v1/pca/` consulta itens por ano e classificação; não é o feed
  incremental.

Esses endpoints continuam úteis para carga histórica e reconciliação, mas
não devem avançar o watermark de atualização. [DOC]

## Parâmetros confirmados

Todos os períodos abaixo usam texto no formato `yyyyMMdd`. `pagina` começa em
1. A especificação não oferece parâmetro de ordenação. [DOC]

### PCA por atualização

`GET /api/consulta/v1/pca/atualizacao`

| Parâmetro | Obrigatório | Restrição documentada |
|---|---:|---|
| `dataInicio` | sim | `yyyyMMdd` |
| `dataFim` | sim | `yyyyMMdd` |
| `cnpj` | não | texto |
| `codigoUnidade` | não | texto |
| `pagina` | sim | inteiro >= 1 |
| `tamanhoPagina` | não | 10 a 500 |

Não existe filtro de UF. Para o recorte RJ, consultar por CNPJ/unidade
derivados de um inventário de unidades do RJ e validar o escopo localmente.

### Contratações por atualização

`GET /api/consulta/v1/contratacoes/atualizacao`

| Parâmetro | Obrigatório | Restrição documentada |
|---|---:|---|
| `dataInicial`, `dataFinal` | sim | `yyyyMMdd` |
| `codigoModalidadeContratacao` | sim | inteiro |
| `codigoModoDisputa` | não | inteiro |
| `uf` | não | texto |
| `codigoMunicipioIbge` | não | texto |
| `cnpj` | não | texto |
| `codigoUnidadeAdministrativa` | não | texto |
| `idUsuario` | não | inteiro |
| `pagina` | sim | inteiro >= 1 |
| `tamanhoPagina` | não | 10 a 50 |

Cobertura completa exige um shard/job por modalidade. O conjunto de
modalidades deve ser congelado no início de cada janela; adicionar uma nova
modalidade depois não pode fazer uma janela antiga parecer completa.

O runner offline materializa essa regra por meio de `sync_coverage_plan`,
`sync_coverage_window`, `sync_coverage_member` e
`sync_coverage_watermark`. O watermark individual continua limitado ao shard;
somente o watermark composto representa a cobertura integral declarada.

### Contratos por atualização

`GET /api/consulta/v1/contratos/atualizacao`

| Parâmetro | Obrigatório | Restrição documentada |
|---|---:|---|
| `dataInicial`, `dataFinal` | sim | `yyyyMMdd` |
| `cnpjOrgao` | não | texto |
| `codigoUnidadeAdministrativa` | não | texto |
| `usuarioId` | não | inteiro |
| `pagina` | sim | inteiro >= 1 |
| `tamanhoPagina` | não | 10 a 500 |

O endpoint não oferece UF. O título oficial diz "Contratos/Empenhos": ele
abrange o registro principal que pode representar contrato ou empenho com
força de contrato. Isso não é o mesmo recurso que a lista de empenhos
dependentes de um contrato.

### Atas por atualização

`GET /api/consulta/v1/atas/atualizacao`

| Parâmetro | Obrigatório | Restrição documentada |
|---|---:|---|
| `dataInicial`, `dataFinal` | sim | `yyyyMMdd` |
| `idUsuario` | não | inteiro |
| `cnpj` | não | texto |
| `codigoUnidadeAdministrativa` | não | texto com 1 a 30 caracteres |
| `pagina` | sim | inteiro >= 1 |
| `tamanhoPagina` | não | 10 a 500 |

O endpoint não oferece UF. O recorte deve usar os CNPJs/unidades conhecidos
e validar o vínculo ou escopo no payload.

### Recursos dependentes

- Itens: `cnpj`, `ano` e `sequencial` são obrigatórios no path; `pagina` e
  `tamanhoPagina` são opcionais e aceitam valor >= 1.
- Resultados: `cnpj`, `ano`, `sequencial` e `numeroItem` são obrigatórios; não
  há paginação documentada e a resposta é uma lista.
- Empenhos: `cnpj`, `ano` e `sequencial` do contrato são obrigatórios;
  `pagina` é opcional e `tamanhoPagina`, quando enviado, deve ser >= 10.

Itens retornaram uma lista simples mesmo com paginação, sem
`totalPaginas`. O adaptador precisa definir conclusão por página curta/vazia e
testar o caso em que a última página tem exatamente o tamanho solicitado. [GET]

### Relações oficiais auxiliares

A API PNCP também documenta:

- contratos de uma contratação:
  `/v1/orgaos/{cnpj}/contratos/contratacao/{anoContratacao}/{sequencialContratacao}`;
- atas de uma contratação:
  `/v1/orgaos/{cnpj}/compras/{anoCompra}/{sequencialCompra}/atas`;
- itens de PCA associados a um número de contratação:
  `/v1/orgaos/{cnpj}/pca/{ano}/{sequencial}/itens/contratacao`, com
  `numeroContratacao` obrigatório.

Esses endpoints podem auxiliar reconciliação e modelagem de vínculos, mas não
substituem feeds globais de atualização. A simples existência do terceiro
endpoint não prova que todo item de PCA tenha associação preenchida nem que o
campo corresponda diretamente ao `numeroControlePNCP`; essa cobertura não foi
validada nesta etapa. [DOC, NÃO CONFIRMADO]

## Identidade canônica confirmada

| Recurso | Identidade da entidade |
|---|---|
| PCA | `idPcaPncp` |
| Contratação | `numeroControlePNCP` |
| Contrato/empenho com força de contrato | `numeroControlePNCP` |
| Ata | `numeroControlePNCPAta` |
| Item | `(numeroControlePNCP da contratação, numeroItem)` |
| Resultado | `(numeroControlePNCPCompra, numeroItem, sequencialResultado)` |
| Empenho dependente | `(numeroControlePNCP do contrato, sequencialEmpenho)`; o payload também traz CNPJ, ano e sequencial do contrato |

`external_id` isolado não é global: a identidade persistida continua sendo
`(base_id, resource_type, external_id)`. [DOC, CÓDIGO]

## Campos temporais

| Recurso | Publicação/inclusão | Atualização da entidade | Atualização global |
|---|---|---|---|
| PCA | `dataPublicacaoPNCP` | itens possuem `dataAtualizacao` | `dataAtualizacaoGlobalPCA` |
| Contratação | `dataPublicacaoPncp`, `dataInclusao` | `dataAtualizacao` | `dataAtualizacaoGlobal` |
| Contrato | `dataPublicacaoPncp` | `dataAtualizacao` | `dataAtualizacaoGlobal` |
| Ata | `dataPublicacaoPncp`, `dataInclusao` | `dataAtualizacao` | `dataAtualizacaoGlobal` |
| Item | `dataInclusao` | `dataAtualizacao` | não possui |
| Resultado | `dataInclusao`; `dataResultado` é evento administrativo | `dataAtualizacao` | não possui |
| Empenho dependente | `dataInclusao`; também possui emissão/situação | `dataAtualizacao` | não confirmado |

O OpenAPI não marca esses campos como obrigatórios nos schemas de retorno.
O adaptador deve aceitar ausência sem substituir por `fetched_at`.

### Formatos observados

- PCA: `2025-04-24T15:57:02`.
- Contratação: `2023-03-01T07:01:22`.
- Contrato: `2023-03-01T08:40:35`.
- Ata: `2023-03-01`, apesar de o schema declarar `date-time`.
- Item: `2023-03-13T12:11:53`.
- Resultado: atualização `2023-04-28T17:28:17` e evento
  `dataResultado=2023-03-13`.

Nenhum desses exemplos continha `Z` ou offset. O significado de timezone dos
campos de atualização **não foi confirmado**. Preservar o texto original;
normalizar apenas para comparação quando o formato for válido e não anexar um
offset inventado.

## Consultas GET controladas

Nenhuma resposta foi persistida. Todas as chamadas usaram `GET`, uma página e
o menor tamanho aceito, salvo o endpoint de resultados que não pagina.

### Endpoints por atualização

| Recurso/período | HTTP | Resultado observado |
|---|---:|---|
| PCA, 2023-04-01 | 204 | sem corpo |
| Contratação modalidade 6/RJ, 2023-04-01 | 204 | sem corpo |
| Contrato, 2023-04-01 | 204 | sem corpo |
| Ata, 2023-04-01 | 204 | sem corpo |
| PCA/CNPJ controlado, 2025-04-01 a 2025-04-30 | 200 | 1 plano na página; 10 itens; 174 registros e 18 páginas informados |
| Contratação modalidade 6/RJ, 2023-03-01 | 200 | 1 registro, 1 página |
| Contrato, 2023-03-01 | 200 | 10 de 151 registros, 16 páginas |
| Ata, 2023-03-01 | 200 | 1 registro, 1 página |

Um intervalo com início e fim iguais foi aceito e retornou registros daquele
dia. Isso sustenta tratar as janelas do projeto como inclusivas, mas uma
sobreposição continua necessária porque o PNCP não documenta SLA de
atualizações atrasadas. [GET, RECOMENDAÇÃO]

### Recursos dependentes

Usando identificadores já presentes no snapshot T1:

- Itens: HTTP 200, lista de 1 item quando `tamanhoPagina=1`; campos
  `numeroItem`, `dataInclusao`, `dataAtualizacao` e `temResultado` presentes.
- Resultados: HTTP 200, lista de 1 resultado; campos
  `numeroControlePNCPCompra`, `numeroItem`, `sequencialResultado`,
  `dataInclusao`, `dataAtualizacao` e `dataResultado` presentes.
- Empenhos de um contrato: HTTP 404 com a mensagem "Nenhum empenho do
  contrato encontrado". No coletor de relacionamento, esse 404 específico deve
  continuar sendo tratado como conjunto vazio, sem ser generalizado para
  endpoints em que 404 indique identidade inválida.

### Achado crítico da paginação de PCA

Foram comparadas somente as páginas 1 e 2 de um mesmo recorte:

```text
página 1: idPcaPncp 42498733000148-0-000002/2025
          itens [162, 163, 164, 165, 166, 167, 29, 50, 41, 161]
página 2: idPcaPncp 42498733000148-0-000002/2025
          itens [2, 3, 4, 5, 6, 7, 8, 9, 10, 161]
```

O plano se repetiu e o item 161 apareceu nas duas páginas. A ordem não foi
crescente. Uma amostra não prova que a paginação sempre se comportará assim,
mas prova que o adaptador não pode assumir páginas disjuntas, ordenar pelo
número do item nem persistir cada fragmento como estado completo do PCA.

A estratégia escolhida não concatena esses fragmentos. O feed de atualização
serve apenas para descobrir os identificadores alterados; depois que todas as
páginas foram lidas, cada plano é consultado no endpoint direto
`/api/pncp/v1/orgaos/{cnpj}/pca/{ano}/{sequencial}/itens/plano`. O payload
integral retornado por esse endpoint é validado e preservado como RAW. Se um
plano estiver ausente ou inconsistente, a tentativa falha antes de emitir o
primeiro registro. [CÓDIGO, TESTE OFFLINE]

O runner incremental offline exige `payload_kind=complete_pca_plan`, valida a
identidade dos itens e recusa o mesmo PCA repetido em páginas diferentes. O
adaptador HTTP incremental mantém esse contrato: deduplica a descoberta, busca
e valida todos os detalhes antes de emitir o primeiro plano. A coleta HTTP
incremental continua desabilitada no CLI.

## Estratégia de watermark

### Princípios comuns

1. Dividir o tempo em janelas fechadas e determinísticas, inicialmente diárias.
2. Processar todas as páginas e todos os shards/filtros esperados.
3. Confirmar `window_end` somente depois do `SUCCESS` e dos alvos completos.
4. Não usar o maior timestamp recebido como posição de progresso.
5. Reconsultar uma faixa anterior controlada. Sugestão inicial de piloto: 2 a
   7 dias; medir atrasos para definir o valor operacional.
6. Aceitar timestamps iguais e deixar identidade canônica + hash realizar a
   deduplicação. Nunca filtrar localmente apenas com `>`.
7. `204` confirma que aquela consulta não retornou conteúdo, mas uma janela
   composta só termina quando todos os seus shards responderem com sucesso.
8. Qualquer 4xx inesperado, 5xx, timeout, JSON inválido, página ausente ou
   divergência de paginação falha a janela e conserva o watermark anterior.
9. Não usar `(timestamp, id)` como cursor enquanto não existir garantia de
   ordenação. O `tie_breaker` pode permanecer vazio.

### Por recurso

| Recurso | Posição confirmada | Unidade do job | Sobreposição/reconciliação |
|---|---|---|---|
| Contratação | fim da janela de `dataAtualizacaoGlobal` | UF + modalidade + demais filtros fixos | repetir dias anteriores; só declarar cobertura quando todas as modalidades terminarem |
| Contrato | fim da janela de `dataAtualizacaoGlobal` | CNPJ/unidade RJ | repetir dias anteriores e validar UF do payload |
| Ata | fim da janela de `dataAtualizacaoGlobal` | CNPJ/unidade RJ | repetir dias anteriores; não usar vigência como atualização |
| PCA | fim da janela de `dataAtualizacaoGlobalPCA` | CNPJ/unidade RJ | mesma sobreposição, mas bloqueado até resolver a paginação |
| Item | não possui watermark temporal global | alvo contratação | fingerprint/estado do alvo + ciclo de reconciliação |
| Resultado | não possui watermark temporal global | alvo item | fingerprint/estado do alvo + ciclo de reconciliação |
| Empenho dependente | não possui watermark temporal global confirmado | alvo contrato | fingerprint/estado do alvo + ciclo de reconciliação |

## Recursos dependentes e reconciliação

### Respostas às questões da auditoria

- **Uma contratação alterada deve provocar reconsulta dos itens?** Sim. O
  manual afirma que `dataAtualizacaoGlobal` inclui item, resultado, documento e
  imagem. Reconsultar os itens é a forma segura de materializar a mudança.
- **Um item pode ser atualizado sem alteração visível nos campos principais da
  contratação?** Sim. O payload do pai pode mudar apenas no timestamp global;
  o hash ainda detecta a observação e deve agendar os filhos.
- **Um resultado pode surgir posteriormente?** Sim. Resultados possuem inclusão
  e atualização próprias. A mudança deve refletir no timestamp global da
  contratação; a reconciliação periódica protege contra atraso ou falha.
- **Como descobrir resultados novos em contratações antigas?** Reprocessar os
  filhos das contratações retornadas no feed global e manter uma varredura
  periódica de contratações antigas ainda suscetíveis a resultado. Não marcar
  para sempre um item `temResultado=false` como encerrado.
- **Como identificar alterações em contratos?** Pelo endpoint global
  `/contratos/atualizacao`, usando `dataAtualizacaoGlobal` e identidade
  `numeroControlePNCP`.
- **Como identificar novos empenhos dependentes?** Reconsultar contratos novos
  ou alterados e executar reconciliação periódica. Não foi confirmado que a
  inclusão de um empenho dependente sempre atualize
  `dataAtualizacaoGlobal` do contrato-pai.

### Duas estratégias complementares

1. **Acionada pelo pai**
   - contratação nova/alterada -> listar itens;
   - item novo/alterado ou `temResultado=true` -> listar resultados;
   - contrato novo/alterado -> listar empenhos dependentes;
   - confirmar cada alvo somente depois de persistir todos os filhos.
2. **Reconciliação periódica**
   - faixa quente: pais alterados/publicados recentemente, diariamente;
   - faixa morna: pais ainda ativos/abertos e contratos vigentes, semanalmente;
   - inventário antigo: varredura rotativa mensal ou trimestral, respeitando
     rate limit e orçamento de chamadas.

As faixas exatas são parâmetros operacionais a calibrar. Uma sugestão inicial,
não validada como SLA do PNCP, é 30 dias para a faixa quente e 12 meses para
a faixa morna. O controle deve usar `sync_target_progress`, com fingerprint do
pai e identificador da campanha de reconciliação; não usar apenas a posição
numérica do alvo.

### Estado implementado offline

O runner offline mantém agora uma fila durável em
`sync_reconciliation_target`, separada do progresso de cada janela. Pais
descobertos alimentam a fila; alvos devidos são materializados em uma nova
janela sem reabrir a anterior. A confirmação do alvo e sua próxima data de
consulta são atômicas.

As fixtures distinguem `FOUND`, `EMPTY` e `NOT_FOUND`. A integração HTTP ainda
depende do contrato comum de adaptador para preservar essa distinção a partir
do status real. Em particular, `temResultado=false` não deve retirar o item da
fila: ele é somente uma observação momentânea do payload.

## Compatibilidade com a RAW existente

O snapshot T1 foi aberto em `mode=ro`, passou em `PRAGMA quick_check` e possui:

| Recurso | Registros | Identidades externas distintas |
|---|---:|---:|
| contratação | 2.024 | 2.024 |
| item | 4.804 | 4.804 |
| resultado | 3.669 | 3.669 |
| contrato | 1.378 | 1.378 |
| PCA | 195 | 195 |

Os metadados `source_published_at` e `source_updated_at` estão nulos nesse
snapshot antigo, mas os campos temporais continuam no JSON de contratação,
item, resultado e contrato. O repositório atual possui fallback de leitura do
JSON, preservando compatibilidade. [RAW, CÓDIGO]

O PCA histórico é uma exceção: o payload observado tem apenas `itens`, `uasg`
e `unidadeResponsavel`, enquanto o endpoint de atualização devolve cabeçalho
com `idPcaPncp`, datas, órgão, unidade e `itens`. Embora o `external_id`
histórico use o controle PNCP, a primeira observação pelo novo endpoint terá
hash diferente por mudança de representação. Isso deve ser registrado como
transição de origem, e não interpretado automaticamente como alteração de
negócio.

Nenhuma mudança de esquema é obrigatória para armazenar
`source_updated_at`: a coluna já existe. Uma futura separação entre publicação,
inclusão e evento exigiria proposta de migração própria, fora desta etapa.

## Limitações e riscos

- Não há garantia documentada de ordenação, snapshot consistente entre
  páginas ou cursor composto.
- Não foi encontrado SLA oficial para propagação de atualizações nem garantia
  contra atualizações retroativas.
- Os limites máximos de tamanho de página variam: 50 para contratações e 500
  para PCA, contratos e atas. Recursos dependentes têm outro formato.
- O limite temporal máximo dos endpoints de atualização não está declarado
  no OpenAPI examinado. Usar janelas diárias evita depender desse limite.
- HTTP 204 é resposta normal de consulta vazia. HTTP 404 foi observado como
  ausência de empenhos para um contrato, mas não deve ser tratado globalmente
  como sucesso.
- A ata real retornou data sem hora, divergindo do `date-time` do schema.
- O endpoint global de contratos não filtra UF; uma consulta nacional sem
  sharding por órgão/unidade seria cara e poderia contaminar o recorte RJ.
- A paginação de PCA apresentou sobreposição real entre páginas.
- O comportamento quando dados mudam durante a paginação é **NÃO CONFIRMADO**.
- As consultas foram fotografias pontuais. Não foi acompanhada a mesma
  entidade antes e depois de uma alteração real; propagação, atraso e
  retroatividade permanecem sem garantia empírica.

## Plano recomendado para a Etapa 5

1. Criar um contrato comum de adaptador HTTP que represente `200`, `204`,
   paginação, cursor confirmado e erros sem escrever no banco durante o fetch.
2. Implementar primeiro contratações, pois possuem filtro direto de UF e a
   semântica global mais bem documentada; pilotar uma modalidade e uma janela.
3. Integrar itens e resultados por alvo, com testes de falha entre páginas,
   lista vazia, `temResultado` alterado e reconciliação.
4. Implementar contratos por CNPJ/unidade RJ e validar o escopo de cada payload.
5. Implementar empenhos dependentes e medir se alterações propagam para o
   timestamp global do contrato; manter reconciliação independentemente disso.
6. Implementar atas por CNPJ/unidade e data global.
7. Integrar ao runner o adaptador de PCA já implementado e testado: descoberta
   paginada, deduplicação e busca do plano completo pelo endpoint direto.
8. Executar testes offline e um piloto
   real isolado em `/tmp`; somente depois propor ativação operacional.

### Contrato HTTP comum implementado

`PNCPClient.get_response()` preserva `status_code`, payload, URL, tipo de
conteúdo, quantidade de tentativas e o resultado de transporte. O método
legado `get()` continua devolvendo somente o JSON, mantendo os coletores
históricos compatíveis.

`PNCPIncrementalHTTPAdapter` converte a resposta em uma página tipada e
distingue:

- `FOUND`: HTTP 200 com registros;
- `EMPTY`: HTTP 200 sem registros ou HTTP 204;
- `NOT_FOUND`: HTTP 404 aceito apenas no contexto configurado.

Metadados de página divergentes e registros com formato inválido falham antes
da persistência. O contrato foi testado com transporte simulado e com respostas
reais 200, 204 e 404 em uma cópia isolada.

`PNCPEndpointIncrementalAdapter` implementa sobre esse contrato os feeds de
atualização de contratações, contratos e atas. Ele monta janelas fechadas em
`yyyyMMdd`, exige modalidade+UF para contratações, exige CNPJ do inventário
para contratos/atas, valida a identidade e recusa payload fora do escopo
solicitado. Mudança no total de páginas, página intermediária vazia, página
integral repetida e 404 em feed global interrompem a janela.

`PNCPPCAIncrementalAdapter` usa o feed fragmentado apenas para descoberta e
só emite planos integrais obtidos no endpoint direto, depois de buscar e
validar todos os detalhes. `PNCPRelationshipIncrementalAdapter` consulta itens,
resultados e empenhos por alvo, valida a identidade do pai e devolve
explicitamente `FOUND`, `EMPTY` ou `NOT_FOUND` para a fila de reconciliação.
Ambos permanecem desacoplados do CLI e são testados sem rede.

`run_adapter_incremental()` conecta os adaptadores injetados à RAW e aos
checkpoints v2. Por padrão, o caminho é restrito a SQLite dentro de `/tmp`;
fora dele, o modo operacional exige a confirmação redundante do banco. O
runner repete a janela de endpoint desde a primeira página numa retomada e
exige a confirmação de todos os alvos dependentes antes do watermark. Uma
falha intermediária deixa a janela recuperável e não cria watermark. O CLI
expõe somente a rota protegida de contratações.

`plan_closed_windows()` materializa a sobreposição como política testável. O
padrão atual revisita dois dias confirmados e cria janelas diárias, sem tratar
o timestamp de um registro como cursor. Esses valores foram aprovados como
baseline de piloto e devem ser revistos após 30 dias; não são SLA do PNCP.

`build_scope_inventory()` transforma payloads de contratação do recorte em
um conjunto congelado de CNPJ/unidade. O resultado é deduplicado, possui hash
estável e fornece diretamente os membros do manifesto de cobertura. A função
recusa campos ausentes e UF divergente; ela não pode, sozinha, provar que a RAW
histórica de origem cobriu todos os órgãos do RJ.

Pendências para ampliar a ativação operacional aos demais recursos:

- separação semântica de publicação/inclusão/evento nos metadados;
- comprovação da completude da RAW usada para congelar o inventário do RJ;
- validação empírica de mutação de conteúdo entre páginas;
- confirmação empírica de como empenhos dependentes afetam o contrato-pai;
- planejamento supervisionado da cobertura composta no banco oficial.

A autorização e a migração do banco oficial foram concluídas na Fase 4. As
modalidades 1 a 14 de contratação foram homologadas em `/tmp` na Fase 5.

## Validação desta etapa

- `python3 -m pytest -q`: conferir a contagem atual no relatório de entrega.
- `python3 -m compileall -q src app tests`: concluído sem erro.
- `git diff --check`: concluído sem erro.
- A suíte automatizada não acessa a rede. O piloto real foi executado
  separadamente em banco migrado dentro de `/tmp`.
- Esta seção registra a validação original da etapa. O banco oficial recebeu
  posteriormente as tabelas v2 e a primeira janela controlada, conforme
  `FASE_4_ATIVACAO_INCREMENTAL_OPERACIONAL.md`.
