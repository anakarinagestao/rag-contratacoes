# Tarefas de processamento documental sob demanda

## Objetivo

Quando uma pergunta necessita de um documento ainda não indexado, o sistema pode
localizá-lo no catálogo do PNCP e criar uma tarefa de processamento. A tarefa é
executada por um worker local, fora da requisição HTTP, para não bloquear a API.

## Fluxo atual

```text
Catálogo PNCP → criação da tarefa → worker local → download → Docling
→ Markdown → chunks JSONL → SQLite/FTS5 → FAISS → consulta RAG
```

Os estados possíveis são `PENDENTE`, `EM_PROCESSAMENTO`, `CONCLUIDO` e `ERRO`.
Uma tarefa em erro pode ser reenviada à fila pela ação de retentativa.

## Pré-requisito do FAISS

O índice legado em `data/index/faiss/v1` é somente leitura. Antes de indexar
incrementalmente, preparar uma única vez o staging:

```powershell
.\venv\Scripts\python.exe -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
  --preparar-staging `
  --base-indice-dir data/index/faiss/v1 `
  --indice-dir data/index/faiss/staging/v1
```

No `.env`, apontar a aplicação e o worker para o staging:

```env
RAG_FAISS_INDEX_DIR=data/index/faiss/staging/v1
```

## Teste manual no Swagger

1. Iniciar a API e autenticar-se no Swagger.
2. Usar `POST /documents/catalog/{catalog_entry_id}/processing-tasks` para criar
   a primeira tarefa de um documento catalogado.
3. Em outro terminal, iniciar o worker:

```powershell
.\venv\Scripts\python.exe -m app.modules.documents.worker --watch --intervalo 4 --corpus-version-id 1
```

4. Consultar `GET /documents/processing-tasks/{tarefa_id}` até o status ser
   `CONCLUIDO`.
5. Se houver falha transitória, usar
   `POST /documents/processing-tasks/{tarefa_id}/retry`. Não criar outra tarefa
   pelo endpoint do catálogo para o mesmo item.
6. Repetir a pergunta original em `/query/ask` e verificar a presença de
   evidências em `sources`.

## Formatos atualmente aceitos

- PDF direto;
- DOCX direto;
- ZIP, inclusive ZIP aninhado, que contenha PDF ou DOCX.

Arquivos XLSX, imagens, DWG e RAR ainda não são processados pelo worker. Eles
devem resultar em erro controlado ou ficar para uma evolução específica.

## Rastreabilidade e limpeza

Cada execução usa a pasta `data/adaptive_tasks/{tarefa_id}/`:

```text
original/     arquivo baixado do PNCP
expanded/     conteúdo expandido de ZIP, quando houver
markdown/     texto estruturado produzido pelo Docling
chunks.jsonl  chunks gerados
relatorio.json resumo de persistência e vetores novos
```

Essas pastas são artefatos locais de auditoria e estão no `.gitignore`. Elas
podem ser apagadas depois de validar a tarefa, sem apagar os dados já
persistidos no SQLite e no FAISS. O índice ativo em
`data/index/faiss/staging/v1` não deve ser apagado.
