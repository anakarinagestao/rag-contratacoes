# Engenharia de Software

## Sistema Adaptive RAG --- Contratações Públicas do Estado do Rio de Janeiro

**Documento:** Engenharia de Software\
**Cenário:** Contratações Públicas do Estado do Rio de Janeiro\
**Arquitetura principal:** FastAPI + módulos de domínio + Adaptive RAG\
**Interface da API:** Swagger/OpenAPI\
**Versão:** 2.0\
**Data:** 16/09/2026

------------------------------------------------------------------------

# 1. Introdução

Este documento detalha a Engenharia de Software do sistema definido na
Análise de Requisitos para o cenário **Contratações Públicas do Estado
do Rio de Janeiro**.

A solução será desenvolvida como uma aplicação backend em **FastAPI**,
organizada por módulos de domínio. Cada módulo deverá possuir separação
interna entre:

``` text
model
schemas
services
routes
```

A aplicação principal será inicializada em:

``` text
app/main.py
```

As rotas FastAPI serão registradas na aplicação principal e
automaticamente documentadas por meio do padrão **OpenAPI**, utilizando
a interface interativa **Swagger UI**.

O núcleo inteligente do sistema será um pipeline de **Adaptive
Retrieval-Augmented Generation (Adaptive RAG)** capaz de analisar a
pergunta, identificar o tipo de evidência necessário, selecionar uma ou
mais bases, aplicar filtros, realizar recuperação híbrida e produzir uma
resposta fundamentada.

------------------------------------------------------------------------

# 2. Objetivo da Engenharia de Software

Transformar os requisitos funcionais e não funcionais do projeto em uma
arquitetura implementável, modular, testável e rastreável.

Os principais objetivos técnicos são:

-   organizar o backend por módulos;
-   separar models, schemas, services e routes;
-   centralizar a inicialização em `app/main.py`;
-   disponibilizar endpoints REST com FastAPI;
-   documentar e testar os endpoints pelo Swagger;
-   implementar Adaptive RAG;
-   suportar quatro naturezas de evidência;
-   implementar Dense Retrieval e BM25;
-   combinar rankings com RRF;
-   aplicar reranking;
-   aplicar guardrails antes da geração;
-   validar respostas com Pydantic;
-   persistir histórico e feedback;
-   versionar e reproduzir o corpus;
-   implementar observabilidade;
-   possibilitar testes unitários, de contrato e integração.

------------------------------------------------------------------------

# 3. Visão Geral da Arquitetura

A solução será dividida em:

``` text
Cliente / Swagger
        |
        v
FastAPI - app/main.py
        |
        v
Routes
        |
        v
Services
        |
        +-----------------------------+
        |                             |
        v                             v
Models / Persistência          Pipeline Adaptive RAG
                                      |
                                      v
                                Query Analyzer
                                      |
                                      v
                                Adaptive Router
                                      |
                                      v
                           Dense + BM25 + Filtros
                                      |
                                      v
                                     RRF
                                      |
                                      v
                                  Reranking
                                      |
                                      v
                                  Guardrails
                                      |
                                      v
                                   Geração
                                      |
                                      v
                                  Validação
                                      |
                                      v
                             Resposta + Citações
```

------------------------------------------------------------------------

# 4. Estrutura de Pastas do Projeto

A estrutura oficial proposta para o projeto será:

