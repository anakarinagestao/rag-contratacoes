# RAG — Contratações Públicas do Estado do Rio de Janeiro

Projeto desenvolvido pelo **Time 03** para construção de uma solução de **Retrieval-Augmented Generation (RAG)** aplicada ao cenário de **Contratações Públicas do Estado do Rio de Janeiro**.

A aplicação utiliza **FastAPI** como framework backend, **Uvicorn** como servidor ASGI, **SQLite** como banco de dados local e **SQLAlchemy** para persistência e acesso aos dados.

O projeto utiliza diferentes fontes de dados e documentos, organizados em quatro bases:

* **Base Normativa** — leis, decretos e editais;
* **Base Estruturada** — contratos, contratações, itens e resultados;
* **Base Conversacional** — atas e e-mails sintéticos;
* **Base Agregada** — PCA e valores homologados por item.

A arquitetura evoluiu de um pipeline RAG inicialmente focado em recuperação documental para uma arquitetura de consulta composta por **Guardrail, Query Analyzer, Router, ferramentas de consulta, geração, persistência e auditoria**.

O objetivo é permitir que uma mesma aplicação identifique o tipo de pergunta recebida e utilize a estratégia adequada para respondê-la, podendo realizar consultas estruturadas, recuperação documental ou combinar as duas abordagens.

---

# 1. Início rápido

## 1.1 Criar e ativar o ambiente

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 1.2 Instalar dependências

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 1.3 Criar o `.env`

Na raiz do projeto:

```env
DATABASE_URL=sqlite:///./app.db

JWT_SECRET_KEY=
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

Gere uma chave JWT local:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copie o resultado para `JWT_SECRET_KEY`.

O `.env` não deve ser enviado ao GitHub.

## 1.4 Inicializar o banco

```powershell
python -m app.init_db
```

## 1.5 Criar o primeiro administrador

```powershell
python -m app.create_admin
```

O comando solicita username, email, senha e confirmação.

A senha deve possuir de 12 a 128 caracteres e somente seu hash é armazenado.

Exemplo:

```text
Username: alisson
Email: a@a.com
Senha (12 a 128 caracteres):
Confirme a senha:

Administrador criado: alisson (id=1).
```

## 1.6 Iniciar a API

```powershell
python -m uvicorn app.main:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

ReDoc:

```text
http://127.0.0.1:8000/redoc
```

## 1.7 Fazer login

No Swagger:

```text
Auth → POST /auth/login
```

Envie:

```json
{
  "username": "alisson",
  "password": "sua senha"
}
```

Copie o `access_token`, clique em **Authorize**, escolha **BearerAuth** e cole somente o token.

Depois teste:

```text
GET /auth/me
```

As rotas `/admin/*` exigem usuário autenticado com `role = admin`.

---

# 2. Tecnologias

Tecnologias utilizadas ou previstas no projeto:

* Python
* FastAPI
* Uvicorn
* Pydantic
* Pydantic Settings
* SQLAlchemy
* SQLite
* FAISS
* BM25
* RRF
* Pytest
* LLM
* Docling
* OpenRouter
* LangChain
* JWT
* Argon2id

---

# 3. Pré-requisitos

Antes de executar o projeto, verifique se possui o Python instalado.

```bash
python --version
```

No Windows também pode ser utilizado:

```bash
py --version
```

Recomenda-se utilizar **Python 3.12 ou superior**.

---

# 4. Clonar o projeto

Clone o repositório:

```bash
git clone https://github.com/grande-desafio-ia-rag-03/rag-grande-desafio-grupo03-contratacoes-publicas.git
```

Entre na pasta:

```bash
cd rag-grande-desafio-grupo03-contratacoes-publicas
```

Caso seja necessário trabalhar em uma branch específica:

```bash
git fetch --all
git switch nome-da-branch
```

---

# 5. Criar o ambiente virtual

O ambiente virtual **não é enviado para o GitHub**.

Cada desenvolvedor deverá criar sua própria `.venv` depois de clonar o projeto.

## Windows — PowerShell

```powershell
py -m venv .venv
```

Ative:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Windows — Git Bash

```bash
py -m venv .venv
```

Ative:

```bash
source .venv/Scripts/activate
```

Quando estiver ativo:

```text
(.venv)
```

---

# 6. Instalar as dependências

Com a `.venv` ativada:

```bash
python -m pip install --upgrade pip
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

Para desenvolvimento e testes:

```bash
pip install -r requirements-dev.txt
```

---

# 7. Configuração do `.env`

O projeto utiliza variáveis de ambiente para configurações que não devem ficar diretamente no código.

Crie:

```text
.env
```

na raiz do projeto.

Exemplo:

```env
DATABASE_URL=sqlite:///./app.db

JWT_SECRET_KEY=
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

O arquivo `.env` não deve ser enviado para o GitHub.

---

# 8. Banco de dados

O projeto utiliza inicialmente:

