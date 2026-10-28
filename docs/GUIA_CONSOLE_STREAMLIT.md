# Console local de testes RAG

O console Streamlit é uma interface local para testar a mesma API FastAPI já
utilizada no Swagger. Ele não altera a autenticação, não grava token no disco e
não substitui a API: apenas facilita as consultas durante o desenvolvimento.

## Preparação

Instale as dependências de desenvolvimento uma única vez:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Em um terminal, inicie a API:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Em outro terminal, inicie a interface:

```powershell
.\venv\Scripts\streamlit.exe run app\streamlit_console.py
```

Abra o endereço exibido pelo Streamlit (normalmente `http://localhost:8501`).
Na barra lateral, informe a URL da API e cole um `access_token` obtido por
`POST /auth/login`. O token permanece apenas na sessão aberta do navegador.

## O que testar

- **Perguntar** chama `POST /query/ask` e mostra resposta, rota/análise, SQL,
  fontes e eventual catálogo de documentos.
- **Catálogo PNCP** chama `GET /query/catalog/documents`. Essa tela continua
  útil mesmo quando PNCP ou Ecoa estão indisponíveis, pois consulta apenas o
  catálogo já persistido no banco local.
- **Casos de teste** apresenta os cenários fixos usados nos testes offline.
- **Histórico** guarda somente as chamadas feitas na sessão atual do navegador.

## Testes offline da rota

Os casos em `tests/fixtures/query_cases.json` simulam respostas problemáticas
já vistas na LLM. Eles verificam o Analyzer sem rede, token, Ecoa, PNCP ou
banco:

```powershell
.\venv\Scripts\python.exe -m unittest tests.unit.test_query_cases_offline -v
```

Quando a Ecoa voltar, use as mesmas perguntas no console. Se uma delas gerar um
plano inválido, o detalhe de validação exibido pela API mostra qual campo do
plano precisa ser ajustado.