``` text
contratacoes-rj-rag/
│
├── app/
│   │
│   ├── __init__.py
│   ├── main.py
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── exceptions.py
│   │   └── logging.py
│   │
│   └── modules/
│       │
│       ├── query/
│       │   ├── __init__.py
│       │   ├── model.py
│       │   ├── schemas.py
│       │   ├── services.py
│       │   └── routes.py
│       │
│       ├── rag/
│       │   ├── __init__.py
│       │   ├── model.py
│       │   ├── schemas.py
│       │   ├── services.py
│       │   └── routes.py
│       │
│       ├── corpus/
│       │   ├── __init__.py
│       │   ├── model.py
│       │   ├── schemas.py
│       │   ├── services.py
│       │   └── routes.py
│       │
│       ├── documents/
│       │   ├── __init__.py
│       │   ├── model.py
│       │   ├── schemas.py
│       │   ├── services.py
│       │   └── routes.py
│       │
│       ├── feedback/
│       │   ├── __init__.py
│       │   ├── model.py
│       │   ├── schemas.py
│       │   ├── services.py
│       │   └── routes.py
│       │
│       └── history/
│           ├── __init__.py
│           ├── model.py
│           ├── schemas.py
│           ├── services.py
│           └── routes.py
│
├── src/
│   ├── __init__.py
│   ├── fetch_corpus.py
│   ├── ingest.py
│   ├── index.py
│   ├── router.py
│   ├── retrieve.py
│   ├── generate.py
│   ├── schema.py
│   └── obs.py
│
├── notes/
│   ├── ANALISE_DE_REQUISITOS_CONTRATACOES_PUBLICAS_RJ.md
│   ├── ENGENHARIA_DE_SOFTWARE_CONTRATACOES_PUBLICAS_RJ.md
│   └── AGENTS.md
│
├── data/
│   ├── raw/
│   │   ├── v1/
│   │   └── v2/
│   ├── processed/
│   ├── cache/
│   └── indexes/
│
├── eval/
│   ├── questions.json
│   ├── benchmark.py
│   └── results/
│
├── logs/
│
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── contract/
│   └── integration/
│
├── corpus_manifest.yaml
├── CORPUS_INVENTORY.md
├── ARQUITETURA.md
├── PERSONAS.md
├── README.md
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
└── pyproject.toml
```

------------------------------------------------------------------------

# 5. Pasta `notes`

A pasta:

``` text
notes/
```

será responsável pela documentação interna de engenharia e orientação
dos agentes.

Estrutura:

``` text
notes/
├── ANALISE_DE_REQUISITOS_CONTRATACOES_PUBLICAS_RJ.md
├── ENGENHARIA_DE_SOFTWARE_CONTRATACOES_PUBLICAS_RJ.md
└── AGENTS.md
```

## 5.1 Análise de Requisitos

``` text
notes/ANALISE_DE_REQUISITOS_CONTRATACOES_PUBLICAS_RJ.md
```

Define:

-   escopo;
-   atores;
-   personas;
-   requisitos funcionais;
-   requisitos não funcionais;
-   regras de negócio;
-   casos de uso;
-   critérios de aceite;
-   restrições;
-   riscos.

## 5.2 Engenharia de Software

``` text
notes/ENGENHARIA_DE_SOFTWARE_CONTRATACOES_PUBLICAS_RJ.md
```

Define:

-   arquitetura;
-   módulos;
-   responsabilidades;
-   fluxos;
-   contratos;
-   persistência;
-   RAG;
-   testes;
-   Swagger;
-   padrões de desenvolvimento.

## 5.3 AGENTS

``` text
notes/AGENTS.md
```

Define instruções para agentes de desenvolvimento, como Codex e outros
assistentes de IA que atuem no repositório.

------------------------------------------------------------------------

# 6. Arquitetura Modular

A aplicação utilizará organização orientada a módulos de domínio.

Cada módulo deverá seguir:

``` text
app/modules/<modulo>/
├── __init__.py
├── model.py
├── schemas.py
├── services.py
└── routes.py
```

Essa estrutura deve ser mantida como padrão.

------------------------------------------------------------------------

# 7. Responsabilidade de `model.py`

O arquivo:

``` text
model.py
```

representará principalmente entidades persistentes do módulo.

Quando utilizado SQLAlchemy, nele ficarão classes ORM.

Exemplo conceitual:

``` python
class QueryModel(Base):
    __tablename__ = "queries"

    id = ...
    run_id = ...
    text = ...
    created_at = ...
```

O model não deverá conter regras extensas de orquestração RAG.

------------------------------------------------------------------------

# 8. Responsabilidade de `schemas.py`

O arquivo:

``` text
schemas.py
```

deverá possuir schemas Pydantic para:

-   entrada;
-   saída;
-   filtros;
-   validações;
-   contratos da API.

Exemplo:

``` python
from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str


class QueryResponse(BaseModel):
    query_id: str
    run_id: str
    answer: str
    routes: list[str]
    sources_used: list[dict]
```

Os schemas também serão utilizados pelo FastAPI para gerar
automaticamente a documentação OpenAPI/Swagger.

