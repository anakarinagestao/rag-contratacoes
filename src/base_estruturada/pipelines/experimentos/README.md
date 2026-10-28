# Experimentos de extração

Esta pasta reúne scripts usados para comparar extratores ou validar hipóteses.
Eles não fazem parte do fluxo oficial de coleta, download, extração Docling,
chunking ou persistência da base.

- `pipeline_teste_extracao.py`: comparação pontual pypdf × Docling.
- `pipeline_teste_pymupdf.py`: comparação pontual pypdf × PyMuPDF.
- `pipeline_extracao_hibrida_legada.py`: fluxo antigo pypdf → candidatos Docling.

Os resultados desses scripts devem ser gravados somente em `data/experiments/`.
O fluxo oficial de texto é `pipeline_docling_lotes.py`, que escreve Markdown em
`data/extracted/docling/<ano>/<mês>/` e mantém sua fila/checkpoint em
`data/checkpoints/extracao/`.
