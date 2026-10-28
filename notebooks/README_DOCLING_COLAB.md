# Execucao Docling no Google Colab

Este diretorio contem notebooks para a etapa de extracao. O notebook usa as
funcoes de `src/base_estruturada/pipelines/pipeline_docling_lotes.py`; portanto,
a logica de lotes e checkpoint continua versionada no projeto, e o Colab fica
apenas como ambiente de execucao com GPU.

## Fluxo recomendado: lotes rastreaveis

Use `processar_lote_docling_colab.ipynb` junto com
`pipeline_lotes_documentos.py`. Cada ZIP tem um manifesto de lote e um
checkpoint Docling próprios, por exemplo `lote_001`.

## Fluxo mensal legado

1. Copie o projeto para uma pasta do seu Google Drive, por exemplo
   `MyDrive/pncp_projeto`.
2. Gere a fila localmente, executando a primeira celula do notebook ou o trecho
   equivalente indicado nele. Ela cria
   `data/checkpoints/extracao/<ano>/fila_docling_<ano_mes>.json`.
3. Crie um ZIP com o **conteudo** de `data/documents/` do periodo. A raiz do
   ZIP deve conter `original/` e, quando houver ZIPs expandidos, `expanded/`.
   Nao inclua `data/documents` como uma pasta adicional dentro do ZIP.
4. Envie esse ZIP para `MyDrive/pncp_lotes/<ano_mes>/documentos.zip`.
5. Abra `processar_documentos_docling_colab.ipynb` pelo navegador do Colab e
   selecione uma GPU T4 em *Runtime > Change runtime type*.

## O que o notebook faz

- copia o ZIP do Drive para `/content` e o extrai localmente;
- processa no maximo 1.000 PDFs por execucao, em microlotes de 50;
- grava Markdown em `data/extracted/docling/<ano>/<mes>/` no Drive;
- atualiza o checkpoint apos cada microlote;
- grava um relatorio em `data/normalized/inventarios/<ano>/`.

Se a sessao cair, rode o mesmo notebook outra vez. Arquivos cujo status seja
`concluido` sao ignorados. Para tentar novamente apenas erros, ajuste
`REPROCESSAR_ERROS = True` na celula de configuracao.

## Depois da extracao

Quando os arquivos Markdown e o checkpoint voltarem ao projeto, execute
`src/base_estruturada/pipelines/pipeline_chunks.py`. Ele le o checkpoint
Docling sem modificar o manifesto original do PNCP e gera o JSONL de chunks.