------------------------------------------------------------------------

# 9. Responsabilidade de `services.py`

O arquivo:

``` text
services.py
```

conterá as regras de negócio e orquestração do módulo.

Fluxo típico:

``` text
Route
  |
  v
Service
  |
  +--> Model / Banco
  |
  +--> Pipeline RAG
  |
  +--> Integrações
```

As routes não deverão implementar toda a lógica diretamente.

------------------------------------------------------------------------

# 10. Responsabilidade de `routes.py`

O arquivo:

``` text
routes.py
```

será responsável pela interface HTTP.

Exemplo:

``` python
from fastapi import APIRouter

router = APIRouter(
    prefix="/queries",
    tags=["Queries"],
)
```

As routes deverão:

1.  receber requisições;
2.  validar dados pelos schemas;
3.  chamar services;
4.  retornar schemas de resposta;
5.  declarar códigos HTTP adequados.

------------------------------------------------------------------------

# 11. Módulo `query`

Estrutura:

``` text
app/modules/query/
├── model.py
├── schemas.py
├── services.py
└── routes.py
```

Responsável pelo ciclo de consulta do usuário.

## Model

Poderá persistir:

``` text
query_id
run_id
texto
created_at
```

## Schemas

Exemplos:

``` text
QueryRequest
QueryFilters
QueryAnalysis
QueryResponse
```

## Services

Responsável por iniciar a execução do pipeline.

## Routes

Exemplo conceitual:

``` http
POST /queries
```

------------------------------------------------------------------------

# 12. Módulo `rag`

Estrutura:

``` text
app/modules/rag/
├── model.py
├── schemas.py
├── services.py
└── routes.py
```

Responsável pela integração entre a aplicação FastAPI e o pipeline
Adaptive RAG.

O `services.py` desse módulo deverá coordenar:

``` text
Query Analyzer
Adaptive Router
Retriever
RRF
Rerank
Guardrails
Generator
Validator
```

------------------------------------------------------------------------

# 13. Módulo `corpus`

Estrutura:

``` text
app/modules/corpus/
├── model.py
├── schemas.py
├── services.py
└── routes.py
```

Responsável pelas informações e operações relacionadas ao corpus.

Poderá disponibilizar operações administrativas como:

``` text
inventário
versões
status
```

A coleta pesada poderá continuar sendo executada por:

``` text
src/fetch_corpus.py
```

------------------------------------------------------------------------

# 14. Módulo `documents`

Responsável pelos documentos e chunks processados.

Estrutura:

``` text
app/modules/documents/
├── model.py
├── schemas.py
├── services.py
└── routes.py
```

Poderá representar:

``` text
CorpusDocument
DocumentChunk
SourceReference
```

------------------------------------------------------------------------

# 15. Módulo `feedback`

Estrutura:

``` text
app/modules/feedback/
├── model.py
├── schemas.py
├── services.py
└── routes.py
```

Responsável pelo feedback do usuário.

Dados possíveis:

``` text
feedback_id
query_id
rating
comment
created_at
```

------------------------------------------------------------------------

# 16. Módulo `history`

Estrutura:

``` text
app/modules/history/
├── model.py
├── schemas.py
├── services.py
└── routes.py
```

Responsável por consultas e respostas anteriormente executadas.

------------------------------------------------------------------------

# 17. `app/main.py`

O arquivo:

``` text
app/main.py
```

será o ponto principal de inicialização da aplicação FastAPI.

Responsabilidades:

-   criar a aplicação;
-   configurar título;
-   configurar versão;
-   configurar descrição;
-   registrar routers;
-   disponibilizar Swagger;
-   disponibilizar OpenAPI;
-   configurar eventos da aplicação;
-   configurar handlers globais.

Exemplo conceitual:

``` python
from fastapi import FastAPI

from app.modules.query.routes import router as query_router
from app.modules.rag.routes import router as rag_router
from app.modules.corpus.routes import router as corpus_router
from app.modules.documents.routes import router as documents_router
from app.modules.feedback.routes import router as feedback_router
from app.modules.history.routes import router as history_router


app = FastAPI(
    title="Contratações Públicas RJ - Adaptive RAG API",
    description=(
        "API para consultas inteligentes sobre contratações "
        "públicas do Estado do Rio de Janeiro."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


app.include_router(query_router)
app.include_router(rag_router)
app.include_router(corpus_router)
app.include_router(documents_router)
app.include_router(feedback_router)
app.include_router(history_router)


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
```

