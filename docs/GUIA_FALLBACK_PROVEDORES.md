# Fallback de LLM: Ecoa → OpenAI ou Groq

## Regra única do projeto

O Query Analyzer e a geração RAG usam a Ecoa como provedor primário. Em uma
falha transitória, ambos usam o mesmo fallback definido localmente no `.env`.

```text
Ecoa
  ↓ timeout, erro de conexão, HTTP 429 ou HTTP 5xx
OpenAI ou Groq, conforme LLM_FALLBACK_PROVIDER
```

Embeddings e FAISS permanecem exclusivamente na Ecoa. Não altere o modelo de
embedding de um índice existente sem reconstruí-lo.

## Configuração por integrante

Nenhuma chave entra no Git. Cada integrante escolhe localmente um provedor:

```env
# Opções: none, openai, groq
LLM_FALLBACK_PROVIDER=groq
ECOA_TIMEOUT_SECONDS=30
LLM_FALLBACK_TIMEOUT_SECONDS=45

# Para Groq
GROQ_API_KEY=
LLM_FALLBACK_GROQ_MODEL=openai/gpt-oss-120b

# Para OpenAI
OPENAI_API_KEY=
LLM_FALLBACK_OPENAI_MODEL=gpt-4.1-mini
```

Use `none` quando quiser testar somente a Ecoa. Após alterar o `.env`,
reinicie o FastAPI.

## Comportamento e auditoria

- erros de credencial, resposta truncada ou JSON inválido não acionam fallback;
- quando os dois provedores falham, a API retorna HTTP 503;
- o trace de geração registra `provider`, `model`, `fallback_used` e
  `fallback_reason`;
- as métricas de desempenho preservam o provedor utilizado sem registrar
  prompts, respostas ou chaves.
