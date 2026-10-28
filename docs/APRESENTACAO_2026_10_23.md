# Apresentação segura — Adaptive RAG PNCP/RJ

Este é o checkpoint recomendado para a apresentação. A branch homologada é
`integration/adaptive-rag-entrega-20261023`; ela combina a principal com o
trabalho mais recente sem alterar a branch principal.

## O que demonstrar ao vivo

1. Abra `http://127.0.0.1:8501` e entre pelo formulário lateral.
2. Pergunte `Quantas contratações existem por modalidade em 2024?`.
3. Pergunte `Quantas atas de registro de preços foram publicadas em 2024?`.
4. Pergunte `Quantos PCAs existem em 2025?`.
5. Pergunte `Qual o valor total planejado nos itens dos PCAs de 2025?`.
6. Em uma nova conversa, pergunte `Quantos contratos foram assinados em 2025?`
   e depois `E somente os estaduais?`.

Resultados conferidos no snapshot local:

- 22.685 atas publicadas em 2024;
- 185 PCAs em 2025;
- 74.605 itens dentro dos PCAs de 2025;
- R$ 46.359.966.536,52 de valor total planejado nesses itens;
- 59.995 contratos assinados em 2025;
- 8.218 desses contratos com esfera estadual;
- 5.606 atas vigentes e com adesão permitida em 23/10/2026.

Na pergunta contextual, abra **Detalhes técnicos** e mostre que o sistema
preservou `ano=2025` e `campo_data=data_assinatura`, acrescentando `esfera=E`.

## Inicialização

Em terminais separados:

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
source .venv/bin/activate
python -m streamlit run app/streamlit_console.py \
  --server.address 127.0.0.1 --server.port 8501
```

Antes da apresentação, execute o pré-voo somente leitura:

```bash
python -m scripts.demo_preflight
```

O resultado deve mostrar `ready: true`, base normalizada com `query_only: true`
e FAISS com 55 vetores de dimensão 1024.

## O que está operacional

- Chat autenticado em Streamlit.
- Rotas Structured, Aggregate, RAG, Hybrid e Catalog.
- SQL Tool para contratações, contratos, itens, resultados, atas e PCA.
- Continuidade contextual mínima e isolada por conversa.
- Alertas de qualidade para valores financeiros suspeitos.
- Base normalizada aberta somente para leitura.
- FAISS piloto com 55 chunks e recuperação híbrida.
- Tratamento seguro de FAISS indisponível.
- Fila documental assíncrona com HTTP 202, `task_id` e `status_url`.

## O que mostrar como implementado e testado, não como carga atual

O `app.db` deste computador possui 2 documentos e 55 chunks, mas atualmente
não possui entradas em `document_catalog_entries`. Portanto, o fluxo automático
Planner → Catálogo → Fila está implementado e coberto por testes, mas depende
da importação do catálogo para ser demonstrado com dados reais neste snapshot.

Não afirme na apresentação que as 549.171 referências documentais estão
carregadas no `app.db` atual.

## Limitações que devem ser declaradas

- A cobertura das atas ainda é parcial.
- `pca_contratacao_link` possui zero vínculos confirmados; o sistema não
  inventa associação entre planejamento e execução.
- Valores anômalos são preservados e sinalizados, não corrigidos silenciosamente.
- O FAISS é um piloto de 55 chunks, não o catálogo completo.
- O histórico do Chat permanece somente na sessão do Streamlit.
- O corpus normativo completo continua em uma base de demonstração separada.

## Roteiro falado de três minutos

1. **Problema:** dados de contratações estão distribuídos entre registros
   estruturados, planejamento, atas e documentos.
2. **Arquitetura:** preservamos a resposta original na RAW, normalizamos para
   SQL auditável e usamos o Analyzer/Planner para escolher Structured, RAG ou
   Hybrid.
3. **Demonstração:** execute a contagem por modalidade, PCA e a pergunta de
   acompanhamento contextual.
4. **Confiabilidade:** mostre fontes, filtros, alertas e o identificador da
   execução. Explique que cálculos são feitos em SQL, não pelo LLM.
5. **Limites honestos:** destaque a cobertura parcial, o FAISS piloto e a
   ausência de vínculo PCA×contratação confirmado.

Este é um ponto adequado para apresentar. OCR em massa, expansão completa do
FAISS, persistência permanente do Chat e correspondência PCA×contratação
devem permanecer no backlog e não bloquear a demonstração atual.