------------------------------------------------------------------------

# 18. Swagger

O FastAPI gera documentação OpenAPI automaticamente.

Com a aplicação executando, a interface Swagger deverá estar disponível
em:

``` text
http://localhost:8000/docs
```

O schema OpenAPI estará em:

``` text
http://localhost:8000/openapi.json
```

E a documentação alternativa ReDoc poderá ficar em:

``` text
http://localhost:8000/redoc
```

------------------------------------------------------------------------

# 19. Execução da Aplicação

Em desenvolvimento:

``` bash
uvicorn app.main:app --reload
```

Ou:

``` bash
python -m uvicorn app.main:app --reload
```

A aplicação deverá iniciar em:

``` text
http://127.0.0.1:8000
```

Swagger:

``` text
http://127.0.0.1:8000/docs
```

------------------------------------------------------------------------

# 20. Organização dos Endpoints no Swagger

O Swagger deverá utilizar tags para separar os módulos.

Exemplo:

``` text
Health
Queries
RAG
Corpus
Documents
Feedback
History
```

Isso permitirá visualizar a API por responsabilidade.

------------------------------------------------------------------------

# 21. Endpoints Conceituais

A implementação poderá adotar contratos como:

``` text
GET  /health

POST /queries
GET  /queries/{query_id}

POST /rag/ask

GET  /corpus/versions
GET  /corpus/inventory

GET  /documents/{document_id}

POST /feedback
GET  /feedback/{query_id}

GET  /history
GET  /history/{query_id}
```

Esses endpoints representam a proposta arquitetural e devem ser
implementados ou ajustados de acordo com os requisitos efetivamente
consolidados.

------------------------------------------------------------------------

# 22. Fluxo Swagger → Aplicação

``` text
Swagger UI
    |
    | POST /rag/ask
    v
routes.py
    |
    v
schemas.py
    |
    | valida request
    v
services.py
    |
    v
Adaptive RAG
    |
    v
schemas.py
    |
    | valida response
    v
FastAPI
    |
    v
Swagger UI
```

------------------------------------------------------------------------

# 23. Pipeline Adaptive RAG

O pipeline principal continuará separado da camada HTTP.

Estrutura:

``` text
src/
├── fetch_corpus.py
├── ingest.py
├── index.py
├── router.py
├── retrieve.py
├── generate.py
├── schema.py
└── obs.py
```

O diretório `src/` implementa componentes técnicos reutilizáveis.

O diretório:

``` text
app/modules/
```

implementa os módulos da aplicação e integra esses componentes à API.

------------------------------------------------------------------------

# 24. Query Analyzer

O Query Analyzer será responsável por interpretar a consulta.

Exemplo:

``` text
Quais contratos de licenciamento de software foram
assinados por órgãos estaduais do RJ acima de
R$ 500 mil em 2025?
```

Resultado conceitual:

``` json
{
  "intent": "buscar_contratos",
  "semantic_query": "licenciamento de software",
  "evidence_types": [
    "estruturada"
  ],
  "filters": {
    "uf": "RJ",
    "ano": 2025,
    "valor_minimo": 500000
  }
}
```

------------------------------------------------------------------------

# 25. Adaptive Router

Rotas possíveis:

``` text
normativa
estruturada
conversacional
agregada
```

O roteador deverá selecionar evidências, não apenas palavras-chave.

Uma consulta poderá selecionar múltiplas rotas.

------------------------------------------------------------------------

# 26. Recuperação Híbrida

A recuperação utilizará:

``` text
Dense Retrieval
+
BM25
```

## Dense

Adequado para significado semântico.

## BM25

Adequado para:

``` text
CNPJ
número de contrato
número de edital
nome
identificador
expressão exata
```

------------------------------------------------------------------------

# 27. Reciprocal Rank Fusion

Resultados Dense e BM25 serão combinados por RRF.

``` text
Dense ranking ----+
                  |
                  +--> RRF --> ranking combinado
                  |
BM25 ranking -----+
```

