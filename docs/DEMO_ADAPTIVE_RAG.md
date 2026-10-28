# Demonstração local do Adaptive RAG PNCP/RJ

## Estado homologado

- base normalizada aberta em modo somente leitura;
- catálogo: 549.171 documentos PNCP disponíveis;
- piloto documental: 2 contratos, 55 chunks e 55 vetores FAISS;
- embeddings e geração pelo provedor OpenAI selecionado localmente;
- rotas `structured`, `catalog`, `rag` e `hybrid` protegidas por autenticação;
- logs operacionais sem pergunta, prompt ou resposta em texto bruto.

O arquivo `.env`, bancos, índices e logs são locais e não devem ser enviados ao Git.

## Configuração local

No `.env`, configure a chave sem compartilhá-la:

```dotenv
LLM_PRIMARY_PROVIDER=openai
LLM_PRIMARY_OPENAI_MODEL=gpt-4.1-mini
LLM_FALLBACK_PROVIDER=none
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1024
RAG_FAISS_INDEX_DIR=data/index/faiss/staging/openai-v1
OPENAI_API_KEY=preencha-somente-no-env-local
```

## Inicialização

Em dois terminais, com o ambiente virtual ativo:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
python -m streamlit run app/streamlit_console.py
```

A API fica em `http://127.0.0.1:8000`, o Swagger em
`http://127.0.0.1:8000/docs` e o console em `http://127.0.0.1:8501`.

Crie ou autentique um usuário por `/auth/register` e `/auth/login`. Cole no
console somente o valor de `access_token`, sem a palavra `Bearer`.

## Perguntas de demonstração

Rota estruturada:

```text
Quantas contratações existem por modalidade em 2024?
```

Rota híbrida, homologada no piloto:

```text
Segundo o documento contratual, qual é o objeto do contrato PNCP 42498733000148-2-000841/2026?
```

Consulta de catálogo:

```text
Qual é o link e o estado de processamento do contrato PNCP 42498733000148-2-000841/2026?
```

Use o número de controle PNCP completo nas perguntas documentais. Um rótulo
curto como `Contrato 09/2026` não é uma chave global e pode ser ambíguo.

## Processamento sob demanda

A solicitação cria uma tarefa persistente; o download e a indexação não
ocorrem dentro da requisição HTTP. Inicie o worker separadamente:

```bash
python -m app.modules.documents.worker --watch
```

A fila usa lease renovável, recupera tarefas abandonadas e impede que um
worker antigo finalize uma tarefa reassumida. Mensagens brutas de exceção não
são persistidas nem devolvidas pela API.

## Limitação do piloto

O catálogo completo informa quais documentos existem, mas não significa que
todos foram baixados, extraídos e vetorizados. Somente os dois contratos do
piloto podem sustentar respostas documentais agora. Os demais devem ser
processados seletivamente pela fila antes de entrar no RAG.