* **SQLite** — banco de dados;
* **SQLAlchemy** — ORM e acesso ao banco.

Configuração:

```env
DATABASE_URL=sqlite:///./app.db
```

O banco é armazenado localmente em:

```text
app.db
```

O fluxo principal é:

```text
FastAPI
   ↓
SQLAlchemy
   ↓
SQLite
   ↓
app.db
```

O SQLite não necessita de servidor, Docker, porta ou serviço separado.

---

# 9. Modelos e persistência

A aplicação utiliza modelos SQLAlchemy para persistir os dados necessários ao funcionamento do sistema.

Entre as estruturas utilizadas estão:

| Módulo      | Estruturas                                 |
| ----------- | ------------------------------------------ |
| `corpus`    | `corpus_versions`                          |
| `documents` | `documents`, `document_chunks`             |
| `query`     | `queries`, `responses`, `response_sources` |
| `feedback`  | `feedback`                                 |
| `rag`       | `pipeline_events`                          |
| `auth`      | `users`                                    |

Os relacionamentos principais incluem:

```text
Corpus
   ↓
Documents
   ↓
DocumentChunks

Query
   ↓
Response
   ↓
ResponseSources
   ↓
DocumentChunks
```

Também existem relacionamentos entre consultas, feedback e eventos de pipeline.

O campo textual `document_chunks.chunk_id` é utilizado como identificador estável para os índices externos de recuperação, enquanto `response_sources.chunk_id` referencia o registro correspondente no banco.

Os índices de recuperação não armazenam seus embeddings diretamente no SQLite.

---

# 10. Inicialização do banco

Não é necessário criar o arquivo `app.db` manualmente.

Execute:

```bash
python -m app.init_db
```

A inicialização cria as tabelas necessárias sem depender do Uvicorn.

Alterações futuras de esquema deverão ser tratadas adequadamente, pois `create_all()` não funciona como mecanismo de migration para alterações de tabelas existentes.

---

# 11. Visualizar o SQLite no DBeaver

O arquivo `app.db` pode ser aberto diretamente no **DBeaver**.

Não é necessário deixar o Uvicorn rodando para consultar o banco.

No DBeaver:

1. Abra **Nova Conexão**.
2. Escolha **SQLite**.
3. Selecione o arquivo `app.db`.
4. Teste a conexão.
5. Conclua a configuração.

Exemplo:

```sql
SELECT * FROM documents;
```

---

# 12. Executar a aplicação

Com a `.venv` ativada:

```bash
python -m uvicorn app.main:app --reload
```

Também é possível utilizar:

```bash
uvicorn app.main:app --reload
```

A primeira opção é recomendada para garantir que o Uvicorn utilizado pertence ao ambiente Python ativo.

---

# 13. Acessar a API

API:

```text
http://127.0.0.1:8000
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

ReDoc:

```text
http://127.0.0.1:8000/redoc
```

O Swagger é utilizado durante o desenvolvimento para visualizar e testar os endpoints.

---

# 14. Arquitetura atual

A arquitetura atual do projeto está organizada em etapas independentes:

```text
Pergunta do usuário
        ↓
    Guardrail
        ↓
  Query Analyzer
        ↓
      Router
        ↓
 ┌──────┼────────┐
 ↓      ↓        ↓
SQL    RAG     Hybrid
Tool   Tool      ↓
 ↓      ↓      SQL + RAG
 └──────┼────────┘
        ↓
    Generation
        ↓
   Persistence
        ↓
      Audit
```

Cada etapa possui uma responsabilidade específica.

## 14.1 Guardrail

O Guardrail realiza as validações iniciais da pergunta antes que ela siga para o restante do pipeline.

Seu objetivo é impedir que consultas fora do escopo do projeto ou situações não permitidas avancem para execução.

O Guardrail não responde à pergunta.

---

## 14.2 Query Analyzer

O **Query Analyzer** interpreta a pergunta recebida e produz uma representação estruturada da intenção.

Ele identifica, entre outras informações:

* rota provável;
* intenção;
* entidade;
* operação;
* campo;
* filtros;
* bases necessárias;
* necessidade de SQL;
* necessidade de RAG;
* nível de confiança.

Exemplos de rotas:

```text
structured
rag
hybrid
```

Exemplos de intenções:

```text
lookup
aggregate
comparison
ranking
document_question
normative_question
conversational_question
unknown
```

O Analyzer **não executa SQL, não realiza recuperação e não gera a resposta**.

Sua responsabilidade é interpretar a pergunta para que a próxima etapa possa decidir como executá-la.

Exemplos validados:

```text
"Qual foi o valor total dos contratos em dezembro de 2025?"
→ structured
```

```text
"Quais são as regras para contratação direta previstas no Decreto 48.820 de 2023?"
→ rag
```

```text
"Quais contratos ultrapassaram o limite de contratação direta previsto no Decreto 48.820 de 2023?"
→ hybrid
```

---

# 15. Router

O Router utiliza a análise produzida pelo Query Analyzer para selecionar a estratégia de execução.

A lógica prevista é:

```text
Query Analyzer
      ↓
    Router
      ├── structured → SQL Tool
      ├── rag        → RAG Tool
      └── hybrid     → SQL Tool + RAG Tool
