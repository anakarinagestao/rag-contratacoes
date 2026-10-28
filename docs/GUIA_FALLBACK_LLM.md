# Fallback de LLM: Ecoa → Groq

## Objetivo

O sistema tenta a Ecoa como provedor principal para análise de perguntas e
geração RAG. Se ela tiver uma falha transitória, tenta a Groq como segunda
opção. Isso evita indisponibilidade total durante oscilações da Ecoa.

Embeddings e FAISS continuam exclusivamente na Ecoa. Não altere o modelo de
embedding do índice já existente sem reconstruí-lo por completo.

## Configuração local

Copie os campos abaixo para o arquivo `.env`, que não é versionado:

```env
LLM_FALLBACK_ENABLED=true
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-120b
GROQ_TIMEOUT_SECONDS=45
GROQ_TEMPERATURE=0
ECOA_TIMEOUT_SECONDS=30
```

Após alterar o `.env`, reinicie o servidor FastAPI. O processo em execução
não relê variáveis de ambiente já carregadas.

Instale as dependências:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Quando o fallback é usado

- timeout;
- erro de conexão;
- erro HTTP 5xx do provedor principal.

Erros de credencial, resposta vazia, resposta truncada ou plano inválido não
acionam Groq automaticamente. Eles exigem correção de configuração, prompt ou
modelo.

## Auditoria

O trace de geração registra `provider`, `model`, `fallback_used` e
`fallback_reason`. Quando houver troca de provedor, também é criado um evento
`llm_provider` nos logs JSONL.

O Query Analyzer continua validando a resposta com Pydantic. Portanto, uma
resposta da Groq só é aceita quando produz um plano válido.
