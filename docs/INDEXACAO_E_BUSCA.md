# Indexação e busca híbrida

Este guia descreve como o projeto transforma documentos já processados em
evidências recuperáveis pelo RAG. Ele vale para a base estruturada do PNCP e
para documentos adicionados pelas demais frentes da equipe.

## Arquitetura

```text
Contratos e contratações JSON
  -> raw_asset (SQLite)
  -> consultas factuais por SQL

Documento original -> Docling -> Markdown -> chunks JSONL
  -> Document + DocumentChunk (SQLite, fonte de verdade)
  -> FTS5 (busca lexical persistente)
  -> FAISS (vetor + chunk_id)
  -> RRF
  -> texto e metadados recuperados de DocumentChunk
```

`raw_asset` não é copiado integralmente para o FAISS. Ele permanece como fonte
para filtros, contagens, valores, datas e modalidades. O FAISS contém apenas
chunks documentais que precisam de busca semântica.

## Artefatos e responsabilidades

| Artefato | Conteúdo | Fonte de verdade? |
|---|---|---|
| JSONL de chunks | texto dividido e metadados de um lote | artefato canônico portátil |
| `Document` / `DocumentChunk` | texto, metadados e vínculos relacionais | sim |
| FTS5 | índice lexical do SQLite | não; é derivado de `DocumentChunk` |
| FAISS | embeddings e lista ordenada de `chunk_id` | não; é derivado de `DocumentChunk` |

O `chunk_id` liga os resultados do FAISS ao chunk persistido. O FTS5 usa o
`DocumentChunk.id` como `rowid` interno. Portanto os textos não precisam ser
duplicados nos índices.

## Fluxo para a base PNCP por lote

1. Baixar e processar o lote no Docling.
2. Gerar o JSONL de chunks do lote:

   ```powershell
   python -m src.base_estruturada.pipelines.pipeline_chunks `
     --periodo 2026/08 --lote lote_001 --versao v1
   ```

3. Persistir o JSONL no SQLite. Esta etapa é idempotente: chunks com o mesmo
   `chunk_id` são ignorados.

   ```powershell
   python -m src.base_estruturada.pipelines.pipeline_persistencia_documentos `
     --periodo 2026/08 --lote lote_001 --versao v1 `
     --corpus-version-id 1
   ```

4. Auditar o índice atual, preparar uma cópia de staging e indexar nessa cópia.
   A CLI usa o SQLite em modo somente leitura e nunca inicializa/migra o banco:

   ```powershell
   python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
     --auditar `
     --indice-dir data/index/faiss/v1

   python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
     --preparar-staging `
     --base-indice-dir data/index/faiss/v1 `
     --indice-dir data/index/faiss/staging/v2
   ```

   A preparação copia os artefatos legados; não modifica a origem. O destino
   deve ser um diretório novo/vazio. Revise a saída JSON e confirme o modelo
   antes de indexar.

5. Indexar pendências em staging. Sem `--arquivo-chunks`, a seleção consulta
   todos os chunks persistidos; o limite é aplicado após excluir os IDs já
   presentes. Os filtros funcionam também em conjunto com JSONL. Por segurança,
   uma execução sem limite processa um checkpoint (500 chunks, por padrão);
   `--ate-concluir` percorre toda a fila pendente e `--limite-chunks-novos` pode
   ser combinado para impor um máximo total à execução.

   ```powershell
   python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
     --base-id pncp_rj_estruturada `
     --indice-dir data/index/faiss/staging/v2 `
     --ate-concluir `
     --tamanho-lote 50 `
     --tamanho-checkpoint 500 `
     --origem reparo-pncp
   ```

   Para limitar uma execução, acrescente `--limite-chunks-novos 500`. Para a
   base normativa use `--base-id editais_normativo`; para filtrar tipo/versão,
   passe também `--tipo norma` e/ou `--corpus-version-id N`. A opção `--tipo`
   aceita o valor exato persistido em `Document.doc_type`.

6. Auditar a cópia depois da indexação e comparar as contagens:

   ```powershell
   python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
     --auditar `
     --indice-dir data/index/faiss/staging/v2
   ```

## Checkpoints e retomada

