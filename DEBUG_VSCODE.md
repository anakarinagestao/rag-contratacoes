# Debug da camada RAW no VS Code

As configurações deste guia permitem acompanhar a ingestão do PNCP uma
microetapa por vez. Elas usam o arquivo local `.vscode/launch.json` e aparecem
no painel **Executar e Depurar** (`Ctrl+Shift+D`).

O banco usado nas coletas de estudo é
`data/debug/pncp_debug.db`. Ele é separado do `app.db` e está protegido pelas
regras `*.db` do `.gitignore`.

## Preparação

1. Abra no VS Code a raiz deste repositório.
2. Instale as extensões **Python** e **Python Debugger** da Microsoft.
3. Use `Python: Select Interpreter` e selecione o Python do ambiente virtual.
4. Abra **Executar e Depurar** com `Ctrl+Shift+D`.
5. Escolha uma configuração e pressione `F5`.

## Etapas disponíveis

| Configuração | O que verifica | Escreve no banco? |
| --- | --- | --- |
| `RAW 1.1: validar manifesto` | Tipos de base, coletores, paginação, chaves e fontes | Não |
| `RAW 1.2: preview PNCP sem gravar` | Conexão real e formato dos registros recebidos | Não |
| `RAW 1.3: coletar no banco de debug` | Fluxo completo de primeira carga (`fetch`) | Sim, somente no banco de debug |
| `RAW 1.4: atualizar banco de debug` | Retomada por checkpoint (`update`) | Sim, somente no banco de debug |
| `RAW 1.5: inspecionar banco de debug` | Contagens, runs e checkpoints, sem mostrar payload | Não; abre em modo somente leitura |
| `RAW 1.6: idempotência no SQLite` | Transições `NEW`, `UNCHANGED` e `UPDATED` com dados falsos | Usa apenas banco temporário |
| `RAW: testes completos` | Todos os testes automatizados do projeto | Usa apenas bancos temporários |

As etapas 1.2, 1.3 e 1.4 pedem a fonte que será executada:

- `pncp_pca_rj`: Plano de Contratações Anual;
- `pncp_contratacoes_rj_piloto`: contratações estruturadas;
- `pncp_itens_contratacao_rj_piloto`: itens das contratações estruturadas;
- `pncp_resultado_item_piloto_rj`: valores homologados por item.

## Primeira sessão recomendada

Execute as configurações na ordem numérica:

1. valide o `corpus_manifest.yaml`;
2. faça o preview de `pncp_pca_rj` para confirmar a resposta real sem gravar;
3. colete `pncp_pca_rj` no banco de debug;
4. inspecione as tabelas geradas;
5. execute novamente com `update` para observar o checkpoint;
6. rode o teste de idempotência.

Para a base estruturada e os valores homologados, respeite a dependência:

1. colete `pncp_contratacoes_rj_piloto`;
2. colete `pncp_itens_contratacao_rj_piloto`;
3. colete `pncp_resultado_item_piloto_rj`.

A terceira fonte usa exclusivamente os itens já preservados na RAW. Ela não
consulta novamente a listagem de itens e não possui alvo manual de fallback.
Itens com `temResultado=false` não geram chamada HTTP, mas são contabilizados
como alvos concluídos para o checkpoint.

O cliente repete falhas transitórias com espera progressiva. Uma resposta
HTTP 204 é tratada como resultado vazio e não interrompe a ingestão.

## Breakpoints recomendados

Coloque breakpoints nestas funções, clicando à esquerda do número da linha:

1. `src/inspection/raw_manifest.py`, em `validate_source`;
2. `src/ingestion/pncp_client.py`, em `fetch_page` e `get`;
3. `src/ingestion/pncp_collector.py`, no método `collect` do coletor escolhido;
4. `src/fetch_corpus.py`, em `resolve_source_targets` e `run`;
5. `src/ingest.py`, em `ingest_records`;
6. `src/ingest.py`, em `RawRepository.persist_record`;
7. `app/core/database/repositories/raw_repository.py`, em `insert_raw`;
8. `app/core/database/repositories/checkpoint_repository.py`, em
   `update_checkpoint`;
9. `src/inspection/raw_database.py`, em `inspect_database`.

No painel **Watch**, acompanhe aos poucos:

```text
source.id
source.resource_type
source.natureza
page
record.cursor
record.ordinal
status
counts
last_page
run_id
```

Evite adicionar `record.payload` inteiro ao Watch ao compartilhar a tela. A
fonte de resultados homologados pode conter identificadores e dados do
fornecedor. Os scripts de preview e inspeção mostram apenas chaves, hashes,
tamanhos e contagens.

## Comandos do depurador

- `F5`: iniciar ou continuar;
- `F10`: executar a linha atual sem entrar na função;
- `F11`: entrar na função chamada;
- `Shift+F11`: sair da função atual;
- `Shift+F5`: encerrar.

## Comandos equivalentes no terminal

```bash
python -m src.inspection.raw_manifest
python -m src.inspection.pncp_preview --source pncp_pca_rj --limit 2
python -m src.fetch_corpus fetch --source pncp_pca_rj \
  --database-url sqlite:///./data/debug/pncp_debug.db
python -m src.inspection.raw_database \
  --database-url sqlite:///./data/debug/pncp_debug.db
python -m unittest discover -s tests -p 'test_*.py' -v
```

## Observações de segurança

- O preview chama a API real do PNCP, mas não persiste registros.
- A coleta real escreve somente em `data/debug/pncp_debug.db`.
- O banco de debug e o `app.db` não devem ser enviados ao GitHub.
- Não compartilhe capturas do painel **Variables** contendo payloads completos
  ou variáveis de ambiente.
- O `launch.json` é local porque `.vscode/` permanece no `.gitignore`.