```

O Router não deve substituir o Analyzer.

O Analyzer interpreta a pergunta.

O Router decide para onde a pergunta deve seguir.

---

# 16. Ferramentas de consulta

A arquitetura utiliza ferramentas especializadas de acordo com o tipo de pergunta.

## 16.1 SQL Tool

A SQL Tool será responsável por consultas sobre dados estruturados.

Exemplos:

* contagens;
* somas;
* médias;
* valores mínimos e máximos;
* agrupamentos;
* filtros;
* rankings;
* comparações.

A execução deve ser somente leitura.

A ferramenta deve trabalhar com:

* consultas `SELECT`;
* tabelas e colunas permitidas;
* limite de linhas;
* timeout;
* validação da consulta;
* bloqueio de comandos de escrita ou DDL.

Não devem ser permitidas operações como:

```text
INSERT
UPDATE
DELETE
DROP
ALTER
CREATE
```

---

## 16.2 RAG Tool

A RAG Tool utiliza a estrutura de recuperação documental já desenvolvida no projeto.

O fluxo utiliza:

```text
Documentos
    ↓
Chunks
    ↓
BM25 + FAISS
    ↓
RRF
    ↓
Evidências
```

A recuperação pode utilizar:

* busca lexical com BM25;
* busca vetorial com FAISS;
* fusão dos resultados com RRF;
* metadados;
* rastreabilidade dos chunks.

---

## 16.3 Hybrid

A rota híbrida combina as duas estratégias.

Exemplo:

```text
Pergunta
   ↓
Analyzer
   ↓
Hybrid
   ├── SQL → identifica contratos
   │
   └── RAG → recupera a norma aplicável
             ↓
          Generation
```

Esse tipo de consulta é necessário quando a resposta depende simultaneamente de dados estruturados e documentos.

---

# 17. Generation

Depois da recuperação das informações necessárias, o sistema utiliza o modelo de linguagem para construir a resposta.

A geração deve utilizar as evidências recuperadas e manter a rastreabilidade das fontes.

O fluxo geral é:

```text
Consulta
   ↓
Guardrail
   ↓
Analyzer
   ↓
Router
   ↓
Tool(s)
   ↓
Contexto / Resultados
   ↓
Generation
   ↓
Resposta
```

A geração não deve substituir as etapas de análise ou recuperação.

---

# 18. Persistence

O sistema mantém a rastreabilidade das consultas e respostas.

O fluxo principal de persistência é:

```text
Query
  ↓
Response
  ↓
ResponseSource
  ↓
DocumentChunk
```

Também são utilizados eventos de pipeline para registrar informações de execução.

Isso permite relacionar:

* pergunta realizada;
* resposta gerada;
* fontes utilizadas;
* chunks recuperados;
* execução do pipeline;
* informações de auditoria.

---

# 19. Audit e observabilidade

A arquitetura prevê auditoria da execução completa da pergunta.

Os registros devem permitir acompanhar:

```text
trace_id
timestamp
user
input
guardrail
analyzer
route
tools
SQL
resultados
RAG
generation
sources
errors
timings
```

Cada pergunta deverá poder ser acompanhada desde sua entrada até a resposta final.

O objetivo é facilitar:

* depuração;
* avaliação;
* rastreabilidade;
* identificação de falhas;
* análise de desempenho;
* auditoria das respostas.

---

# 20. Estrutura atual do pipeline

O fluxo conceitual completo é:

```text
Pergunta do usuário
        ↓
query_received
        ↓
Guardrail
        ↓
query_analysis
        ↓
roteamento
        ↓
┌───────────────┬────────────────┐
│               │                │
structured      rag            hybrid
│               │                │
SQL Tool      RAG Tool      SQL + RAG
│               │                │
└───────────────┴────────────────┘
                ↓
             geração
                ↓
            validação
                ↓
          persistência
                ↓
              audit
                ↓
         resposta final
```

Para o RAG, a recuperação documental continua utilizando:

```text
dense_retrieval
        +
bm25_retrieval
        ↓
fusao_rrf
        ↓
rerank
        ↓
evidence_selection
        ↓
generation
```

---

# 21. Dados e índices

A pasta:

```text
data/
```

é utilizada para organização do corpus.

Estrutura conceitual:

```text
data/
├── raw/
├── inventory/
├── processed/
└── manual/
```

Os índices são armazenados separadamente:

```text
indexes/
├── faiss/
└── bm25/
```

A divisão de responsabilidades é:

```text
SQLite
   ↓
