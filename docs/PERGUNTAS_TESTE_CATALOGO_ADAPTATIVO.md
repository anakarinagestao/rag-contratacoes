# Perguntas de teste — catálogo e Adaptive RAG

Use as perguntas abaixo no `POST /query/ask` ou no Streamlit. Elas usam um
controle que possui catálogo local de teste. Troque o número por outro controle
existente quando necessário.

## 1. Rota `catalog` — não chama LLM, FAISS nem FTS5

1. `Existe edital listado no PNCP para a contratação 00394502000144-1-009055/2026?`
2. `Qual é a URL do edital da contratação 00394502000144-1-009055/2026?`
3. `Quais documentos estão disponíveis no catálogo para a contratação 00394502000144-1-009055/2026?`
4. `O Termo de Referência da contratação 00394502000144-1-009055/2026 já foi processado?`
5. `Há algum documento pendente para download na contratação 00394502000144-1-009055/2026?`

Resultado esperado: `route: "catalog"`, `tools: ["document_catalog"]`,
`answer: null`, `sources: []` e itens em `external_documents`. Cada item deve
exibir `urlDownload` e `statusProcessamento` (`PENDENTE`, `INCOMPLETO` ou
`PROCESSADO`). O total pode ser zero quando o tipo solicitado não consta no
catálogo local daquele controle.

## 2. Rota `rag` — pergunta sobre conteúdo, não sobre disponibilidade

1. `No edital da contratação 00394502000144-1-009055/2026, quais serviços de manutenção estão previstos?`
2. `Segundo o Termo de Referência 150/2026, qual equipamento é objeto da contratação?`

Resultado esperado: `route: "rag"` (ou `hybrid` se também houver pedido de
valor, modalidade ou outro dado estruturado). Se não houver chunks suficientes,
o fallback pode preencher `external_documents`; isso não significa que o
catálogo substituiu a resposta documental.

## 3. Rota `hybrid` — dados estruturados e conteúdo documental

1. `Na contratação 00394502000144-1-009055/2026, informe o valor estimado e explique os serviços previstos no edital.`
2. `Para a contratação 00394452000103-1-015448/2026, informe a modalidade e, segundo o Termo de Referência, qual equipamento será adquirido.`

Resultado esperado: `route: "hybrid"`, ferramentas `structured_database` e
`rag_retrieval`. A resposta só é confiável se as fontes documentais retornadas
pertencerem ao mesmo número de controle informado.

## 4. Rota `structured` — somente registros SQL

1. `Quantas contratações existem por modalidade em 2026?`
2. `Liste as 10 contratações de 2026 com maior valor estimado.`

Resultado esperado: `route: "structured"`, `structured_result` preenchido,
sem fontes RAG. Esta rota retorna dados determinísticos e não produz resposta
textual por LLM nesta fase.
