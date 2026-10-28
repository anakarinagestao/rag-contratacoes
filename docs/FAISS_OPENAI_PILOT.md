# Piloto FAISS com embeddings OpenAI

O provedor de embeddings é independente do provedor de geração. A chave deve
existir somente no `.env` local e nunca deve ser enviada ao Git.

Configuração para OpenAI:

```dotenv
OPENAI_API_KEY=preencha-localmente
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1024
EMBEDDING_TIMEOUT_SECONDS=60
```

O identificador persistido no FAISS inclui provedor, modelo e dimensão. Um
índice Ecoa e um índice OpenAI nunca devem compartilhar o mesmo diretório.

## Construção controlada

Primeiro use um diretório de staging e um limite pequeno:

```bash
.venv/bin/python -m \
  src.base_estruturada.pipelines.pipeline_indexacao_faiss \
  --indice-dir data/index/faiss/staging/openai-v1 \
  --limite-chunks-novos 55 \
  --tamanho-lote 10 \
  --tamanho-checkpoint 55 \
  --origem piloto_operacional
```

Depois audite sem gerar novos embeddings:

```bash
.venv/bin/python -m \
  src.base_estruturada.pipelines.pipeline_indexacao_faiss \
  --auditar \
  --indice-dir data/index/faiss/staging/openai-v1
```

Somente selecione o staging em `RAG_FAISS_INDEX_DIR` depois de validar
contagens, dimensão, modelo e checksums. A indexação usa a chave configurada e
pode gerar custo externo; portanto, o limite deve ser mantido no primeiro
ensaio.
