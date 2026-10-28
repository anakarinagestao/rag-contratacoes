# ARQUITETURA.md

# Arquitetura Proposta — Adaptive RAG
## Contratações Públicas do Estado do Rio de Janeiro

---

## 1. Identificação do projeto

**Time:** 03

**Cenário escolhido:** Contratações Públicas do Estado do Rio de Janeiro

**Problema:** Desenvolvimento de uma solução baseada em Retrieval-Augmented
Generation (RAG) para consulta inteligente de informações relacionadas às
contratações públicas do Estado do Rio de Janeiro.

**Repositório:**

https://github.com/grande-desafio-ia-rag-03/rag-grande-desafio-grupo03-contratacoes-publicas

### Integrantes

- Luiz Felipe Antunes
- Ana Karina
- Luciano Oliveira
- Marcio da Costa
- Ester da Silva
- Alisson Lima
- Renan Freitas

### Coordenador / Gerente

**Luiz Felipe Antunes**

O coordenador é responsável por acompanhar as atividades da equipe,
organizar as tarefas, acompanhar as entregas e promover a integração entre
os módulos.

O coordenador também participa da implementação técnica do projeto e poderá
ser substituído ao longo das próximas sprints conforme decisão da equipe.

---

# 2. Cenário escolhido

O Time 03 escolheu o cenário de **Contratações Públicas do Estado do Rio de
Janeiro**, envolvendo informações provenientes de documentos normativos,
contratos, processos de contratação, dados públicos estruturados e
informações agregadas.

O cenário foi escolhido por apresentar dados de diferentes naturezas e
formatos, tornando adequada a aplicação de técnicas de RAG, recuperação
híbrida, filtragem por metadados e geração de respostas fundamentadas nas
fontes recuperadas.

---

# 3. Objetivo da arquitetura

A arquitetura tem como objetivo permitir que usuários façam perguntas em
linguagem natural relacionadas às contratações públicas do Estado do Rio
de Janeiro.

O sistema deverá localizar informações relevantes nas bases disponíveis,
selecionar evidências e utilizar um modelo de linguagem para produzir uma
resposta fundamentada.

A solução seguirá uma abordagem de **Adaptive RAG**, permitindo que a
pergunta seja analisada antes da recuperação para determinar quais bases
devem ser consultadas.

---

# 4. Bases de conhecimento

Inicialmente o projeto trabalhará com três frentes principais de dados,
conforme a divisão atual da equipe.

## 4.1 Base Normativa

**Responsáveis:**

- Ana Karina
- Ester Silva

A Base Normativa será responsável pelos documentos jurídicos e normativos
relacionados às contratações públicas.

Exemplos:

- Lei nº 14.133/2021;
- decretos;
- regulamentos;
- editais;
- normas;
- documentos públicos relacionados às contratações.

Os documentos poderão passar por:

1. extração;
2. normalização;
3. chunking;
4. enriquecimento de metadados;
5. geração de embeddings;
6. indexação.

**Identificador:** `editais_normativo` · **Acesso:** `http_pdf` (lista fixa de URLs)

**Formato:** PDF e HTML convertido em texto.

**Uso esperado:** consultar regras e exigências legais, localizar cláusulas de
editais, responder perguntas sobre procedimentos, prazos e obrigações.

**Estratégia de chunking:** divisão por artigo, capítulo ou cláusula, com
overlap; cada chunk preserva a hierarquia (ex.: Art. 75, § 1º).

**Metadados:** `source_file`, `doc_type`, `base_id`, `chunk_id`, `sensitivity`,
`numero_edital`, `orgao`, `data_publicacao`, `artigo`, `objeto`.

**Vigência:** quando houver versões diferentes do mesmo decreto ou edital
retificado, a resposta cita a data de cada trecho.

**Divisão entre as responsáveis:**
- Ester: coleta (lista de URLs, download e hash em `src/fetch_corpus.py`)
- Ana Karina: processamento (extração de texto, chunking e metadados em `src/ingest.py`)

---

## 4.2 Base Estruturada

**Responsáveis:**

- Alisson Lima de Souza
- Luciano Costa

A Base Estruturada será responsável pelos dados organizados relacionados
às contratações públicas.

Exemplos:

- contratos;
- processos de contratação;
- órgãos;
- fornecedores;
- municípios;
- modalidades;
- datas;
- valores;
- dados provenientes de APIs públicas.

Entre as possíveis fontes está o PNCP — Portal Nacional de Contratações
Públicas.

Essa base deverá permitir consultas e filtros estruturados utilizando
metadados.

Exemplos:

- ano;
- município;
- órgão;
- fornecedor;
- modalidade;
- faixa de valor.

---

## 4.3 Base Agregada

**Responsáveis:**

- Felipe Antunes
- Renan Freitas

A Base Agregada será responsável pela produção e disponibilização de
informações consolidadas.

Exemplos:

- valores médios;
- valores homologados;
- indicadores;
- totais por município;
- totais por modalidade;
- agregações por período;
- estatísticas das contratações.

Essa base será particularmente importante para perguntas analíticas.

Exemplo:

