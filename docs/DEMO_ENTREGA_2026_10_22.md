# Demonstração do Adaptive RAG PNCP/RJ — 22/10/2026

Este roteiro usa somente consultas de leitura na base normalizada. Os números
esperados abaixo pertencem ao snapshot local auditado em 22/10/2026 e podem
mudar depois de uma atualização incremental.

## 1. Iniciar a aplicação principal

Em terminais separados:

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --reload
```

```bash
source .venv/bin/activate
python -m streamlit run app/streamlit_console.py
```

Use `http://127.0.0.1:8000/docs` para criar ou autenticar um usuário. Copie
somente o valor de `access_token` retornado por `/auth/login` para a barra
lateral do Streamlit ou use o formulário **Entrar com usuário e senha**. O
token expira conforme `expires_in` e permanece somente na sessão.

## 2. Chat conversacional

O Chat é a experiência principal. Ele possui entrada inferior, mensagens do
usuário e do assistente, fontes expansíveis, tabelas estruturadas, alertas,
nova conversa e alternância entre conversas durante a sessão.

Roteiro contextual:

1. Pergunte `Quantos contratos foram assinados em 2025?`.
2. Em seguida, na mesma conversa, pergunte `E somente os estaduais?`.
3. Confirme nos detalhes técnicos que o ano 2025 foi preservado e que o filtro
   `esfera=E` foi acrescentado.
4. Clique em **Nova conversa** e confirme que o contexto anterior não é
   reutilizado.

O contexto enviado ao backend contém apenas a última análise validada. O texto
completo do histórico não é reenviado ao modelo. Acompanhamentos ambíguos pedem
esclarecimento em vez de inventar filtros.

## 3. Perguntas estruturadas e agregadas

Perguntas sugeridas:

1. `Quantas contratações existem por modalidade em 2024?`
2. `Qual é o contrato de maior valor global?`
3. `Qual é a média ponderada do valor homologado por kg de arroz, por município e modalidade, em 2025?`
4. `Quais contratos de licenciamento de software foram assinados em 2025 por órgãos estaduais do RJ e possuem valor global acima de R$ 500 mil?`
5. `Quantas atas de registro de preços foram publicadas em 2024?`
6. `Quantas atas vigentes permitem adesão?`
7. `Quantos PCAs existem em 2025?`
8. `Qual o valor total planejado nos itens dos PCAs de 2025?`

Evidências do snapshot para conferência:

- 22.685 atas com `ano_ata=2024`;
- 5.606 atas classificadas como vigentes e com adesão permitida em
  23/10/2026;
- 185 PCAs de 2025;
- 74.605 itens de PCA de 2025;
- total planejado de R$ 46.359.966.536,52 nesses itens;
- 19 contratos no recorte de licenciamento de software da pergunta 4;
- o maior contrato retornado possui valor global de
  R$ 457.322.461.390,80 e deve vir acompanhado de alertas de magnitude e
  divergência entre campos financeiros.

Na pergunta de arroz, a resposta deve informar quantidade normalizada em kg,
registros incluídos e excluídos. Unidades como `PACOTE`, `FARDO` ou `UNIDADE`
sem massa explícita não são convertidas por suposição.

## 4. Demonstração normativa isolada

O `app.db` oficial não foi alterado. A base abaixo é um artefato local de
demonstração, ignorado pelo Git:

```bash
DATABASE_URL=sqlite:///./data/debug/pncp_normative_demo.db \
python -m app.modules.normative.persistencia --versao v1
```

Ela contém 13 documentos, 794 chunks e 13 URLs de origem. A carga é
idempotente. Para executar a API usando essa base documental sem mudar o banco
oficial:

```bash
DATABASE_URL=sqlite:///./data/debug/pncp_normative_demo.db \
NORMALIZED_DATABASE_URL=sqlite:///./data/normalized/pncp_rj_normalized.db \
RAG_FAISS_INDEX_DIR=data/index/faiss/staging/openai-v1 \
python -m uvicorn app.main:app --port 8001
```

Crie um usuário nessa base de demonstração pela documentação em
`http://127.0.0.1:8001/docs`. Pergunta sugerida para a persona Cláudia:

`Sou MEI. Quais evidências deste corpus tratam da participação em pregão eletrônico?`

A validação recuperou edital, decreto estadual e Lei 14.133, todos com URL de
origem. Os vetores dos 794 chunks normativos ainda não foram gerados; por isso,
o recorte normativo desta demonstração depende da recuperação lexical FTS5.

## 5. Limitações que devem ser ditas na apresentação

- A cobertura das atas é parcial; `coverage_status` acompanha cada registro.
- A situação da ata é derivada de cancelamento e vigência no snapshot atual.
  Não equivale a garantir que uma oportunidade ainda aceita propostas.
- O corpus normativo possui data de publicação, mas não uma data de fim de
  vigência confiável para todos os documentos.
- `pca_contratacao_link` possui zero registros confirmados. O sistema não
  inventa associação entre planejamento e execução.
- Valores suspeitos são preservados e sinalizados; não são corrigidos nem
  removidos automaticamente.
- Uma ausência no top-k significa apenas ausência nas evidências recuperadas,
  não prova de inexistência no corpus completo.
- O histórico de conversas do MVP existe somente durante a sessão do
  Streamlit; ainda não há persistência entre reinicializações.
- A continuidade contextual aceita apenas alterações explícitas e seguras de
  esfera ou ano. Outros acompanhamentos devem repetir a pergunta completa.

## 6. Validação técnica

```bash
python -m pytest -q
git diff --check
git status --short
```

Nenhum banco, índice, log ou credencial deve aparecer como arquivo preparado
para commit.