A função deverá ser determinística e testável.

------------------------------------------------------------------------

# 28. Reranking

Após RRF:

``` text
Top candidatos
      |
      v
Reranker
      |
      v
Contexto final
```

O reranker não deverá receber todo o corpus.

------------------------------------------------------------------------

# 29. Guardrails

Antes da geração:

``` text
chunks
  |
  v
sensitivity
  |
  +--> publico ------> permitido
  |
  +--> mascarável ---> sanitização
  |
  +--> restrito -----> bloqueio
```

Permitido:

``` text
valor de contrato
nome de empresa
CNPJ de pessoa jurídica
objeto contratado
```

Mascarar:

``` text
CPF
telefone pessoal
e-mail pessoal
```

Bloquear:

``` text
dados bancários
credenciais
dados restritos
```

------------------------------------------------------------------------

# 30. Geração

O gerador deverá receber apenas evidências autorizadas.

``` text
pergunta
+
contexto recuperado
+
instruções
        |
        v
       LLM
        |
        v
resposta fundamentada
```

O modelo não deverá inventar fontes.

------------------------------------------------------------------------

# 31. Validação

A resposta será validada com Pydantic.

Exemplo:

``` python
class SourceReference(BaseModel):
    base_id: str
    source_file: str
    chunk_id: str
    source_url: str | None = None


class RAGResponse(BaseModel):
    query_id: str
    run_id: str
    answer: str
    routes: list[str]
    sources_used: list[SourceReference]
    is_refusal: bool = False
```

Esse schema também poderá ser exibido automaticamente no Swagger.

------------------------------------------------------------------------

# 32. Banco de Dados

Caso o projeto utilize SQLAlchemy, a camada de persistência ficará
integrada aos `model.py`.

Modelo conceitual:

``` text
queries
responses
response_sources
feedback
pipeline_events
```

Relacionamentos:

``` text
Query
  |
  +---- 1:N ---- ResponseSource
  |
  +---- 1:1 ---- Response
  |
  +---- 1:N ---- Feedback
```

------------------------------------------------------------------------

# 33. Fluxo de Persistência

``` text
POST /rag/ask
     |
     v
QueryService
     |
     +--> salva Query
     |
     v
RAGService
     |
     v
gera resposta
     |
     +--> salva Response
     |
     +--> salva Sources
     |
     v
retorna QueryResponse
```

------------------------------------------------------------------------

# 34. Corpus

O arquivo:

``` text
corpus_manifest.yaml
```

será a fonte de verdade da composição do corpus.

Operações:

``` bash
python -m src.fetch_corpus fetch --manifest corpus_manifest.yaml
python -m src.fetch_corpus update --manifest corpus_manifest.yaml
python -m src.fetch_corpus inventory --version v1
```

------------------------------------------------------------------------

# 35. Versionamento

``` text
data/raw/
├── v1/
├── v2/
└── v3/
```

Uma nova atualização cria uma nova versão.

Versões anteriores não devem ser sobrescritas.

------------------------------------------------------------------------

# 36. Ingestão

Pipeline:

``` text
arquivo
  |
  v
loader
  |
  v
normalização
  |
  v
metadados
  |
  v
sensitivity
  |
  v
chunking
  |
  v
DocumentChunk[]
```

Metadados obrigatórios:

``` text
source_file
doc_type
chunk_id
sensitivity
base_id
```

------------------------------------------------------------------------

# 37. Indexação

``` text
DocumentChunk[]
      |
      +---------------------+
      |                     |
      v                     v
Embeddings                Tokens
      |                     |
      v                     v
Vector Index             BM25 Index
```

Os índices deverão ser persistidos em:

``` text
data/indexes/
```

ou localização equivalente configurada.

------------------------------------------------------------------------

# 38. Cache

Embeddings deverão utilizar cache.

Estrutura possível:

``` text
data/cache/
└── embeddings/
```

Chave conceitual:

``` text
hash(conteúdo + modelo_embedding)
```

------------------------------------------------------------------------

# 39. Observabilidade

Logs:

``` text
logs/pipeline-AAAA-MM-DD.jsonl
```

Estágios:

``` text
query_analysis
roteamento
retrieval_denso
retrieval_bm25
fusao_rrf
rerank
geracao
validacao
feedback
```

