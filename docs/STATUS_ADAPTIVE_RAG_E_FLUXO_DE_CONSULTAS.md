# Status do Adaptive RAG e Fluxo de Consultas

## Objetivo

Este documento resume a evolução do fluxo de consulta sobre contratações
públicas do PNCP. Ele serve como referência para a equipe entender o que já
está implementado, como testar e quais são as próximas integrações.

O objetivo da solução não é usar apenas busca vetorial. A pergunta passa por
uma análise que decide se ela deve consultar dados estruturados, documentos ou
as duas fontes ao mesmo tempo.

```text
Pergunta
  -> Query Analyzer
  -> Planner / Router
  -> structured | rag | hybrid
  -> resultado com dados, evidências documentais e fontes
```

## O que foi implementado

### 1. Contratos de análise e planejamento

Foram criados schemas Pydantic para representar uma pergunta analisada, seus
filtros e a execução planejada.

Principais elementos:

- `QueryAnalysis`: intenção, entidade, bases, filtros e rota;
- `QueryFilters`: número PNCP, documento, ano, órgão, município, fornecedor,
  modalidade, período e faixa de valor;
- `ExecutionPlan`: ferramentas autorizadas para a rota;
- `StructuredQueryResult`: resultado tabular vindo do SQLite.

As rotas são:

| Rota | Uso |
|---|---|
| `structured` | Perguntas sobre contratos e contratações: valores, anos, modalidades, órgãos e totais. |
| `rag` | Perguntas respondidas por texto de documentos: editais, contratos, termos de referência e atas. |
| `hybrid` | Perguntas que precisam combinar dados SQL e evidências documentais. |

### 2. Query Analyzer

O Analyzer envia a pergunta à LLM com um prompt que exige retorno JSON
validável. Ele não executa banco, índice ou geração de resposta; apenas produz
uma estrutura como:

```json
{
  "route": "structured",
  "intent": "aggregate",
  "entity": "contratacao",
  "bases": ["estruturada"],
  "filters": {"ano": 2026},
  "operations": ["count"]
}
```

O JSON é validado antes de seguir. Assim, a LLM não envia SQL livre para o
banco e não decide diretamente quais funções internas executar.

### 3. Planner / Router determinístico

O Planner recebe o `QueryAnalysis` validado e escolhe as ferramentas previstas
para cada rota:

```text
structured -> structured_database
rag        -> rag_retrieval
hybrid     -> structured_database + rag_retrieval
```

Essa etapa é determinística: não depende de texto livre da LLM.

### 4. Consulta estruturada ao SQLite

Contratos e contratações já persistidos no banco ficam na tabela `raw_asset`,
com o payload original do PNCP em JSON. A Structured Tool consulta essa base
com `json_extract` e usa somente campos permitidos.

Recursos implementados:

- consulta de contratos e contratações;
- filtros por número PNCP, ano, órgão, município, fornecedor, modalidade,
  período e valor;
- `count`, `sum`, `avg`, `min`, `max`, agrupamento e ordenação;
- parâmetros SQL protegidos contra injeção;
- uso da versão mais recente de cada registro PNCP.

Não existe geração de SQL livre pela LLM.

### 5. Recuperação documental RAG

O fluxo documental já usa:

```text
DocumentChunk no SQLite
  -> FTS5 para busca lexical
  -> FAISS para busca semântica
  -> RRF para fusão dos rankings
  -> geração com LLM
```

As respostas documentais incluem chunks e metadados relevantes, como número
PNCP, título do documento e URL de origem quando disponíveis.

### 6. Filtros no retrieval

Os filtros extraídos pelo Analyzer agora também podem ser aplicados aos chunks
documentais. Isso reduz o risco de recuperar um documento de outro contrato
quando a pergunta possui um identificador ou recorte explícito.

Filtros suportados incluem número PNCP, `document_id`, entidade, título do
documento, ano, órgão, município, fornecedor, modalidade, período e valor.

O FTS5 aplica o conjunto permitido de chunks diretamente na consulta. O FAISS
não possui filtro nativo de metadados no índice atual; por isso, quando há
filtros, os resultados vetoriais são restringidos aos IDs autorizados pelo
SQLite. Não há reconstrução do índice nesse processo.

### 7. Rota híbrida

Para a rota `hybrid`, o sistema executa primeiro a consulta estruturada e usa
os mesmos filtros na recuperação documental.

O contexto entregue à LLM mantém duas seções distintas:

```text
DADOS ESTRUTURADOS (SQLite)
  - linhas e agregações SQL

EVIDÊNCIAS DOCUMENTAIS [1], [2], ...
  - chunks vindos de FTS5 + FAISS + RRF
```