Os diretórios de staging usam arquivos imutáveis de base e shards de checkpoint.
`estado_indexacao.json` é o ponteiro publicado atomicamente para o conjunto de
checkpoints; cada shard contém checksums dos vetores, IDs e hashes de conteúdo.
Um lock exclusivo do sistema operacional impede escritores simultâneos.
`--tamanho-lote` limita o lote enviado ao gerador e
`--tamanho-checkpoint` controla quantos vetores são confirmados em cada shard.
O padrão de 500 vetores reduz a quantidade de arquivos novos sem exigir lotes
de embeddings igualmente grandes. Checkpoints pequenos já existentes são
preservados; compactação é uma operação separada e explícita, não uma etapa
automática da indexação. Quando desejado, consolide os checkpoints publicados
no staging:

```powershell
python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
  --compactar-checkpoints `
  --indice-dir data/index/faiss/staging/v2
```

A compactação valida o índice e usa o mesmo lock exclusivo da escrita. Ela
publica um único shard novo e troca o estado atomicamente; a base e os shards
anteriores permanecem no diretório e o estado anterior é guardado em
`archive/states/` para rollback. O comando não executa embeddings nem altera o
SQLite. Faça auditoria antes e depois, e nunca execute compactação no índice de
produção sem revisão e backup operacional.

Para desfazer a compactação mais recente, valide e restaure o estado anterior:

```powershell
python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
  --restaurar-estado-anterior `
  --indice-dir data/index/faiss/staging/v2
```

O rollback valida os shards referenciados pelo snapshot antes de publicar o
estado, preserva também o estado substituído e não remove arquivos.

Ctrl+C encerra entre checkpoints publicados; o checkpoint em geração não é
publicado parcialmente. Se a interrupção ocorrer depois de o shard ser
finalizado e antes da publicação do estado, a retomada valida seus checksums,
dimensão, IDs e hashes antes de adotá-lo. Um shard órfão inválido ou duplicado
bloqueia a retomada em vez de ser silenciosamente ignorado. A auditoria pode
ser repetida a qualquer momento; ela não gera embeddings nem escreve no banco.

O limite antigo `--limite-chunks` é alias para o limite de chunks novos e também
é aplicado após remover IDs já indexados. Para consumo de memória, a pipeline
consulta primeiro IDs e metadados e só carrega o texto dos pendentes em grupos
de até 300. `--tamanho-lote` controla quantos textos são enviados por lote de
embedding, não o total do comando.

## Bases e promoção

O ID físico PNCP é `pncp_rj_estruturada`; o normativo é
`editais_normativo`. A pipeline não aplica uma lista fixa de tipos documentais.
Com JSONL, os filtros informados continuam aplicados ao SQLite, em vez de serem
ignorados.

Após revisar a auditoria do staging, mantenha o índice anterior como rollback e
aponte a aplicação para o diretório versionado do staging (por exemplo,
`data/index/faiss/v2`). **Não copie/substitua os arquivos de `v1` em produção
durante a indexação.** A promoção requer validação operacional e alteração
controlada do `cache_dir` da aplicação; nenhum comando de promoção automática é
executado pela pipeline.

Exemplo para indexar os chunks PNCP e normativos pendentes, em execuções
separadas:

```powershell
python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
  --base-id pncp_rj_estruturada `
  --indice-dir data/index/faiss/staging/v2 `
  --limite-chunks-novos 500 `
  --tamanho-lote 10

python -m src.base_estruturada.pipelines.pipeline_indexacao_faiss `
  --base-id editais_normativo `
  --indice-dir data/index/faiss/staging/v2 `
  --limite-chunks-novos 500 `
  --tamanho-lote 10
```

## Operação e limites

- O diretório `data/index/` é local e derivado, por isso não vai para Git.
- O modelo e a dimensão são fixados no estado. Alterar modelo exige um staging
  separado e uma migração explícita; a pipeline não migra automaticamente.
- Hashes de conteúdo são persistidos para shards novos. Índices legados não
  contêm hashes históricos e, por isso, a mudança de conteúdo de chunks da base
  legado não pode ser verificada retroativamente.
- O modo de auditoria não gera embeddings e abre SQLite com URI `mode=ro` e
  `PRAGMA query_only=ON`; não chama `init_db`, migrações nem inicialização FTS5.
- Perguntas factuais devem ser respondidas por SQL sobre `raw_asset`; FTS5 e
  FAISS servem para recuperação textual e semântica de documentos.
