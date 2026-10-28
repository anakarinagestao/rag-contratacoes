# RAG-01 — Linha de base de recuperação documental

Data da medição: 23/10/2026.

## Objetivo

Estabelecer uma medição reproduzível antes de ampliar o índice FAISS. Esta
linha de base avalia exclusivamente o recuperador lexical SQLite FTS5/BM25 e
não chama LLM, embeddings ou serviços externos.

O banco é aberto com `mode=ro` e `PRAGMA query_only = ON`. A execução foi
validada comparando o SHA-256 do arquivo antes e depois do benchmark, sem
alteração.

## Corpus e julgamentos

- snapshot local: `data/debug/pncp_normative_demo.db`;
- corpus: `normativo-v1`;
- 13 documentos e 794 chunks normativos;
- conjunto versionado: `eval/questions.json`;
- 10 perguntas totais;
- 7 perguntas com prefixos de chunks esperados, usadas na recuperação;
- 3 perguntas de rota, metadado ou recusa, fora desta medição.

Os prefixos esperados são julgamentos parciais revisados. Por isso:

- `hit@k`: pelo menos uma fonte esperada apareceu até a posição `k`;
- `prefix_recall@k`: proporção dos prefixos esperados encontrados;
- `judged_precision@k`: proporção dos resultados que casam com os prefixos
  conhecidos, sem afirmar que os demais resultados são irrelevantes;
- `reciprocal_rank`: inverso da posição da primeira fonte esperada;
- MRR: média do `reciprocal_rank` entre as perguntas.

## Resultado inicial

| Métrica | Resultado |
|---|---:|
| Hit Rate@1 | 57,14% |
| Hit Rate@3 | 71,43% |
| Hit Rate@5 | 71,43% |
| Hit Rate@10 | 85,71% |
| Prefix Recall@5 | 51,19% |
| Prefix Recall@10 | 54,05% |
| MRR | 0,642857 |

A latência varia conforme a máquina e o cache do sistema. Na segunda
execução local, a média foi 23,6 ms, p50 9,9 ms e p95 72,5 ms por pergunta.

### Diagnóstico por pergunta

| Pergunta | Primeiro acerto | Observação |
|---|---:|---|
| N1 | posição 6 | o top 5 lexical não cobre a fonte esperada |
| N2 | sem acerto no top 10 | principal caso de regressão/comparação para FAISS/RRF |
| N3 | posição 1 | acerto lexical direto |
| N4 | posição 1 | encontra 1 de 4 prefixos esperados até top 10 |
| N5 | posição 1 | encontra 1 de 3 prefixos esperados até top 10 |
| N6 | posição 3 | acerto dentro do top 3 |
| N7 | posição 1 | acerto lexical direto |

## Como executar

Na raiz do repositório, com o snapshot normativo disponível:

```bash
python3 -m eval.benchmark \
  --database-url sqlite:///./data/debug/pncp_normative_demo.db \
  --top-k 1 3 5 10
```

Por padrão, o relatório JSON vai para a saída padrão. Para salvar um
artefato explicitamente:

```bash
python3 -m eval.benchmark \
  --database-url sqlite:///./data/debug/pncp_normative_demo.db \
  --top-k 1 3 5 10 \
  --output /tmp/pncp-rag-baseline.json
```

## Critério para a próxima comparação

Quando o corpus normativo estiver coberto pelo FAISS, executar as mesmas sete
perguntas e comparar BM25, FAISS e fusão RRF. A próxima etapa deve:

1. manter os mesmos julgamentos e cortes `k`;
2. registrar o identificador do snapshot e do índice;
3. demonstrar melhora em N1 e N2 sem regressão nas demais perguntas;
4. medir recuperação separadamente da qualidade da resposta gerada;
5. submeter novos julgamentos de relevância à revisão humana antes de usá-los
   como verdade de referência.

## Limitações

O arquivo SQLite de demonstração é um artefato local e não é versionado no
Git. Os resultados de latência não são comparáveis entre máquinas sem um
ambiente controlado. Esta linha de base não homologa FAISS, RRF, geração ou
fidelidade da resposta final.
