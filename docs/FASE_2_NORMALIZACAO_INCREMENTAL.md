# PNCP/RJ — Fase 2: normalização incremental e idempotente

Data: 2026-10-19
Entrada: contrato normalizado validado na Fase 1
Estado: implementação e piloto integral da RAW cumulativa concluídos

## 1. Objetivo e limites

A Fase 2 permite atualizar um banco normalizado existente sem reconstruir nem
sobrescrever a RAW. A carga separa quatro identidades:

- entidade canônica RAW: `base_id + resource_type + external_id`;
- versão RAW: `raw_asset.id` e `payload_hash`;
- execução de normalização: `normalization_run.id`;
- entidade normalizada: chave de negócio definida por sua tabela.

Esta etapa não executa coleta HTTP, não migra o banco RAW oficial, não
remove payloads e não cria associação automática PCA×contratação.

## 2. Seleção da versão RAW vigente

O seletor agrupa registros por identidade canônica, independentemente do nome
do coletor em `source`. A versão corrente é escolhida na seguinte ordem:

1. timestamp de atualização válido fornecido pelo PNCP;
2. instante de chegada como desempate;
3. `rowid` apenas como último fallback.

Uma versão antiga recebida depois de uma versão nova não regride o estado.
Registros legados sem colunas operacionais continuam aceitos; nesses casos o
seletor tenta extrair a data do JSON e, se ela não existir, usa a chegada.

`raw_observation` não é requisito para normalizar. Quando existir, ela continua
registrando observações da coleta; a entidade é materializada a partir da
versão correspondente em `raw_asset`.

## 3. Fluxo transacional

```text
RAW somente leitura
  -> seleção das versões vigentes
  -> transformação em staging dentro de /tmp
  -> comparação por chave + payload_hash
  -> transação BEGIN IMMEDIATE no banco normalizado
  -> INSERT / UPDATE / UNCHANGED
  -> atualização da linhagem corrente
  -> reconciliação de cobertura
  -> foreign_key_check + quick_check
  -> COMMIT
```

Qualquer exceção antes do `COMMIT` desfaz a reconciliação. O staging é
sempre temporário e removido ao final.

## 4. Semântica da reconciliação

- `inserted`: chave normalizada ainda não existente;
- `updated`: mesma chave com `payload_hash` corrente diferente;
- `unchanged`: mesma chave e mesmo `payload_hash`;
- `skipped`: registro conscientemente fora do escopo, como PCA externo ao
  inventário RJ;
- `rejected`: payload que não satisfaz o contrato da normalização.

`normalization_reconciliation` registra as cinco contagens por tabela e por
execução. Apenas uma linhagem fica com `is_current = 1` para cada entidade;
as anteriores permanecem preservadas.

Ausência no snapshot de entrada não é interpretada como exclusão. Essa regra
evita apagar dados quando a API ou um recorte estiver incompleto.

## 5. Modos do comando

Criação inicial, que recusa sobrescrever um arquivo existente:

```bash
python3 -m src.normalize_pncp \
  --mode build \
  --raw-database CAMINHO_RAW.db \
  --output-database CAMINHO_NORMALIZADA.db \
  --scope-inventory config/pncp_rj_scope_inventory.json
```

Atualização de um banco normalizado existente:

```bash
python3 -m src.normalize_pncp \
  --mode update \
  --raw-database CAMINHO_RAW_CUMULATIVA.db \
  --output-database CAMINHO_NORMALIZADA.db \
  --scope-inventory config/pncp_rj_scope_inventory.json \
  --known-empenho-gaps 4
```

Para operação regular, `update` deve receber a RAW cumulativa. Dumps
trimestrais isolados só podem ser aplicados em ordem cronológica como carga
aditiva controlada; reprocessá-los fora de ordem não é o fluxo operacional
homologado.

## 6. Piloto real idempotente do T1/2023

Entrada somente leitura:

`data/exports/pncp_rj_raw_2023_t1.sqlite`

Destino descartável:

`/tmp/pncp_rj_normalized_2023_t1_phase2_20261019.db`

Na primeira reaplicação do mesmo snapshot:

| Resultado | Quantidade |
|---|---:|
| Inseridas | 0 |
| Atualizadas | 0 |
| Inalteradas | 38.699 |
| Rejeitadas | 0 |

O estado das entidades permaneceu idêntico. Uma nova execução de auditoria
foi registrada, sem duplicar entidades nem linhagens.

## 7. Piloto cronológico T1 → T2/2023

Uma cópia temporária contendo o T1 recebeu o dump do T2 em ordem cronológica:

