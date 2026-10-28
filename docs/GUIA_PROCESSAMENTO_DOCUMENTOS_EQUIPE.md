# Guia de processamento de documentos PNCP — equipe

Este guia define como a equipe coleta, baixa e converte documentos do PNCP sem duplicar trabalho ou perder o andamento de cada período.

## Princípio central

O repositório contém o **código**. Os documentos baixados, Markdown e checkpoints ficam em um **único Google Drive compartilhado**. Cada integrante pode usar seu VS Code e seu Colab, mas todos devem apontar para essa mesma pasta de dados.

```text
Google Drive compartilhado: PNCP_RAG
├── projeto/                  # código e JSONs necessários à execução
├── lotes/                    # ZIPs de documentos enviados ao Colab
└── controle/                 # planilha de responsabilidade por período
```

## Unidade de trabalho

A unidade padrão é um mês de coleta, identificado como `AAAA_MM`.

```text
2026_08 → uma pessoa responsável
2026_07 → outra pessoa responsável
2026_06 → outra pessoa responsável
```

Não execute duas sessões do Colab para o mesmo período ao mesmo tempo. O checkpoint Docling é mensal; duas sessões poderiam sobrescrever o estado uma da outra.

Se um mês precisar ser dividido, registre lotes explícitos na planilha, por exemplo `2026_08_lote_001` e `2026_08_lote_002`. Não use divisões informais.

## Planilha de controle

Criar uma planilha compartilhada em `controle/` com, no mínimo, estas colunas:

| Período | Responsável | Download | Docling | Validação | Chunks | Observações |
| --- | --- | --- | --- | --- | --- | --- |
| 2026_08 | Nome | concluído | em andamento | pendente | pendente | lote 001 concluído |

Antes de iniciar, o responsável marca o período como **em andamento**. Ao terminar cada etapa, atualiza a planilha. A planilha controla a equipe; os checkpoints controlam a retomada técnica do código.

## Fluxo de um período

```text
Coletar contratos e contratações
        ↓
Listar documentos do PNCP
        ↓
Baixar documentos prioritários e expandir ZIP/RAR
        ↓
Preparar um lote de documentos
        ↓
Processar com Docling no Colab
        ↓
Validar Markdown e gerar chunks
```

Os tipos prioritários iniciais são: **Contrato**, **Edital**, **Termo de Referência** e **Ata de Registro de Preços**.

## Etapa 1 — coleta e download local

No VS Code, o responsável executa as pipelines para o período que recebeu. Elas geram ou atualizam:

```text
data/normalized/                 # JSONs de contratos, contratações e manifestos
data/checkpoints/documentos/     # estado do download e da expansão
data/documents/original/         # arquivo baixado do PNCP
data/documents/expanded/         # conteúdo de ZIP/RAR expandido
```

Não altere manualmente o manifesto retornado pelo PNCP. Status de download, expansão e erros ficam nos checkpoints.

### Preparação antecipada pelo responsável pela base

É permitido gerar a **listagem de documentos** de todos os meses antes de
distribuir o trabalho. Essa etapa consulta o PNCP e salva somente os
manifestos; ela não baixa PDFs, ZIPs ou RARs.

Também é possível pré-criar lotes com `ACAO = "criar"` em
`pipeline_lotes_documentos.py`. Isso congela, por exemplo, os 500 documentos
de `lote_001` sem iniciar downloads. Outro integrante pode executar depois o
mesmo `lote_001` com `ACAO = "criar_e_baixar"`.

## Etapa 2 — preparar o lote para o Colab

Para um período, por exemplo `2026_08`:

1. Garanta que `projeto/data/normalized/` e `projeto/data/checkpoints/` no Drive estejam atualizados com o trabalho desse período.
2. Crie um ZIP contendo os documentos do lote.
3. Envie-o para `lotes/2026_08/`.

O ZIP precisa conter `original/` e `expanded/` diretamente na raiz:

```text
documentos_lote_001.zip
├── original/
│   ├── contratos/
│   └── contratacoes/
└── expanded/
    ├── contratos/
    └── contratacoes/
```

Não coloque uma pasta extra `data/documents/` dentro do ZIP.

Para o primeiro processamento de um mês, use 20 documentos para validação. Depois, aumente para 100, 500 ou 1.000 documentos por execução, conforme o tempo disponível.

## Etapa 3 — executar Docling no Google Colab

1. Abra `notebooks/processar_lote_docling_colab.ipynb` pelo navegador.
2. Em **Ambiente de execução**, selecione **T4 GPU**.
3. Monte o Drive na primeira célula.
4. Na célula de configuração, informe ano, mês, arquivo ZIP e limite:

```python
ANO = 2026
MES = 8
LIMITE_DOCUMENTOS = 500
TAMANHO_MICROLOTE = 50
PERFIL = "qualidade"
TIMEOUT_DOCUMENTO = 180
REPROCESSAR_ERROS = False
REPROCESSAR_TIMEOUTS = False
```

5. Execute as células em ordem.

O notebook copia o ZIP para o disco temporário do Colab, processa os PDFs e salva o resultado no Drive.

## Checkpoint e retomada

Após cada microlote, o notebook atualiza:

```text
projeto/data/checkpoints/extracao/<ano>/estado_docling_<ano_mes>_lote_<nnn>.json
```

Se a sessão cair, abra o mesmo notebook e execute novamente. Arquivos com status `concluido` serão ignorados; somente pendências serão processadas.

Para tentar novamente apenas documentos que falharam, ajuste:

```python
REPROCESSAR_ERROS = True
```

Arquivos que ultrapassarem `TIMEOUT_DOCUMENTO` recebem status `timeout` e o
processamento segue para o próximo arquivo. Por padrão eles também não são
tentados de novo; só habilite abaixo após revisar se o caso merece nova
tentativa:

```python
REPROCESSAR_TIMEOUTS = True
```

Não apague o checkpoint sem antes verificar os resultados. Apagá-lo faz o Docling considerar todos os PDFs do período como pendentes novamente.

## Saídas esperadas

```text
projeto/data/extracted/docling/<ano>/<mes>/  # Markdown produzido pelo Docling
projeto/data/checkpoints/extracao/<ano>/     # andamento e erros
projeto/data/normalized/inventarios/<ano>/   # relatório da execução
```

Depois de validar uma amostra, execute `pipeline_chunks.py` para gerar o JSONL que alimentará o índice vetorial.

## Regras rápidas

1. Um período tem somente um responsável ativo.
2. O Drive compartilhado é a referência oficial dos dados gerados.
3. O Git versiona código; não é usado para sincronizar PDFs, Markdown ou checkpoints.
4. Não sobrescreva ou apague um checkpoint de outro integrante.
5. Registre toda mudança de etapa na planilha de controle.
6. Sempre valide uma amostra pequena antes de aumentar o lote.