> Qual foi o valor médio das contratações realizadas por determinado
> município em 2025?

---

# 5. Pipeline planejado

O pipeline inicial do sistema será:

                    ┌─────────────────────┐
                    │      USUÁRIO        │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   query_received    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ query_normalization │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   query_analysis    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  scope_validation   │
                    └──────────┬──────────┘
                               │
                        Está no escopo?
                         /           \
                       NÃO           SIM
                        │             │
                        ▼             ▼
               ┌────────────────┐ ┌──────────────────┐
               │request_refused │ │ adaptive_routing │
               └────────────────┘ └────────┬─────────┘
                                           │
                     ┌─────────────────────┼─────────────────────┐
                     │                     │                     │
                     ▼                     ▼                     ▼
              Base Normativa      Base Estruturada       Base Agregada
                     │                     │                     │
                     └─────────────────────┼─────────────────────┘
                                           │
                                           ▼
                                ┌────────────────────┐
                                │ metadata_filtering │
                                └─────────┬──────────┘
                                          │
                              ┌───────────┴───────────┐
                              │                       │
                              ▼                       ▼
                     ┌────────────────┐       ┌────────────────┐
                     │dense_retrieval │       │ bm25_retrieval │
                     └───────┬────────┘       └───────┬────────┘
                             │                        │
                             └──────────┬─────────────┘
                                        ▼
                                ┌──────────────┐
                                │  rrf_fusion  │
                                └──────┬───────┘
                                       │
                                       ▼
                                ┌──────────────┐
                                │  reranking   │
                                └──────┬───────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │ evidence_selection │
                             └─────────┬──────────┘
                                       │
                                       ▼
                              ┌─────────────────┐
                              │ prompt_building │
                              └────────┬────────┘
                                       │
                                       ▼
                              ┌────────────────┐
                              │ llm_generation │
                              └───────┬────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │ response_validation │
                           └──────────┬──────────┘
                                      │
                                      ▼
                            ┌───────────────────┐
                            │ output_guardrails │
                            └─────────┬─────────┘
                                      │
                                      ▼
                             ┌────────────────┐
                             │ response_final │
                             └───────┬────────┘
                                     │
                                     ▼
                             ┌───────────────┐
                             │    USUÁRIO    │
                             └───────────────┘

---

# 6. Stages utilizados nos logs

Os nomes dos estágios do pipeline também serão utilizados no campo
`stage` dos logs da aplicação.

| Stage | Responsabilidade |
|---|---|
| `query_received` | Recebimento da pergunta |
| `query_normalization` | Normalização da pergunta |
| `query_analysis` | Análise da intenção, entidades e filtros |
| `scope_validation` | Validação do escopo |
| `request_refused` | Registro de pergunta recusada |
| `adaptive_routing` | Escolha das bases |
| `metadata_filtering` | Aplicação dos filtros |
| `dense_retrieval` | Recuperação semântica |
| `bm25_retrieval` | Recuperação lexical |
| `rrf_fusion` | Fusão dos rankings |
| `reranking` | Reordenação dos resultados |
| `evidence_selection` | Seleção das evidências |
| `prompt_building` | Construção do prompt |
| `llm_generation` | Geração pelo LLM |
| `response_validation` | Validação da resposta |
| `output_guardrails` | Aplicação dos guardrails |
| `response_final` | Resposta final |

---

# 7. Adaptive Routing

O Adaptive Router analisará a pergunta para determinar quais bases são
necessárias.

Uma consulta poderá utilizar uma ou mais bases.

Exemplo:

> O que a Lei 14.133/2021 estabelece sobre determinada modalidade?

Roteamento:

    Pergunta
       |
       ▼
    Base Normativa

Outro exemplo:

> Qual foi o valor médio contratado pelo município X em 2025?

Roteamento:

    Pergunta
       |
       ├── Base Estruturada
       │
       └── Base Agregada

---

# 8. Recuperação híbrida

A arquitetura utilizará recuperação híbrida.

                    Pergunta
                       |
             ┌─────────┴─────────┐
             │                   │
             ▼                   ▼
      Dense Retrieval       BM25 Retrieval
        (semântica)           (lexical)
             │                   │
             └─────────┬─────────┘
                       │
                       ▼
                      RRF
                       │
                       ▼
                   Reranking
                       │
                       ▼
               Top-K Evidências

## Dense Retrieval

Responsável pela recuperação semântica utilizando embeddings.

Inicialmente poderá ser utilizado:

**FAISS**

## BM25

Responsável pela recuperação lexical baseada nos termos encontrados nos
documentos.

## RRF

O Reciprocal Rank Fusion combinará os rankings provenientes da recuperação
semântica e lexical.

## Reranking

Os resultados poderão ser reordenados utilizando um modelo de reranking
para selecionar as evidências mais relevantes.

---

# 9. Metadados

Os documentos e chunks deverão possuir metadados que permitam
rastreabilidade e filtragem.

Exemplo:

```json
{
    "base": "estruturada",
    "source": "PNCP",
    "uf": "RJ",
    "municipio": "Rio de Janeiro",
    "ano": 2026,
    "modalidade": "pregao",
    "document_id": "12345"
}