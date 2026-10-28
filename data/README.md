# Dados da base estruturada

As pastas abaixo separam dados de produção, estado operacional e resultados de
experimentos. Arquivos grandes e dados obtidos do PNCP não são versionados.

| Pasta | Finalidade |
| --- | --- |
| `normalized/contratos/` e `normalized/contratacoes/` | JSONs coletados por período e manifestos de documentos. Não sobrescrever manualmente. |
| `checkpoints/` | Estado retomável de coleta, listagem, download e extração. Pode mudar a cada execução. |
| `documents/original/` | Arquivos recebidos do PNCP, organizados por origem, ano e mês de coleta. |
| `documents/expanded/` | Conteúdo extraído de ZIP/RAR; preserva o original em `documents/original/`. |
| `extracted/docling/` | Markdown oficial produzido pelo Docling. |
| `chunks/` | JSONL de chunks gerado exclusivamente a partir do Markdown oficial. |
| `experiments/` | Comparações e artefatos legados (pypdf, PyMuPDF e fluxo híbrido). Nunca usar como entrada da base final. |
| `manual/` | Material de outras frentes da equipe; não alterar nesta pipeline. |

Regra prática: o manifesto retornado pela API é a referência da listagem. Os
resultados de download, expansão e extração devem permanecer em checkpoints ou
artefatos derivados, sem modificar o manifesto original.