Campos:

``` text
ts
run_id
query_id
stage
status
latency_ms
input
output
tokens
model
extra
```

------------------------------------------------------------------------

# 40. Testes

Estrutura:

``` text
tests/
├── fixtures/
├── unit/
├── contract/
└── integration/
```

## Unitários

Testar:

``` text
RRF
filtros
schemas
chunking
mascaramento
manifesto
serialização
```

## Contrato

Validar:

``` text
metadados obrigatórios
schemas de resposta
rotas válidas
sensitivity
sources_used
```

## Integração

Testar fluxo:

``` text
FastAPI
 -> Service
 -> RAG
 -> Response
```

------------------------------------------------------------------------

# 41. Testes da API

O FastAPI poderá utilizar `TestClient`.

Exemplo:

``` python
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

------------------------------------------------------------------------

# 42. Swagger como Ferramenta de Teste Manual

Durante o desenvolvimento, o Swagger poderá ser utilizado para:

-   visualizar endpoints;
-   consultar schemas;
-   enviar JSON;
-   testar filtros;
-   visualizar status HTTP;
-   analisar respostas;
-   validar contratos de entrada e saída.

Swagger não substitui testes automatizados.

------------------------------------------------------------------------

# 43. Exemplo de Endpoint RAG

``` python
from fastapi import APIRouter, Depends

from .schemas import RAGRequest, RAGResponse
from .services import RAGService


router = APIRouter(
    prefix="/rag",
    tags=["RAG"],
)


@router.post(
    "/ask",
    response_model=RAGResponse,
    summary="Realizar consulta Adaptive RAG",
)
async def ask(
    request: RAGRequest,
    service: RAGService = Depends(),
) -> RAGResponse:
    return await service.ask(request)
```

No Swagger aparecerá:

``` text
RAG
POST /rag/ask
```

com os schemas de request e response.

------------------------------------------------------------------------

# 44. Exemplo de Service

``` python
class RAGService:

    async def ask(self, request: RAGRequest) -> RAGResponse:

        analysis = await query_analyzer.analyze(request.query)

        routing = await adaptive_router.route(analysis)

        candidates = await retriever.retrieve(
            query=request.query,
            routing=routing,
            filters=analysis.filters,
        )

        fused = reciprocal_rank_fusion(candidates)

        ranked = await reranker.rerank(
            request.query,
            fused,
        )

        safe_context = guardrails.apply(ranked)

        generated = await generator.generate(
            query=request.query,
            context=safe_context,
        )

        return validator.validate(generated)
```

O código é uma referência arquitetural. A implementação deverá utilizar
as classes e contratos realmente existentes no projeto.

------------------------------------------------------------------------

# 45. Diagrama de Sequência da API

``` mermaid
sequenceDiagram
    actor U as Usuário/Swagger
    participant API as FastAPI
    participant RT as routes.py
    participant SV as services.py
    participant QA as Query Analyzer
    participant AR as Adaptive Router
    participant RE as Retriever
    participant RR as RRF/Rerank
    participant GU as Guardrails
    participant LLM as LLM
    participant DB as Persistência

    U->>API: POST /rag/ask
    API->>RT: RAGRequest
    RT->>SV: ask()
    SV->>DB: salvar consulta
    SV->>QA: analyze()
    QA-->>SV: QueryAnalysis
    SV->>AR: route()
    AR-->>SV: RoutingDecision
    SV->>RE: retrieve()
    RE-->>SV: candidatos
    SV->>RR: fusion + rerank
    RR-->>SV: contexto
    SV->>GU: apply()
    GU-->>SV: contexto seguro
    SV->>LLM: generate()
    LLM-->>SV: resposta
    SV->>DB: salvar resposta/fontes
    SV-->>RT: RAGResponse
    RT-->>API: HTTP 200
    API-->>U: JSON + fontes
```

------------------------------------------------------------------------

# 46. Diagrama de Componentes

``` mermaid
flowchart LR

SW[Swagger UI] --> MAIN[app/main.py]

MAIN --> QR[Query Routes]
MAIN --> RR[RAG Routes]
MAIN --> CR[Corpus Routes]
MAIN --> DR[Documents Routes]
MAIN --> FR[Feedback Routes]
MAIN --> HR[History Routes]

