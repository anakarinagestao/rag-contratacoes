# Guia prático de execução das pipelines PNCP

Este documento explica como executar a base de contratos, contratações e documentos do PNCP. Ele descreve tanto **o que fazer** quanto **por que cada etapa existe**.

O objetivo é permitir que uma pessoa nova no projeto consiga acompanhar o fluxo sem precisar conhecer todos os detalhes internos do código.

---

## 1. Visão geral

O projeto transforma dados públicos do PNCP em duas bases complementares:

1. **Base estruturada**: contratos, contratações, órgãos, valores, datas e demais campos JSON.
2. **Base textual**: documentos como contratos, editais e termos de referência convertidos para Markdown e, depois, para chunks de RAG.

O fluxo completo é:

```text
Coleta de contratos/contratações
        ↓
Listagem dos documentos disponíveis no PNCP
        ↓
Seleção e criação de lotes
        ↓
Download e expansão de ZIP/RAR
        ↓
Conversão para Markdown com Docling no Colab
        ↓
Validação do texto
        ↓
Geração de chunks
        ↓
Persistência no SQLite + FTS5
        ↓
Indexação incremental no FAISS
```

Nem toda etapa precisa ser repetida. O princípio é simples: **se o arquivo de saída já existe e está válido, inicie na próxima etapa**.

---

## 2. Conceitos importantes

| Termo | Significado prático |
| --- | --- |
| Período | Um mês de coleta, como `2026_08`. |
| Manifesto | JSON que lista os documentos disponíveis e suas URLs no PNCP. Não contém necessariamente o arquivo baixado. |
| Checkpoint | JSON de estado. Registra progresso, erros, downloads e conversões para permitir retomada. |
| Lote | Grupo congelado de documentos, por exemplo até 500 arquivos. |
| Microlote | Subgrupo processado pelo Docling, por exemplo 50 arquivos por checkpoint. |
| Markdown | Texto estruturado extraído pelo Docling, usado depois para gerar chunks. |

### Regra de ouro

O manifesto original do PNCP não deve ser modificado com estados de execução. Download, expansão e erros devem ficar em checkpoints e arquivos derivados.

---

## 3. Etapa 1 — coletar contratos e contratações

### Pipeline

```text
src/base_estruturada/pipelines/pipeline_documentos.py
```

### O que ela faz

Consulta a API do PNCP por período e salva registros estruturados de contratos e contratações.

### Configuração principal

```python
PERIODO = PeriodoColeta(ano=2026, mes=8)
TAMANHO_PAGINA_CONTRATO = 500
TAMANHO_PAGINA_CONTRATACAO = 50
```

### Saídas

```text
data/normalized/contratos/2026/contratos_2026_08.json
data/normalized/contratacoes/2026/contratacoes_2026_08.json
```

### Quando rodar

- Quando o mês ainda não foi coletado.
- Quando faltam páginas que deram erro na coleta.
- Quando se quer complementar dados novos.

### Quando não rodar

Não rode novamente apenas para baixar documentos. Se o JSON mensal já está correto, passe para a listagem.

---

## 4. Etapa 2 — listar os documentos disponíveis

### Pipeline

```text
src/base_estruturada/pipelines/pipeline_listagem_documentos.py
```

### O que ela faz

Para cada contrato ou contratação, consulta o endpoint `/arquivos` do PNCP. Ela descobre quais documentos existem, seus tipos, títulos, sequenciais e URLs.

Ela **não baixa arquivos** nesta etapa.

### Saídas

```text
data/normalized/contratos/2026/contratos_documentos_2026_08.json
data/normalized/contratacoes/2026/contratacoes_documentos_2026_08.json
```

Esses arquivos são os manifestos usados pelas etapas seguintes.

### Checkpoint da listagem

```text
data/checkpoints/documentos/contratos/2026/estado_documentos_2026_08.json
data/checkpoints/documentos/contratacoes/2026/estado_documentos_2026_08.json
```

O checkpoint registra, por entidade, se a listagem foi concluída ou se ocorreu erro.

### O que acontece ao rodar novamente

- Itens já presentes no manifesto são pulados.
- Itens que deram erro de listagem são tentados novamente.
- Itens ainda não listados também podem ser processados, respeitando o limite configurado.

Um erro `404` persistente não costuma ser recuperado com tentativas repetidas. Após algumas tentativas, ele deve ser registrado para revisão manual.

---

## 5. Etapa 3 — inventariar tipos prioritários (opcional)

### Pipeline

```text
src/base_estruturada/pipelines/pipeline_documentos_prioritarios.py
```

### O que ela faz

Gera um relatório mostrando quantos documentos existem por tipo. A seleção inicial do projeto é:

```text
Contrato
Edital
Termo de Referência
Ata de Registro de Preços
```

### Quando usar

- Antes de iniciar downloads em massa.
- Para estimar volume e tempo de processamento.
- Para validar se os nomes de tipo devolvidos pelo PNCP correspondem ao filtro.

### Quando pular

É uma etapa de conferência. O fluxo de lotes pode funcionar sem ela.