dados da aplicação + metadados + persistência

FAISS
   ↓
índice vetorial / embeddings

BM25
   ↓
índice lexical
```

---

# 22. Estrutura do projeto

A estrutura combina a aplicação principal, módulos de negócio e componentes do pipeline de dados e RAG.

```text
rag-grande-desafio-grupo03-contratacoes-publicas/
│
├── .env
├── .gitignore
├── README.md
├── requirements.txt
├── requirements-dev.txt
│
├── app/
│   ├── __init__.py
│   ├── main.py
│   │
│   ├── core/
│   │   ├── config.py
│   │   ├── database/
│   │   ├── exceptions.py
│   │   ├── logging.py
│   │   └── security.py
│   │
│   └── modules/
│       ├── auth/
│       ├── admin/
│       ├── corpus/
│       ├── documents/
│       ├── query/
│       ├── feedback/
│       └── rag/
│
├── src/
│   ├── analyzer/
│   ├── query/
│   │   ├── schemas.py
│   │   └── analyzer.py
│   │
│   ├── retrieve.py
│   ├── generate.py
│   ├── guardrails.py
│   ├── index.py
│   ├── ingestion.py
│   └── ...
│
├── data/
│   ├── raw/
│   ├── inventory/
│   ├── processed/
│   └── manual/
│
├── indexes/
│   ├── faiss/
│   └── bm25/
│
├── logs/
│   └── queries/
│
├── tests/
│
└── notes/
    ├── ARQUITETURA.md
    ├── PERSONAS.md
    └── corpus_manifest.yaml
```

A estrutura exata pode evoluir conforme os módulos forem integrados.

---

# 23. Responsabilidade das principais pastas

## `app/`

Código principal da aplicação.

## `app/core/`

Configurações compartilhadas pelo sistema.

Inclui recursos relacionados a:

```text
configuração
banco
logging
segurança
exceções
```

## `app/modules/`

Módulos de negócio da aplicação.

Os módulos possuem responsabilidades específicas, como:

```text
auth
admin
corpus
documents
query
feedback
rag
```

## `src/`

Componentes relacionados ao pipeline de dados, análise, recuperação e geração.

Inclui componentes relacionados a:

* Query Analyzer;
* Guardrails;
* ingestão;
* indexação;
* recuperação;
* geração.

---

# 24. Bases do projeto

O projeto está organizado em quatro bases principais.

---

## 24.1 Base Normativa

**Responsáveis:**

* Ana Karina
* Ester Neves

Base composta por leis, decretos estaduais e editais de licitação.

É a base que representa **o que a legislação e os documentos normativos determinam**.

### Corpus atual

A base normativa possui:

* 1 Lei 14.133/2021;
* 7 decretos estaduais;
* 5 editais;
* 13 documentos no conjunto original;
* 794 chunks processados após a evolução do processamento.

A base foi processada mantendo metadados e rastreabilidade.

### Processamento

A Lei e os decretos são divididos por artigo.

Os editais são divididos por cláusulas.

Trechos acima de 4.000 caracteres podem ser divididos em partes, utilizando overlap de 200 caracteres dentro da mesma unidade documental.

Não existe overlap entre artigos diferentes.

### Metadados

Entre os metadados utilizados estão:

```text
source_file
doc_type
chunk_id
sensitivity
base_id
data_publicacao
fonte_data
numero_norma
numero_edital
artigo
clausula
parte
tem_overlap
orgao
ano
altera
dados_pessoais
```

### Dados pessoais

A ingestão trata dados pessoais encontrados nos documentos.

| Dado            | Tratamento           |
| --------------- | -------------------- |
| CPF             | mascarado            |
| Telefone        | mascarado            |
| E-mail          | mantido e sinalizado |
| CNPJ            | mantido              |
| Nome de empresa | mantido              |
| Valores         | mantidos             |
| Objeto          | mantido              |

Após o processamento, não há CPF ou telefone visível no corpus processado.

### Logs

A coleta e ingestão utilizam logs estruturados em JSONL:

```text
logs/pipeline-AAAA-MM-DD.jsonl
```

Cada execução recebe um `run_id`.

Os eventos registram informações como:

```text
timestamp
run_id
stage
status
latência
entrada
saída
modelo
tokens
informações adicionais
```

---

## 24.2 Base Estruturada

**Responsáveis:**

* Alisson Lima de Souza
* Luciano Costa

Responsável pelos dados estruturados relacionados às contratações públicas.

A base trabalha com dados como:

* contratações;
* contratos;
* itens;
* resultados;
* fornecedores;
* órgãos;
* municípios;
* informações disponibilizadas pelo PNCP.

Durante a evolução do projeto foram implementadas rotinas de coleta, persistência, controle de checkpoints, tratamento de falhas e preparação dos dados para consultas estruturadas.

Também foram realizados testes com dados reais e integração com a camada RAW.

A base estruturada será utilizada principalmente pela **SQL Tool** nas consultas estruturadas.

---

## 24.3 Base Agregada

**Responsáveis:**

* Felipe Antunes
* Renan Freitas

Responsável por indicadores, estatísticas e informações consolidadas.

A base utiliza principalmente informações relacionadas ao:

* Plano de Contratações Anual (PCA);
* valores homologados por item;
* resultados das contratações;
* informações agregadas provenientes do PNCP.

Foram desenvolvidos mecanismos de:

* coleta;
* versionamento;
* checkpoints;
* detecção de alterações;
* preservação dos dados RAW;
* auditoria;
* integração dos resultados.

A base também foi ampliada historicamente, com coleta de diferentes períodos e órgãos.

---

## 24.4 Base Conversacional

Base destinada às informações provenientes de:

* atas;
* e-mails sintéticos;
* informações conversacionais utilizadas no cenário do projeto.

A base faz parte da definição do corpus e será integrada às etapas de processamento, recuperação e roteamento conforme a evolução do pipeline.

---

# 25. Coleta e camada RAW

As diferentes bases possuem necessidades de coleta específicas.

A camada RAW mantém os dados originais recebidos das fontes.

O objetivo é preservar:

* payload original;
* identificação da fonte;
* versão;
* hash;
* checkpoint;
* status da coleta;
* informações necessárias para auditoria.

O fluxo geral é:

```text
Fonte oficial
     ↓