QR --> QS[Query Services]
RR --> RS[RAG Services]
CR --> CS[Corpus Services]
DR --> DS[Document Services]
FR --> FS[Feedback Services]
HR --> HS[History Services]

RS --> QA[Query Analyzer]
RS --> ROUTER[Adaptive Router]
RS --> RET[Retriever]

RET --> DENSE[Dense]
RET --> BM25[BM25]

DENSE --> RRF[RRF]
BM25 --> RRF

RRF --> RERANK[Rerank]
RERANK --> GUARD[Guardrails]
GUARD --> GEN[Generator]
GEN --> VALID[Validator]
```

------------------------------------------------------------------------

# 47. Dependências entre Camadas

A dependência recomendada será:

``` text
routes
   |
   v
services
   |
   +------> models
   |
   +------> schemas
   |
   +------> src / infraestrutura
```

Evitar:

``` text
model -> route
schema -> route
src -> FastAPI route
```

O núcleo de recuperação não deverá depender da interface HTTP.

------------------------------------------------------------------------

# 48. Tratamento de Erros

Exceções de domínio poderão ser centralizadas em:

``` text
app/core/exceptions.py
```

Exemplos:

``` text
CorpusError
SourceUnavailableError
ManifestValidationError
IndexNotFoundError
QueryAnalysisError
RetrievalError
ModelUnavailableError
ResponseValidationError
SecurityPolicyError
```

O FastAPI deverá converter erros em respostas HTTP apropriadas.

------------------------------------------------------------------------

# 49. Configuração

Arquivo:

``` text
app/core/config.py
```

Poderá utilizar `pydantic-settings`.

Variáveis:

``` text
OLLAMA_URL
GENERATION_MODEL
EMBEDDING_MODEL
RERANK_MODEL
DATABASE_URL
DATA_DIR
INDEX_DIR
LOG_DIR
TOP_K_DENSE
TOP_K_BM25
TOP_K_RERANK
RRF_K
```

------------------------------------------------------------------------

# 50. Modelos de IA

Referência do ambiente do desafio:

``` text
OLLAMA_URL=http://ecoa-llm.duckdns.org:11434/v1
```

Geração:

``` text
qcwind/qwen3-8b-instruct-Q4-K-M
```

Embedding:

``` text
xagi/qwen3-embedding:0.6b-q4_k_m
```

O reranker deverá ser configurável.

------------------------------------------------------------------------

# 51. Segurança

Nunca versionar:

``` text
.env
credenciais
tokens
senhas
dados brutos sensíveis
```

O `.env.example` deverá possuir apenas nomes das variáveis.

------------------------------------------------------------------------

# 52. `.gitignore`

Deverá considerar:

``` text
.env
.venv/
__pycache__/
.pytest_cache/
data/raw/
data/cache/
data/indexes/
logs/
*.pyc
```

A política final deverá refletir quais artefatos o projeto precisa ou
não versionar.

------------------------------------------------------------------------

# 53. Rastreabilidade

Relação entre requisitos e arquitetura:

  Área                   Componente
  ---------------------- ----------------------------------------------
  Consulta               `app/modules/query`
  Adaptive RAG           `app/modules/rag`
  Corpus                 `app/modules/corpus` + `src/fetch_corpus.py`
  Documentos             `app/modules/documents`
  Histórico              `app/modules/history`
  Feedback               `app/modules/feedback`
  Query Analyzer         `src/router.py`
  Adaptive Router        `src/router.py`
  Dense/BM25             `src/retrieve.py`
  RRF/Rerank             `src/retrieve.py`
  Geração                `src/generate.py`
  Schemas RAG internos   `src/schema.py`
  Observabilidade        `src/obs.py`
  API/OpenAPI            `app/main.py` + `routes.py`

------------------------------------------------------------------------

# 54. Fluxo Completo do Sistema

``` text
                 USUÁRIO / SWAGGER
                         |
                         v
                  POST /rag/ask
                         |
                         v
                 app/main.py
                         |
                         v
              rag/routes.py
                         |
                         v
             rag/services.py
                         |
                         v
                  Query Analyzer
                         |
                         v
                  Adaptive Router
                         |
           +-------------+-------------+
           |             |             |
           v             v             v
       Normativa     Estruturada   Conversacional
           |             |             |
           +-------------+-------------+
                         |
                     Agregada
                         |
                         v
                 Aplicação de filtros
                         |
               +---------+---------+
               |                   |
               v                   v
             Dense                BM25
               |                   |
               +---------+---------+
                         |
                         v
                        RRF
                         |
                         v
                     Reranking
                         |
                         v
                     Guardrails
                         |
                         v
                      Geração
                         |
                         v
                     Validação
                         |
                         v
                  Persistência
                         |
                         v
                 RAGResponse
                         |
                         v
                     Swagger
