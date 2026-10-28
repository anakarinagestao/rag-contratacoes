# PNCP/RJ — Normalização Fase 5: publicação da release

Data: 2026-10-20

Branch: `feature/ingestao-incremental-pncp`

## 1. Resultado executivo

A primeira base normalizada sanitizada do PNCP/RJ foi publicada no
repositório privado do projeto:

```text
tag: base-normalizada-pncp-rj-20261020-v1
commit: ac77e6b9d1ffd0ce316f6493ac83a9aaaa18438b
publicada em: 2026-10-20T18:44:55Z
```

URL:

<https://github.com/grande-desafio-ia-rag-03/rag-grande-desafio-grupo03-contratacoes-publicas/releases/tag/base-normalizada-pncp-rj-20261020-v1>

A release não foi marcada como `latest`, pois o repositório também distribui
outros produtos e recortes de dados.

## 2. Anexos publicados

| Arquivo | Tamanho | SHA-256 remoto |
|---|---:|---|
| `pncp_rj_normalized_public_20261020.sqlite.gz` | 504.234.659 bytes | `fa28e5c8e2a15f947e47fc1c66326a842ad095cc39c706dbaab60e9f41f585b7` |
| `release_manifest.json` | 2.497 bytes | `c7b98c33770279a4f6880471a8dc6f17c67818640eee9a7860ffa4be9a3b91e9` |
| `SHA256SUMS.txt` | 199 bytes | `8e579027b17f42b384472806167d02bfe3f75daa6eddbe02e1bdfa8674874b1a` |

Os três anexos foram confirmados pela API do GitHub com estado `uploaded`.
Os digests informados pelo GitHub coincidem com os arquivos locais.

## 3. Proteção da chave

A chave HMAC não foi enviada ao GitHub nem incluída no Git. Uma cópia
secundária foi guardada fora do repositório em diretório local com permissão
`700`; o arquivo possui permissão `600`. A cópia foi comparada byte a byte
com a chave operacional.

O fingerprint registrado permanece:

```text
df90599b94f34a0bfc99a460d0ed7c0e05c983b34452a08fac80b105b7a8185e
```

Essa cópia protege contra remoção acidental no repositório, mas não substitui
um backup externo ao computador. Recomenda-se armazenar posteriormente uma
cópia cifrada em meio controlado pelo grupo.

## 4. Procedimento para consumidores

Com o GitHub CLI autenticado:

```bash
mkdir -p pncp-normalizada-20261020
cd pncp-normalizada-20261020

gh release download base-normalizada-pncp-rj-20261020-v1 \
  --repo grande-desafio-ia-rag-03/rag-grande-desafio-grupo03-contratacoes-publicas

sha256sum -c SHA256SUMS.txt
gzip -dk pncp_rj_normalized_public_20261020.sqlite.gz
sqlite3 pncp_rj_normalized_public_20261020.sqlite "PRAGMA integrity_check;"
sqlite3 pncp_rj_normalized_public_20261020.sqlite "PRAGMA foreign_key_check;"
```

O primeiro `PRAGMA` deve retornar `ok`. O segundo não deve retornar linhas.

## 5. Rastreabilidade

O manifesto da release registra:

- recorte RJ e cobertura temporal da RAW;
- hashes do banco oficial, do banco sanitizado e do arquivo compactado;
- versões da transformação e da política de sanitização;
- contagens por tabela;
- cobertura `COMPLETE` ou `PARTIAL` por recurso;
- fingerprint, sem incluir a chave;
- limitações conhecidas da base.

## 6. Decisão

**RELEASE NORMALIZADA SANITIZADA PUBLICADA E VALIDADA.**

A camada RAW oficial e a normalizada privada continuam fora do Git. Futuras
releases devem reconstruir uma nova saída a partir da normalizada oficial,
reutilizando a chave protegida quando for necessário manter pseudônimos
estáveis entre snapshots.