Coleta
     ↓
Checkpoint
     ↓
Detecção de alteração
     ↓
RAW
     ↓
Processamento
     ↓
Estruturas de consulta / RAG
```

Essa separação permite reprocessar os dados sem depender de uma nova consulta à fonte original.

---

# 26. Snapshot e versionamento

O projeto utiliza snapshots para preservar determinados recortes do corpus.

Um snapshot pode conter:

* banco SQLite;
* dump SQL;
* inventário;
* hashes;
* informações de versão;
* registros necessários para restauração.

O objetivo é permitir reprodução, auditoria e comparação dos dados coletados.

---

# 27. Base normativa — execução

As rotinas de coleta da base normativa podem ser executadas conforme o manifesto.

Exemplo:

```bash
python -m app.modules.normative.services fetch --manifest corpus_manifest.yaml --versao v1
```

### Fluxo completo da base normativa

Do manifesto até a busca, em ordem. Cada passo usa o resultado do anterior.

1. **Nova versão e diff** (opcional): cria `v(n+1)` sem sobrescrever a anterior e gera `data/inventory/DIFF_v1_v2.md`.

```bash
python -m app.modules.normative.services update --manifest corpus_manifest.yaml
```

2. **Inventário**: gera `data/inventory/v1.json`, com o hash do manifesto, e o inventário legível.

```bash
python -m app.modules.normative.services inventory --versao v1 --manifest corpus_manifest.yaml
```

3. **Processamento em trechos**: lê `data/raw/v1/editais_normativo/` e grava `data/processed/v1_editais_normativo.jsonl` (794 trechos). Se algum trecho ficar sem data de vigência, o comando avisa no final.

```bash
python -m app.modules.ingest.services --versao v1
```

4. **Carregamento no banco**: grava os trechos em `documents` e `document_chunks`, na versão de corpus `normativo-v1`. É idempotente: rodar de novo não duplica nada. O FTS5, que faz a busca por palavra-chave (BM25), indexa os trechos sozinho, por gatilho.

```bash
python -m app.modules.normative.persistencia --versao v1
```

5. **Índice vetorial (FAISS)**: gera os vetores dos trechos no servidor de embeddings e grava em um staging. Para juntar com o índice do PNCP, use `--preparar-staging` a partir do índice atual e confira com `--auditar` antes de publicar.

```bash
python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss --base-id editais_normativo --ate-concluir --indice-dir data/index/faiss/staging/<nome>
```

6. **Perguntas de referência**: `eval/questions.json` traz as perguntas da base normativa, cada uma com a fonte esperada.

Antes de rodar, crie o arquivo de configuração local a partir do exemplo (`.env.example` → `.env`). Ele não vai para o Git.

**O que não vai para o Git:** `data/raw/` (coletado), `data/index/` (índice FAISS), `logs/` e `.env`. O banco (`app.db`), que contém também o índice BM25 do FTS5, e o índice FAISS são compartilhados pelo Drive do grupo, e não por commit; nomeie sempre os arquivos no `git add` para o banco não entrar por engano. O corpus é reconstruído por qualquer pessoa com `fetch`, e o banco, com os passos 3 e 4.

A coleta é planejada para ser:

* idempotente;
* retomável;
* controlada por versão;
* registrada em inventário;
* auditável.

A atualização de uma versão pode gerar diferenças entre versões sem sobrescrever os dados anteriores.

---

# 28. Ingestão documental

A ingestão prepara os arquivos para utilização no RAG.

O projeto possui suporte para diferentes formatos documentais, incluindo:

```text
JSON
JSONL
PDF
HTML
TXT
```

Para documentos que necessitam de processamento adicional, pode ser utilizado o **Docling**.

O processo geral é:

```text
Arquivo
   ↓