As citações `[1]`, `[2]` referem-se somente aos documentos. Dados SQL não são
tratados como se fossem um documento citado.

### 8. Segurança, resposta e auditoria

O gerador possui guardrails antes da recuperação, regras para resposta curta,
citações numéricas e orientação para não relacionar evidências de processos
diferentes sem sustentação explícita.

Os traces de auditoria registram, entre outros dados:

- pergunta e `top_k`;
- filtros usados;
- chunks recuperados e seus scores;
- contexto e prompt usados;
- modelo, resposta, status e tempo;
- resultado estruturado quando a rota é híbrida.

## Endpoints disponíveis

Todos os endpoints abaixo exigem autenticação configurada pela API.

| Método | Endpoint | Finalidade |
|---|---|---|
| `POST` | `/query/generate` | Fluxo documental legado: FTS5 + FAISS + RRF + LLM, sem Analyzer. |
| `POST` | `/query/structured` | Consulta somente a base estruturada, passando pelo Analyzer e Planner. |
| `POST` | `/query/ask` | Endpoint principal do Adaptive RAG: decide entre `structured`, `rag` e `hybrid`. |

Exemplo de corpo para `/query/ask`:

```json
{
  "pergunta": "Quais contratos existem em 2022?",
  "top_k": 5
}
```

Exemplos de perguntas para teste:

```text
Quantos contratos existem em 2022?
Quais documentos são exigidos na fase de habilitação?
Quais contratos de 2022 possuem informações documentais sobre vigência?
```

## Como testar manualmente

1. Confirmar que o banco possui os dados estruturados e os chunks desejados.
2. Confirmar que o índice FAISS corresponde aos `document_chunks` existentes.
3. Iniciar a API:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

4. Abrir o Swagger:

```text
http://127.0.0.1:8000/docs
```

5. Autenticar-se conforme o fluxo já configurado pela aplicação.
6. Testar primeiro cada endpoint isolado e depois `/query/ask` com uma pergunta
   de cada rota.
7. Conferir se:
   - a rota retornada corresponde à pergunta;
   - filtros foram respeitados;
   - fontes documentais pertencem ao recorte esperado;
   - dados SQL e fontes documentais aparecem separados na resposta híbrida.

## Validações já realizadas

Foram executados testes unitários e de integração cobrindo schemas, Analyzer
simulado, Planner, Structured Tool, filtros, FTS5, FAISS, RRF, geração,
endpoints e rota híbrida.

Na última validação consolidada, **59 testes passaram**.

Também foram feitos testes de leitura no `app.db` com dados reais para validar
a ponte entre a `Session` da API, a tabela `raw_asset` e a consulta
estruturada. As chamadas externas reais da LLM para classificar perguntas no
Swagger ainda devem ser validadas manualmente pela equipe.

## Estado do banco e dos índices

- `app.db` contém os dados estruturados e os documentos/chunks disponíveis no
  ambiente local.
- O FAISS guarda vetores e `chunk_id`; o SQLite continua sendo a fonte de
  verdade para texto e metadados.
- Antes de compartilhar ou versionar artefatos, a equipe deve decidir como
  distribuir o banco e o diretório de índices, garantindo que ambos sejam da
  mesma carga de chunks.
- A inicialização atual aplica a migração local já existente para a coluna
  `documents.title` quando necessário.

## Pendências para fechar a rastreabilidade ponta a ponta

1. Validar no Swagger, com LLM real, perguntas de rota `structured`, `rag` e
   `hybrid`; ajustar o prompt do Analyzer se a classificação não for adequada.
2. Relacionar formalmente no banco o `trace_id` de auditoria com o `run_id` da
   Query, a resposta, o plano, filtros e fontes utilizadas.
3. Persistir consultas somente estruturadas no histórico, caso a equipe queira
   que elas também apareçam junto das respostas RAG.
4. Definir uma estratégia de compartilhamento/versionamento para `app.db`,
   FAISS e manifestos de corpus.
5. Criar um conjunto fixo de perguntas de avaliação com rota esperada, fontes
   esperadas e critérios de qualidade.

## Melhorias futuras

- Normalizar contratos e contratações em tabelas relacionais, reduzindo a
  dependência de `json_extract` em consultas analíticas maiores.
- Implementar filtragem vetorial nativa ou índices FAISS por recorte, para
  evitar varrer o índice inteiro quando houver filtros de metadados.
- Adicionar reranking sobre os candidatos do RRF.
- Tornar os traces e eventos de pipeline consultáveis pela API.
- Evoluir o endpoint para apresentar, de modo mais amigável, a origem de cada
  dado estruturado e de cada evidência documental.