| Resultado | Quantidade |
|---|---:|
| Inseridas | 37.320 |
| Atualizadas | 965 |
| Inalteradas | 0 |
| Rejeitadas | 0 |

As atualizações correspondem a dimensões compartilhadas e PCAs anuais
reapresentados. Depois, o mesmo T2 foi aplicado novamente:

| Resultado | Quantidade |
|---|---:|
| Inseridas | 0 |
| Atualizadas | 0 |
| Inalteradas | 38.285 |

Estado acumulado T1+T2 usado no teste:

| Tabela | Registros |
|---|---:|
| `contratacao` | 6.970 |
| `contratacao_item` | 18.546 |
| `resultado_item` | 13.881 |
| `contrato` | 6.177 |
| `pca` | 68 |
| `pca_item` | 26.952 |

Resultado final: `integrity_check = ok` e zero violações de chave
estrangeira.

## 8. Piloto integral da RAW cumulativa

O banco RAW encerrado na Fase 0 foi aberto em modo somente leitura e
normalizado para:

`/tmp/pncp_rj_normalized_full_phase2_20261019.db`

Hash SHA-256 da entrada:

`0aa81991aaeb19787d0bffe354636a7b34ff4997d903d1de8201e68245d87460`

Resultados:

| Tabela | Registros |
|---|---:|
| `orgao` | 617 |
| `unidade` | 2.116 |
| `fornecedor` | 42.766 |
| `contratacao` | 180.151 |
| `contratacao_item` | 557.211 |
| `resultado_item` | 417.675 |
| `contrato` | 151.898 |
| `empenho` | 1.900 |
| `ata_registro_preco` | 80.761 |
| `pca` | 567 |
| `pca_item` | 228.469 |
| **Entidades** | **1.664.131** |

Auditoria:

- 1.391.174 registros RAW examinados;
- 1.011 PCAs externos ao inventário filtrados com evidência;
- 72 resultados preservados com `SUSPECT_DATE`;
- zero rejeições;
- 2.852.299 relações de linhagem;
- zero entidades com mais ou menos de uma linhagem corrente;
- 148.226 contratos com compra-pai resolvida;
- 3.672 contratos com compra fora do recorte;
- 151.894 contratos com fornecedor resolvido;
- quatro contratos `PE` sem NI, marcados `MISSING_IDENTIFIER`;
- todas as 80.761 atas com compra-pai resolvida;
- 1.900 empenhos com cobertura `PARTIAL` e quatro lacunas HTTP 500
  registradas;
- `integrity_check = ok`;
- zero violações de chave estrangeira.

Reconciliação financeira exata:

| Recurso | RAW e normalizada |
|---|---:|
| Resultados homologados | R$ 533.771.492.800,03 |
| Valor global dos contratos | R$ 787.533.670.110,70 |
| Empenhos observados | R$ 131.953.649,08 |
| PCA carregado no escopo RJ | R$ 191.389.328.133,31 |

A execução levou 10min30s, usou no máximo aproximadamente 358 MiB de
memória e produziu um SQLite de 2.184.953.856 bytes. O arquivo permanece fora
do repositório.

## 9. Critérios de aceite

- [x] identidade canônica independente do coletor;
- [x] seleção temporal resistente a chegada fora de ordem;
- [x] compatibilidade com snapshots RAW legados;
- [x] staging temporário antes da escrita;
- [x] merge atômico no banco normalizado;
- [x] contagens por tabela e execução;
- [x] preservação da linhagem histórica;
- [x] apenas uma linhagem corrente por entidade;
- [x] reexecução idempotente;
- [x] versão antiga fora de ordem não substitui a atual;
- [x] testes e pilotos apenas em bancos temporários.
- [x] build integral dos sete recursos da RAW cumulativa;
- [x] reconciliação financeira integral em centavos.

## 10. Pendências antes da base normalizada oficial

1. [x] criar backup do banco RAW encerrado e validar novamente seu hash;
2. [x] definir o caminho e a política de backup do banco normalizado oficial;
3. [x] gerar um artefato compactado e seu SHA-256;
4. [x] revisar privacidade dos quatro contratos sem NI e fornecedores PF;
5. [ ] implementar mascaramento ou controle de acesso;
6. [ ] somente depois publicar uma release sanitizada da normalizada.

A promoção local e suas evidências estão documentadas em
`FASE_3_BASE_NORMALIZADA_OFICIAL.md`. A revisão de privacidade identificou
4.877 fornecedores pessoa física; por isso o artefato integral não está
autorizado para release pública.

Nenhum desses passos autoriza aplicar migrações `001` a `005` na RAW oficial.