Extração
   ↓
Limpeza
   ↓
Metadados
   ↓
Chunking
   ↓
Document
   ↓
DocumentChunk
   ↓
Indexação
```

A ingestão utiliza identificadores determinísticos para permitir comportamento idempotente e reaproveitamento de conteúdos já processados.

---

# 29. Recuperação híbrida

A recuperação documental utiliza duas estratégias complementares.

## Busca lexical

Utiliza **BM25** para localizar termos diretamente relacionados à pergunta.

## Busca semântica

Utiliza **FAISS** e embeddings para encontrar conteúdos semanticamente relacionados.

## Fusão

Os resultados das duas estratégias são combinados utilizando **RRF — Reciprocal Rank Fusion**.

Fluxo:

```text
              Pergunta
                  ↓
        ┌─────────┴─────────┐
        ↓                   ↓
      BM25                FAISS
        ↓                   ↓
        └─────────┬─────────┘
                  ↓
                 RRF
                  ↓
              Ranking
                  ↓
              Evidências
```

A recuperação híbrida foi validada com documentos reais do projeto.

---

# 30. API de geração

A aplicação disponibiliza o fluxo de geração por API.

Endpoint:

```text
POST /query/generate
```

A consulta recebe a pergunta e parâmetros relacionados à recuperação.

A resposta pode conter:

* resposta gerada;
* fontes;
* posição da fonte;
* identificador do chunk;
* relevância;
* metadados.

Exemplo conceitual:

```text
Pergunta
   ↓
RAG
   ↓
Recuperação híbrida
   ↓
Contexto
   ↓
LLM
   ↓
Resposta + fontes
```

O fluxo também realiza persistência da consulta, resposta e fontes.

---

# 31. Query Analyzer — estrutura

O Query Analyzer possui schemas próprios para representar a análise.

A análise inclui conceitos como:

```text
route
intent
entity
operation
field
filters
bases
requires_sql
requires_rag
reasoning
confidence
original_question
metadata
```

As entidades consideradas incluem:

```text
contratos
contratacoes
itens_contratacao
resultados_item
pca
documentos
atas
emails
fornecedores
orgaos
municipios
normas
```

A análise é validada por schema antes de seguir para as próximas etapas.

---

# 32. Avaliação

A avaliação do sistema será realizada utilizando perguntas de referência relacionadas às personas e ao cenário.

A avaliação deverá considerar, entre outros aspectos:

* recuperação;
* relevância;
* geração;
* fundamentação;
* rastreabilidade;
* escolha da rota;
* comportamento do sistema diante de perguntas fora do escopo.

A base normativa congelada foi utilizada como referência inicial para preparação das perguntas de benchmark.

---

# 33. Logs de pipeline

A coleta e ingestão possuem logs estruturados em JSONL:

```text
logs/pipeline-AAAA-MM-DD.jsonl
```

O formato utiliza um `run_id` para relacionar os eventos de uma mesma execução.

Exemplo:

```json
{
  "ts": "2026-10-02T15:29:26-03:00",
  "run_id": "a15bffbe",
  "query_id": null,
  "stage": "coleta",
  "status": "ok",
  "latencia_ms": 2482,
  "input": "data/raw/v1/editais_normativo",
  "output": "13 ok, 0 falhas de 13",
  "tokens": {
    "prompt": 0,
    "completion": 0,
    "total": 0
  },
  "modelo": null,
  "extra": {
    "base_id": "editais_normativo",
    "versao": "v1"
  }
}
```

Os eventos de consulta deverão evoluir para registrar também:

```text
guardrail
query_analysis
route
tool
retrieval
generation
sources
errors
timings
```

---

# 34. Testes

Os testes são utilizados para validar as diferentes partes do sistema.

Exemplo:

```bash
python -m unittest discover -s tests/integration -p "test_*.py" -v
```

Também podem ser utilizados testes específicos com Pytest:

```bash
python -m pytest -v
```

As suítes existentes abrangem diferentes aspectos do projeto, incluindo:

* coleta;
* checkpoints;
* retries;
* persistência;
* ingestão;
* recuperação híbrida;
* geração;
* autenticação;
* banco;
* integridade;
* rastreabilidade;
* idempotência;
* Query Analyzer.

---

# 35. Autenticação JWT e administração

A autenticação utiliza a tabela `users` no SQLite.

O módulo `auth` contém:

* model;
* schemas;
* serviços;
* rotas.

O módulo `admin` utiliza o mesmo modelo de usuários.

Hash de senha, JWT e dependências de autorização ficam em:

```text
app/core/security.py
```

As dependências de autenticação incluem:

```text
PyJWT
pwdlib[argon2]
email-validator
```

A API exige uma chave JWT de pelo menos 32 bytes.

O algoritmo utilizado nesta implementação é:

```text
HS256
```

---

# 36. Criar o primeiro administrador

Execute:

```powershell
python -m app.init_db
python -m app.create_admin
```

O comando solicita:

* username;
* email;
* senha;
* confirmação.

Somente o hash Argon2id da senha é armazenado.

O primeiro administrador é provisionado exclusivamente pelo comando local.

---

# 37. Cadastro de usuários

O cadastro público está disponível em:

```text
POST /auth/register
```

Exemplo:

```json
{
  "username": "novo_usuario",
  "email": "usuario@example.com",
  "password": "uma senha de pelo menos 12 caracteres"
}
```

Para criação administrativa de usuários:

```text
POST /admin/users
```

Exemplo:

```json
{
  "username": "novo_admin",
  "email": "novo_admin@example.com",
  "password": "uma senha de pelo menos 12 caracteres",
  "role": "admin"
}
```

Somente administradores autenticados podem utilizar as rotas administrativas.

---

# 38. Uso do JWT pelo Swagger

1. Faça login em `POST /auth/login`.
2. Copie somente o `access_token`.
3. Clique em **Authorize**.
4. Em **BearerAuth**, cole o token.
5. Confirme.
6. Execute `GET /auth/me`.

Administradores também podem testar:

```text
GET /admin/users
GET /admin/users/{user_id}
PATCH /admin/users/{user_id}/status
```

---

# 39. `.gitignore`

Arquivos locais não devem ser enviados para o GitHub.

Exemplo:

```gitignore
# Ambiente virtual
.venv/
venv/

