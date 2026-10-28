# Artefatos grandes gerados

## Chunks PNCP de agosto de 2026

Os chunks canônicos de agosto de 2026 são saídas reproduzíveis da pipeline
`src.base_estruturada.pipelines.pipeline_chunks`. Eles permanecem disponíveis
fora da árvore Git no pacote da Release:

- tag: `chunks-pncp-2026-08-v1`;
- arquivo: `pncp-chunks-2026-08-v1.tar.gz`;
- SHA-256 do pacote:
  `82f3bcee5d0b281078300595a320a657d3d8caf975ff6f6332ab999973caef11`;
- conteúdo: quatro JSONL e quatro relatórios de processamento;
- total: 99.536 chunks válidos.

Inventário dos JSONL originais:

| Lote | Registros | Bytes | SHA-256 |
| --- | ---: | ---: | --- |
| `lote_001` | 26.287 | 86.327.431 | `721ee719a4a3274da3f5ca37b8f6c0ce25c17ad5aa04aa164a0aada06a4fe846` |
| `lote_002` | 20.925 | 64.112.516 | `0819a800b2b52c7308f209f01ece0de108e9fc8a48adeae03d95619e089e7330` |
| `lote_003` | 22.613 | 69.977.848 | `2e289fb7ae17ea25fa13a8498d983419485aa6b2576d50200931a5ad2fce422f` |
| `lote_004` | 29.711 | 95.089.579 | `7360a4f0d99306f37f3faaadb00e24c0fe6030ff453bad08aa279611bde4fe9a` |

## Recuperação

Depois de baixar o pacote da Release, valide e extraia na raiz do projeto:

```bash
sha256sum pncp-chunks-2026-08-v1.tar.gz
tar -xzf pncp-chunks-2026-08-v1.tar.gz -C data/chunks
```

O diretório `data/chunks/` é ignorado pelo Git. A remoção do índice não
reescreve o histórico: blobs presentes em commits anteriores continuam no
repositório até que a equipe aprove uma limpeza histórica coordenada.
