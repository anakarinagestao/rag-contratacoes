# PNCP/RJ — Normalização Fase 4: exportação sanitizada

Data: 2026-10-20

Branch: `feature/ingestao-incremental-pncp`

## 1. Resultado executivo

Foi implementada uma exportação separada da base normalizada para reduzir a
exposição de identificadores diretos de pessoas físicas e estrangeiras. A
origem oficial é aberta em modo somente leitura e nunca é alterada.

Artefato local sanitizado:

```text
data/normalized/releases/pncp_rj_normalized_public_20261020.db
tamanho: 2.115.391.488 bytes
SHA-256:
dda99f3218477342e3c034ffa93f78b5c845d03f7ed4e84367cc8bf49279dcfb
```

Arquivo compactado preparado para distribuição:

```text
data/normalized/releases/pncp_rj_normalized_public_20261020.sqlite.gz
tamanho: 504.234.659 bytes
SHA-256 compactado:
fa28e5c8e2a15f947e47fc1c66326a842ad095cc39c706dbaab60e9f41f585b7
SHA-256 depois de descompactado:
dda99f3218477342e3c034ffa93f78b5c845d03f7ed4e84367cc8bf49279dcfb
```

Os arquivos de banco, o pacote e a chave privada permanecem fora do Git por
meio da regra `data/normalized/` do `.gitignore`.

## 2. Política aplicada

Versão: `pncp-public-v1`

- tipos protegidos: `PF` e `PE`;
- identificadores são substituídos por HMAC-SHA-256 determinístico, com
  prefixo `anon-v1-`;
- nomes associados são substituídos por `PESSOA PROTEGIDA`;
- a mesma pessoa conserva o mesmo pseudônimo entre as tabelas;
- pessoas jurídicas (`PJ`) permanecem inalteradas;
- a linhagem do fornecedor recebe a mesma chave pseudonimizada;
- eventuais problemas de normalização que contenham campos pessoais têm
  valor e detalhe removidos;
- problemas de qualidade não pessoais continuam disponíveis para auditoria.

A pseudonimização não equivale a anonimização absoluta. O artefato reduz a
exposição direta e deve continuar sujeito a controle de acesso e uso
responsável.

## 3. Contagens transformadas

| Alvo | Registros protegidos |
|---|---:|
| Fornecedores | 6.446 |
| Resultados homologados | 13.071 |
| Contratos | 6.075 |
| Empenhos | 57 |
| Chaves de linhagem de fornecedor | 6.446 |
| Problemas com campo pessoal redigido | 0 |

O valor zero na última linha é esperado para este snapshot: nenhum dos
1.083 problemas atuais usa um campo pessoal. Os 72 registros `SUSPECT_DATE`
foram preservados integralmente.

## 4. Chave HMAC

A chave foi criada apenas na área privada local:

```text
data/normalized/private/sanitization_hmac_v1.key
permissão: 600
fingerprint:
df90599b94f34a0bfc99a460d0ed7c0e05c983b34452a08fac80b105b7a8185e
```

A chave nunca é persistida no banco; somente o fingerprint é registrado em
`sanitization_metadata`. Ela **não deve ser adicionada ao Git, a uma release,
a mensagens ou a serviços de compartilhamento**. Antes da publicação, é
obrigatório manter uma cópia segura fora do repositório. Perder a chave impede
reproduzir os mesmos pseudônimos nas próximas releases.

## 5. Validações do artefato real

- `PRAGMA integrity_check = ok`;
- zero violações em `PRAGMA foreign_key_check`;
- nenhuma alteração nas contagens das tabelas normalizadas;
- nenhum identificador original de `PF` ou `PE` permaneceu nas relações
  protegidas;
- todos os pseudônimos e nomes protegidos obedecem à política;
- nenhum fornecedor `PJ` foi modificado;
- totais financeiros permaneceram idênticos ao banco oficial;
- os bytes da chave HMAC não aparecem no banco sanitizado;
- `gzip -t` e a conferência do hash descompactado foram aprovados.

O banco de origem utilizado possui SHA-256:

```text
2392c086ac681534e0f2d792ae51785ffb4adae189175be3a45aaac99bd4ffdd
```

## 6. Uso

Para gerar uma nova saída, sempre use um caminho ainda inexistente:

```bash
python3 -m src.sanitize_normalized \
  --source-database data/normalized/pncp_rj_normalized.db \
  --output-database data/normalized/releases/nova_saida.db \
  --key-file data/normalized/private/sanitization_hmac_v1.key
```

O comando recusa chaves com menos de 32 bytes, origem igual ao destino e
sobrescrita de uma saída existente. Se ocorrer uma falha, a saída incompleta
é removida.

## 7. Decisão e limite operacional

**ARTEFATO SANITIZADO APROVADO TECNICAMENTE PARA DISTRIBUIÇÃO CONTROLADA.**

O banco sanitizado é um produto derivado e não deve receber atualizações do
normalizador. Cada release deve ser recriada a partir da base normalizada
oficial, usando a mesma chave enquanto a continuidade dos pseudônimos for
necessária.

A chave recebeu uma cópia secundária local em diretório privado fora do
repositório, com permissões `700` no diretório e `600` no arquivo. O pacote,
o manifesto e os checksums foram publicados posteriormente. As evidências da
publicação estão em
`docs/FASE_5_PUBLICACAO_RELEASE_NORMALIZADA.md`.