# Variáveis de ambiente
.env

# Banco SQLite local
app.db
*.db

# Python
__pycache__/
*.py[cod]

# Pytest
.pytest_cache/

# IDE
.vscode/
.idea/

# Logs
*.log
logs/

# Dados locais
data/raw/
```

A `.venv` nunca deve ser versionada.

---

# 40. Documentação do projeto

Os principais documentos de apoio incluem:

| Documento              | Responsável           |
| ---------------------- | --------------------- |
| `ARQUITETURA.md`       | Alisson Lima de Souza |
| `PERSONAS.md`          | Luciano Costa         |
| `corpus_manifest.yaml` | Felipe Antunes        |

Outros documentos e materiais são mantidos conforme a necessidade das diferentes frentes do projeto.

---

# 41. Equipe

**Time 03 — Contratações Públicas do Estado do Rio de Janeiro**

* Luiz Felipe Antunes
* Ana Karina
* Luciano Oliveira
* Marcio da Costa
* Ester da Silva
* Alisson Lima
* Renan Freitas

**Gerente / Coordenador:** Luiz Felipe Antunes

O coordenador acompanha as atividades, organiza as tarefas e auxilia na integração das diferentes partes do projeto, além de participar tecnicamente do desenvolvimento.

---

# 42. Responsabilidades das bases

| Base           | Responsáveis                          |
| -------------- | ------------------------------------- |
| Normativa      | Ana Karina e Ester Neves              |
| Estruturada    | Alisson Lima de Souza e Luciano Costa |
| Agregada       | Felipe Antunes e Renan Freitas        |
| Conversacional | Base do corpus do projeto             |

As atribuições acima permanecem de acordo com a divisão estabelecida pela equipe.

---

# 43. Responsabilidades técnicas e evolução do projeto

As responsabilidades são distribuídas entre as diferentes frentes do projeto.

A equipe atua de forma integrada nas etapas de:

```text
Coleta
   ↓
RAW
   ↓
Processamento
   ↓
Ingestão
   ↓
Indexação
   ↓
Recuperação
   ↓
Análise da pergunta
   ↓
Roteamento
   ↓
Geração
   ↓
Persistência
   ↓
Auditoria
```

Cada integrante mantém sua frente de desenvolvimento, enquanto as etapas de integração são acompanhadas conjuntamente pela equipe.

---

# 44. Estado atual da arquitetura

A evolução do projeto pode ser resumida da seguinte forma:

### Etapa inicial

```text
Corpus
  ↓
Ingestão
  ↓
Indexação
  ↓
BM25 + FAISS
  ↓
RRF
  ↓
LLM
```

### Arquitetura atual

```text
                         Pergunta
                            ↓
                        Guardrail
                            ↓
                      Query Analyzer
                            ↓
                          Router
                            ↓
              ┌─────────────┼─────────────┐
              ↓             ↓             ↓
          Structured       RAG          Hybrid
              ↓             ↓             ↓
           SQL Tool      RAG Tool     SQL + RAG
              └─────────────┼─────────────┘
                            ↓
                        Generation
                            ↓
                        Persistence
                            ↓
                          Audit
                            ↓
                      Resposta final