---

## 6. Etapa 4 — criar e baixar lotes de documentos

### Pipeline

```text
src/base_estruturada/pipelines/pipeline_lotes_documentos.py
```

Essa é a pipeline oficial para download por lote. Não use a antiga `pipeline_download_documentos.py` para o fluxo novo de equipe.

### Por que usar lotes

Os arquivos físicos ficam organizados por contrato, e não por ordem de download. Sem um lote explícito, seria necessário escolher pastas manualmente para criar um ZIP.

O lote resolve isso: ele congela a lista exata dos documentos que pertencem a uma execução.

### 6.1 Criar um lote sem baixar

```python
NOME_LOTE = "lote_001"
LIMITE_DOCUMENTOS = 500
ACAO = "criar"
```

Saída:

```text
data/checkpoints/lotes_documentos/2026_08/lote_001.json
```

Use esta opção quando quiser preparar a divisão de trabalho antes de iniciar downloads.

### 6.2 Criar e baixar o lote

```python
NOME_LOTE = "lote_001"
LIMITE_DOCUMENTOS = 500
ACAO = "criar_e_baixar"
```

Essa ação:

1. Cria o lote, caso ele não exista.
2. Carrega o lote existente, caso ele já exista.
3. Baixa somente os documentos pertencentes a ele.
4. Identifica formato real do arquivo: PDF, ZIP, DOCX, XLSX etc.
5. Expande ZIPs, inclusive ZIPs aninhados quando possível.
6. Atualiza o checkpoint de documentos.
7. Gera automaticamente um ZIP portável do lote.

### 6.3 Reprocessar somente erros do lote

```python
NOME_LOTE = "lote_001"
ACAO = "reprocessar_erros"
```

Essa ação não seleciona documentos novos. Ela tenta apenas arquivos cujo download falhou dentro daquele lote.

### Arquivos produzidos

```text
data/documents/original/<origem>/<ano>/<mês>/
data/documents/expanded/<origem>/<ano>/<mês>/
data/checkpoints/lotes_documentos/2026_08/lote_001.json
data/lotes_documentos/2026_08/documentos_lote_001.zip
```

O JSON do lote é o registro operacional: informa quais documentos pertencem ao lote, quais baixaram, quais falharam e onde está o ZIP.

---

## 7. Etapa 5 — processar o lote com Docling no Google Colab

### Notebook

```text
notebooks/processar_lote_docling_colab.ipynb
```

### Preparação no Drive

O ZIP do lote deve ser enviado ao Drive compartilhado, por exemplo:

```text
MyDrive/pncp_rag/lotes/2026_08/documentos_lote_001.zip
```

O código do projeto e os JSONs/checkpoints também devem estar em:

```text
MyDrive/pncp_rag/projeto/
```

### Configuração do notebook

```python
ANO = 2026
MES = 8
NOME_LOTE = "lote_001"
LIMITE_DOCUMENTOS = 100
TAMANHO_MICROLOTE = 50
PERFIL = "qualidade"
TIMEOUT_DOCUMENTO = 180
REPROCESSAR_ERROS = False
REPROCESSAR_TIMEOUTS = False
```

### O que esses limites significam

```text
ZIP físico: até 500 documentos do lote
Execução do Docling: até 100 documentos por vez
Microlote: 50 documentos antes de salvar checkpoint
```

### Retomada

O notebook grava um checkpoint exclusivo por lote:

```text
data/checkpoints/extracao/2026/estado_docling_2026_08_lote_001.json
```

Se processar 100 documentos hoje e executar novamente amanhã, o Docling pula os concluídos e seleciona os próximos pendentes.

Se a sessão do Colab cair, execute novamente o notebook com as mesmas configurações. Não apague o checkpoint sem motivo.

Se um arquivo muito pesado, como uma planta anexada, ultrapassar
`TIMEOUT_DOCUMENTO`, ele recebe status `timeout` e o Docling segue para o
próximo. O padrão é não repetir esse caso; use `REPROCESSAR_TIMEOUTS = True`
somente para uma nova tentativa consciente.

### Saídas

```text
data/extracted/docling/2026/08/
data/normalized/inventarios/2026/extracao_docling_2026_08_lote_001.json
```

---

## 8. Etapa 6 — validar o Markdown

Antes de gerar chunks para todo o lote, abra uma amostra de arquivos Markdown e confira:

- texto legível;
- título e seções preservados;
- tabelas importantes minimamente compreensíveis;
- ausência de arquivo vazio;
- relação entre o Markdown e o documento original.

O objetivo não é exigir tabela visualmente perfeita. O importante é preservar contexto textual suficiente para recuperação semântica.

---

## 9. Etapa 7 — gerar chunks

### Pipeline

```text
src/base_estruturada/pipelines/pipeline_chunks.py
```

Ela lê o Markdown oficial do Docling e gera arquivos JSONL com texto dividido em partes menores e metadados de origem.

Essa etapa deve ser executada somente depois de validar uma amostra do Markdown.

Saída esperada:

```text
data/chunks/
```

