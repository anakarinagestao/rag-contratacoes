# Governança de dados operacionais no Git

## Objetivo

Bancos, credenciais, logs, índices e embeddings gerados não fazem parte do
código-fonte. Esses artefatos podem conter informações pessoais ou estado
operacional e devem permanecer fora do Git.

O banco local padrão continua sendo configurado por `DATABASE_URL`. Um ambiente
novo não depende de um `app.db` versionado: as tabelas podem ser criadas com:

```bash
cp .env.example .env
python3 -m app.init_db
```

O comando cria somente o esquema. Usuários reais, bancos RAW, bases
normalizadas e releases não devem ser usados como fixtures.

## Proteção antes do commit

O `.gitignore` cobre bancos SQLite e seus arquivos auxiliares, credenciais,
logs, caches, índices e embeddings gerados. Antes de um commit, valide os
arquivos preparados:

```bash
python3 scripts/check_repository_governance.py --staged
```

O workflow `repository-governance.yml` executa a mesma política para arquivos
novos ou modificados em pull requests. Arquivos acima de 50 MiB também são
bloqueados. Migrations, manifestos e fixtures sintéticas permanecem permitidos.

## Bancos e artefatos compartilháveis

Snapshots autorizados devem ser sanitizados, acompanhados de manifesto e
SHA-256 e publicados como artefatos de release ou em armazenamento externo.
Eles não devem ser adicionados à árvore Git.

## Limitação histórica

Retirar um arquivo do índice impede seu rastreamento futuro, mas não o remove
de commits anteriores. A avaliação de exposição, eventual rotação de segredos
e possível reescrita de histórico devem ocorrer em uma tarefa separada e com
coordenação de toda a equipe.

## Recuperação local

Se o banco local for perdido, restaure uma cópia protegida e verificada ou crie
um banco vazio com `python3 -m app.init_db`. Nunca restaure dados operacionais
por meio de um commit do Git.

## Transição para quem já clonou o repositório

O commit que retira `app.db` do Git registra a exclusão do arquivo versionado.
Por isso, antes de atualizar uma cópia antiga do repositório, cada integrante
deve preservar o seu banco local fora da árvore do projeto:

```bash
cp app.db ../app.db.backup
sha256sum app.db ../app.db.backup
```

Depois do `git pull`, restaure a cópia somente se `app.db` não estiver mais no
diretório do projeto:

```bash
cp ../app.db.backup app.db
```

O arquivo restaurado permanecerá local porque `*.db` está no `.gitignore`.
Uma cópia local modificada também pode impedir a atualização automática do
Git; nesse caso, não force a operação nem descarte o arquivo sem antes validar
o backup.