```

Essa evolução permite que o sistema deixe de tratar todas as perguntas exclusivamente como consultas RAG e passe a selecionar a estratégia adequada de acordo com a intenção identificada.

---

# 45. Fluxo para novos desenvolvedores

Depois de clonar:

```bash
git clone https://github.com/grande-desafio-ia-rag-03/rag-grande-desafio-grupo03-contratacoes-publicas.git

cd rag-grande-desafio-grupo03-contratacoes-publicas
```

Criar ambiente:

```bash
py -m venv .venv
```

Ativar no PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Instalar:

```bash
pip install -r requirements.txt
```

Criar `.env`:

```env
DATABASE_URL=sqlite:///./app.db

JWT_SECRET_KEY=
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

Inicializar:

```bash
python -m app.init_db
```

Executar:

```bash
python -m uvicorn app.main:app --reload
```

Abrir:

```text
http://127.0.0.1:8000/docs
```

---

# 46. Histórico recente de evolução

O projeto passou pelas seguintes etapas principais:

### Encontros 1–3

* definição do cenário;
* arquitetura inicial;
* criação do corpus;
* início das coletas;
* integração com PNCP;
* definição das bases;
* primeiros dados RAW;
* coleta de resultados homologados;
* início do processamento documental.

### Encontros 4–6

* evolução dos checkpoints;
* retries e tratamento de falhas;
* processamento da base normativa;
* criação do inventário;
* versionamento do corpus;
* tratamento de dados pessoais;
* expansão histórica das bases;
* snapshots;
* auditoria e integridade;
* preparação da recuperação híbrida.

### Encontro 7

* evolução da ingestão documental;
* processamento de documentos reais;
* Docling;
* chunks determinísticos;
* ingestão idempotente;
* BM25 + FAISS + RRF;
* geração fundamentada;
* persistência de Query, Response e ResponseSource;
* integração das diferentes frentes.

### Encontro 8

* integração do RAG com a API;
* endpoint `POST /query/generate`;
* Swagger;
* persistência das respostas e fontes;
* logs estruturados;
* correção de idempotência;
* evolução da suíte de testes;
* auditoria das coletas históricas;
* início da estrutura do Query Analyzer.

### Encontro 9

* fechamento da cobertura de resultados do recorte trabalhado;
* evolução da coleta do PCA;
* nova frente para atas de registro de preços;
* consolidação dos 794 chunks normativos;
* início do benchmark;
* desenvolvimento e validação do Query Analyzer;
* identificação das rotas `structured`, `rag` e `hybrid`;
* preparação da integração com Router e ferramentas de consulta.

---

# 47. Próximas etapas

As próximas etapas técnicas do projeto estão organizadas em torno da integração das partes já desenvolvidas.

## Corpus e dados

* finalizar coletas pendentes;
* ampliar e auditar as bases;
* concluir normalização;
* validar relacionamentos;
* organizar artefatos e snapshots.

## RAG

* continuar indexação;
* ampliar avaliação da recuperação;
* validar geração;
* manter rastreabilidade das evidências.

## Consulta

* integrar Query Analyzer;
* implementar Router;
* estruturar SQL Tool;
* integrar RAG Tool;
* implementar fluxo Hybrid.

## Segurança e controle

* manter Guardrails;
* manter SQL somente leitura;
* validar escopo das consultas;
* preservar autenticação e autorização.

## Observabilidade

* consolidar logs;
* registrar etapas da consulta;
* medir latência;
* registrar fontes;
* registrar erros;
* manter `trace_id`/`run_id` para auditoria.

## Avaliação

* ampliar benchmark;
* testar perguntas estruturadas;
* testar perguntas normativas;
* testar perguntas conversacionais;
* testar perguntas híbridas;
* avaliar recuperação e geração;
* validar rastreabilidade das respostas.

---

# 48. Objetivo final

O objetivo é entregar uma aplicação capaz de responder perguntas sobre **Contratações Públicas do Estado do Rio de Janeiro** utilizando as diferentes fontes disponíveis no corpus.

A aplicação deverá identificar a natureza da pergunta, selecionar a estratégia adequada e produzir uma resposta fundamentada e rastreável.

O fluxo final esperado é:

```text
Usuário
  ↓
Pergunta
  ↓
Guardrail
  ↓
Query Analyzer
  ↓
Router
  ↓
┌─────────────────────────────┐
│                             │
│ Structured │ RAG │ Hybrid   │
│                             │
└─────────────────────────────┘
  ↓
Ferramentas de consulta
  ↓
Resultados / Evidências
  ↓
Generation
  ↓
Validação
  ↓
Persistence
  ↓
Audit
  ↓
Resposta fundamentada
```

A arquitetura foi estruturada para manter separadas as responsabilidades de **interpretação, roteamento, recuperação, consulta, geração, persistência e auditoria**, permitindo que cada componente possa evoluir independentemente sem comprometer as demais partes do sistema.