```

------------------------------------------------------------------------

# 55. Ordem Recomendada de Implementação

## Etapa 1

Criar:

``` text
app/main.py
app/core/
app/modules/
```

e subir FastAPI com `/health` e Swagger.

## Etapa 2

Criar módulos:

``` text
query
rag
corpus
documents
feedback
history
```

cada um contendo:

``` text
model.py
schemas.py
services.py
routes.py
```

## Etapa 3

Implementar schemas e contratos.

## Etapa 4

Implementar corpus e ingestão.

## Etapa 5

Implementar Dense e BM25.

## Etapa 6

Implementar RRF.

## Etapa 7

Implementar Query Analyzer e Adaptive Router.

## Etapa 8

Implementar reranking.

## Etapa 9

Implementar guardrails e geração.

## Etapa 10

Integrar tudo ao `RAGService`.

## Etapa 11

Persistir histórico e feedback.

## Etapa 12

Criar testes e benchmark.

------------------------------------------------------------------------

# 56. Definition of Done

Uma funcionalidade deverá ser considerada concluída quando:

``` text
[ ] possui schema de entrada/saída quando aplicável
[ ] possui service
[ ] possui route quando exposta pela API
[ ] aparece corretamente no Swagger
[ ] possui tratamento de erro
[ ] possui testes
[ ] respeita observabilidade
[ ] respeita sensibilidade
[ ] não expõe segredo
[ ] mantém separação model/schemas/services/routes
[ ] mantém documentação coerente
```

------------------------------------------------------------------------

# 57. Regra Arquitetural Principal

Todo módulo da aplicação deverá seguir:

``` text
model.py
schemas.py
services.py
routes.py
```

O fluxo padrão deverá ser:

``` text
HTTP
 ↓
routes
 ↓
schemas
 ↓
services
 ↓
models / src / integrações
 ↓
schemas
 ↓
HTTP
```

O `app/main.py` será responsável por montar a aplicação FastAPI e
registrar os routers.

O Swagger será a interface principal de documentação e teste manual da
API durante o desenvolvimento.

------------------------------------------------------------------------

# 58. Conclusão

A arquitetura revisada organiza o projeto em duas áreas complementares.

A primeira é a aplicação:

``` text
app/
```

com:

``` text
main.py
core/
modules/
```

Cada módulo de negócio utiliza:

``` text
model.py
schemas.py
services.py
routes.py
```

A segunda é o núcleo técnico do Adaptive RAG:

``` text
src/
```

responsável por:

``` text
coleta
ingestão
indexação
roteamento
recuperação
RRF
reranking
geração
schemas internos
observabilidade
```

A documentação de engenharia fica centralizada em:

``` text
notes/
├── ANALISE_DE_REQUISITOS_CONTRATACOES_PUBLICAS_RJ.md
├── ENGENHARIA_DE_SOFTWARE_CONTRATACOES_PUBLICAS_RJ.md
└── AGENTS.md
```

Essa estrutura permite que a aplicação FastAPI permaneça organizada por
domínio enquanto os componentes de RAG permanecem desacoplados da
interface HTTP.

O resultado esperado é uma aplicação que possa ser executada com:

``` bash
python -m uvicorn app.main:app --reload
```

e inspecionada/testada pelo Swagger em:

``` text
http://127.0.0.1:8000/docs
```

mantendo o pipeline:

``` text
Query Analyzer
→ Adaptive Router
→ filtros
→ Dense + BM25
→ RRF
→ Rerank
→ Guardrails
→ Geração
→ Validação
→ Resposta com fontes
```