Esse diretório contém artefatos gerados e não deve ser versionado no Git.
Para compartilhar um lote, publique um pacote verificável em uma Release ou
no armazenamento externo da equipe, acompanhado de SHA-256 e inventário.

Exemplo por lote:

```powershell
python -m src.base_estruturada.pipelines.pipeline_chunks `
  --periodo 2026/08 --lote lote_001 --versao v1
```

Essa pipeline mostra uma barra de progresso baseada nos Markdown elegíveis e
produz `data/chunks/v1/2026/chunks_2026_08_lote_001.jsonl`.

---

## 10. Etapa 8 — persistir chunks e atualizar FTS5

### Pipeline

```text
src/base_estruturada/pipelines/pipeline_persistencia_documentos.py
```

Ela importa o JSONL canônico para `Document` e `DocumentChunk` no SQLite. O
FTS5 é atualizado automaticamente pelo banco. A execução é idempotente:
`chunk_id` já existentes são mantidos, sem duplicação.

```powershell
python -m src.base_estruturada.pipelines.pipeline_persistencia_documentos `
  --periodo 2026/08 --lote lote_001 --versao v1 `
  --corpus-version-id 1
```

Use o mesmo `corpus-version-id` para lotes do mesmo corpus mensal. A barra
mostra o andamento da leitura/persistência do JSONL.

---

## 11. Etapa 9 — indexar embeddings no FAISS

### Pipeline

```text
src/base_estruturada/pipelines/pipeline_indexacao_faiss.py
```

O FAISS recebe vetor e `chunk_id`; o texto continua no SQLite. Para processar
um lote inteiro, omita limites. Para avançar em blocos sem repetir chunks já
indexados, use `--limite-chunks-novos`:

```powershell
python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
  --arquivo-chunks data/chunks/v1/2026/chunks_2026_08_lote_001.jsonl `
  --indice-dir data/index/faiss/v1 `
  --origem 2026_08_lote_001 `
  --limite-chunks-novos 500 `
  --tamanho-lote 10
```

Repita o comando para o próximo bloco. A barra considera apenas embeddings
novos; o índice é salvo a cada microlote e retoma pelos `chunk_id` pendentes.

---

## 12. Trabalho em equipe

### O que é compartilhado

- Um Google Drive único para documentos, checkpoints e Markdown.
- Uma planilha de acompanhamento por período e lote.
- O mesmo código-base no repositório.

### Regra de segurança

Não processe o mesmo período/lote em duas sessões diferentes ao mesmo tempo. Duas pessoas escrevendo o mesmo checkpoint podem causar conflito.

### Exemplo de divisão

| Período | Responsável | Lote | Etapa atual |
| --- | --- | --- | --- |
| 2026_08 | Pessoa A | lote_001 | Docling |
| 2026_08 | Pessoa A | lote_002 | download |
| 2026_07 | Pessoa B | lote_001 | listagem |

---

## 13. Decisão rápida: qual pipeline usar?

| Situação | O que executar |
| --- | --- |
| Ainda não tenho contratos/contratações do mês | `pipeline_documentos.py` |
| Tenho os JSONs, mas não sei quais documentos existem | `pipeline_listagem_documentos.py` |
| Quero saber o volume por tipo documental | `pipeline_documentos_prioritarios.py` |
| Quero preparar 500 documentos para uma pessoa | `pipeline_lotes_documentos.py` com `ACAO = "criar"` |
| Quero baixar os documentos do lote | `pipeline_lotes_documentos.py` com `ACAO = "criar_e_baixar"` |
| Alguns downloads daquele lote falharam | `pipeline_lotes_documentos.py` com `ACAO = "reprocessar_erros"` |
| Quero converter PDFs já baixados | `processar_lote_docling_colab.ipynb` |
| Já tenho Markdown validado | `pipeline_chunks.py` |
| Já tenho JSONL de chunks | `pipeline_persistencia_documentos.py` |
| Já persisti os chunks no SQLite | `pipeline_indexacao_faiss.py` |

---

## 14. O que não fazer

1. Não apagar checkpoints para “tentar de novo” sem entender o impacto.
2. Não alterar manualmente manifestos originais do PNCP.
3. Não criar ZIP escolhendo pastas manualmente; use o ZIP automático do lote.
4. Não executar duas pessoas no mesmo lote.
5. Não usar resultados de `data/experiments/` como corpus oficial.
6. Não pular a validação de uma amostra do Markdown antes de gerar muitos chunks.

---

## 15. Checklist final por lote

```text
[ ] Manifestos do período existem
[ ] Lote foi criado e registrado
[ ] Download terminou ou erros foram registrados
[ ] ZIP automático do lote foi gerado
[ ] ZIP foi enviado ao Drive compartilhado
[ ] Docling processou os documentos pendentes
[ ] Markdown foi validado por amostragem
[ ] Chunks foram gerados
[ ] JSONL foi persistido no SQLite / FTS5
[ ] Chunks novos foram indexados no FAISS
[ ] Planilha da equipe foi atualizada
```
