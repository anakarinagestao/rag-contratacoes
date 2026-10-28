# Análise de Requisitos

## Sistema de Adaptive RAG para Contratações Públicas do Estado do Rio de Janeiro

**Projeto:** Grande Desafio --- IA Generativa & RAG\
**Cenário:** Contratações Públicas do Estado do Rio de Janeiro\
**Versão:** 1.0\
**Data:** 16/09/2026\
**Instituições:** Residência TIC em Trilhas · PUC-Rio · Instituto ECOA

------------------------------------------------------------------------

## 1. Introdução

Este documento apresenta a análise de requisitos do sistema de
Inteligência Artificial Generativa com Retrieval-Augmented Generation
(RAG) para o cenário de **Contratações Públicas do Estado do Rio de
Janeiro**.

O sistema deverá permitir que usuários realizem consultas em linguagem
natural sobre licitações, contratos, fornecedores, modalidades, valores,
editais, atas e demais informações presentes nas bases selecionadas para
o projeto.

A característica central da solução será o **Adaptive RAG**. Antes de
executar a recuperação, o sistema deverá determinar **qual tipo de
evidência é necessário para responder à pergunta**, podendo consultar
uma ou várias bases.

------------------------------------------------------------------------

## 2. Contexto e Problema

As informações de contratações públicas encontram-se distribuídas entre
diferentes documentos, registros e fontes. No cenário definido pelo
desafio, o **Portal Nacional de Contratações Públicas (PNCP)** é uma das
fontes centrais.

O problema não consiste apenas em localizar documentos. Muitas perguntas
exigem combinar informações de naturezas diferentes.

Exemplo:

> "O Estado do RJ está pagando mais caro por ambulância do que os
> municípios?"

Uma pergunta desse tipo pode exigir o cruzamento de registros
estruturados de contratos, documentos de contratação, atas e valores
agregados.

Outro exemplo:

> "Quais contratos de licenciamento de software foram assinados por
> órgãos estaduais do RJ acima de R\$ 500 mil em 2025, e quem foram os
> fornecedores?"

Nesse caso, o sistema precisa combinar **busca semântica** sobre o
objeto da contratação com **filtros estruturados**, como ano e valor.

Portanto, uma busca vetorial isolada não é suficiente para atender aos
requisitos do cenário.

------------------------------------------------------------------------

## 3. Objetivo Geral

Desenvolver um sistema inteligente capaz de receber perguntas em
linguagem natural e recuperar, combinar e apresentar evidências
provenientes das diferentes bases relacionadas às contratações públicas
do Estado do Rio de Janeiro.

------------------------------------------------------------------------

## 4. Objetivos Específicos

O sistema deverá:

-   receber consultas em linguagem natural;
-   interpretar a intenção da consulta;
-   identificar o tipo de evidência necessário;
-   extrair filtros estruturados;
-   rotear consultas para uma ou mais bases;
-   executar recuperação semântica e lexical;
-   combinar resultados utilizando RRF;
-   permitir reranking dos resultados;
-   gerar respostas fundamentadas nas evidências recuperadas;
-   apresentar as fontes utilizadas;
-   aplicar regras de proteção de dados;
-   registrar histórico e feedback;
-   registrar logs estruturados;
-   contabilizar latência e consumo de tokens;
-   permitir testes e avaliação do pipeline;
-   manter o corpus reproduzível e versionado.

------------------------------------------------------------------------

# 5. Escopo

## 5.1 Escopo incluído

Fazem parte do sistema:

1.  coleta automatizada das fontes;
2.  versionamento do corpus;
3.  geração do inventário;
4.  ingestão de documentos;
5.  chunking específico por natureza de dado;
6.  extração e preservação de metadados;
7.  geração e cache de embeddings;
8.  criação de índices persistentes;
9.  análise da consulta;
10. Adaptive RAG;
11. recuperação híbrida;
12. filtros por metadados;
13. fusão RRF;
14. reranking;
15. aplicação de guardrails;
16. geração da resposta;
17. validação da resposta;
18. apresentação das fontes;
19. histórico;
20. feedback;
21. observabilidade;
22. testes;
23. avaliação do sistema.

## 5.2 Fora do escopo

Não faz parte do objetivo do sistema:

-   executar processos licitatórios;
-   enviar propostas para licitações;
-   modificar informações do PNCP;
-   substituir sistemas oficiais;
-   emitir parecer jurídico;
-   determinar automaticamente que uma contratação é ilegal;
-   acusar pessoa ou empresa de fraude;
-   divulgar informações pessoais protegidas;
-   divulgar dados bancários ou credenciais.

------------------------------------------------------------------------

# 6. Fontes e Bases de Conhecimento

O cenário deverá possuir **quatro bases de naturezas diferentes**.

  -----------------------------------------------------------------------
  Base              Natureza          Conteúdo previsto Fonte principal
  ----------------- ----------------- ----------------- -----------------
  Normativa         Normativa         Lei 14.133/2021,  Planalto e
                                      decretos e        documentos de
                                      editais           contratação

  Contratos         Estruturada       Contratos e       PNCP
                                      contratações do   
                                      RJ                

  Atas e            Conversacional    Atas de registro  PNCP + base
  comunicações                        de preços e       sintética
                                      trilha de e-mails 

  Indicadores       Agregada          PCA e valores     PNCP
                                      homologados por   
                                      item              
  -----------------------------------------------------------------------

A arquitetura deverá respeitar as diferenças entre essas bases.

------------------------------------------------------------------------

# 7. Atores

## 7.1 Usuário

Realiza consultas, visualiza respostas e fontes e poderá registrar
feedback.

## 7.2 Administrador / Equipe Técnica

Responsável por:

-   configurar o manifesto;
-   executar coleta;
-   atualizar o corpus;
-   executar ingestão;
-   construir ou carregar índices;
-   acompanhar logs;
-   executar testes;
-   executar benchmarks;
-   diagnosticar problemas.

## 7.3 Fontes externas

Serviços e documentos utilizados na composição do corpus.

------------------------------------------------------------------------

# 8. Personas de Referência

## 8.1 Marina --- Analista de Licitações

Analista de uma prefeitura do interior do Rio de Janeiro que precisa
utilizar informações de contratações anteriores como referência.

**Consulta de exemplo:**

> "Qual foi o valor médio por quilo de arroz contratado por municípios
> do RJ em 2025, e em quais modalidades?"

**Rota esperada:** agregada + estruturada.

O sistema deverá recuperar valores efetivamente praticados e não apenas
definições legais sobre modalidades.

------------------------------------------------------------------------

## 8.2 Rodrigo --- Repórter de Dados

Investiga contratações realizadas por órgãos estaduais e precisa cruzar
critérios textuais, financeiros e temporais.

**Consulta:**

> "Quais contratos de licenciamento de software foram assinados por
> órgãos estaduais do RJ acima de R\$ 500 mil em 2025, e quem foram os
> fornecedores?"

**Necessidades:**

-   busca semântica por "licenciamento de software";
-   filtro por ano;
-   filtro numérico por valor;
-   identificação do fornecedor;
-   identificação do órgão.

------------------------------------------------------------------------

## 8.3 Cláudia --- Pequena Empresária

Deseja participar de processos de contratação pública, mas não domina a
terminologia técnica.

**Consulta:**

> "Sou MEI, posso participar de pregão eletrônico? Tem algum aberto pra
> material de escritório?"

A pergunta possui duas necessidades:

1.  conteúdo normativo;
2.  consulta a registros de contratações.

O sistema deverá realizar **roteamento para múltiplas bases**.

------------------------------------------------------------------------

# 9. Requisitos Funcionais

## RF001 --- Receber consulta

O sistema deverá permitir que o usuário envie perguntas em linguagem
natural.

**Prioridade:** Alta.

------------------------------------------------------------------------

## RF002 --- Criar identificador da execução

Cada consulta deverá possuir um `run_id` que permita correlacionar todas
as etapas do pipeline.

**Prioridade:** Alta.

------------------------------------------------------------------------

## RF003 --- Analisar a consulta

O sistema deverá possuir um Query Analyzer capaz de identificar
elementos relevantes da pergunta.

Entre os elementos possíveis:

-   intenção;
-   entidade;
-   órgão;
-   município;
-   UF;
-   fornecedor;
-   modalidade;
-   objeto;
-   ano;
-   intervalo de datas;
-   valor mínimo;
-   valor máximo;
-   identificadores;
-   tipo de evidência necessário.

**Prioridade:** Alta.

------------------------------------------------------------------------

## RF004 --- Extrair filtros estruturados

O sistema deverá converter restrições presentes na pergunta em filtros
estruturados.

Exemplo:

> "contratos de software acima de R\$ 500 mil em 2025"

Resultado esperado:

``` json
{
  "ano": 2025,
  "valor_minimo": 500000,
  "objeto": "software"
}
```

**Prioridade:** Alta.

------------------------------------------------------------------------

## RF005 --- Executar Adaptive Routing

O sistema deverá determinar quais bases contêm os tipos de evidência
necessários para responder à pergunta.

Rotas:

``` text
normativa
estruturada
conversacional
agregada
```

**Prioridade:** Alta.

------------------------------------------------------------------------

## RF006 --- Permitir múltiplas rotas

Uma pergunta poderá utilizar mais de uma rota.

O sistema não deverá ser limitado a selecionar apenas uma base por
consulta.

**Prioridade:** Alta.

------------------------------------------------------------------------

## RF007 --- Consultar base normativa

O sistema deverá recuperar informações provenientes de legislação,
decretos, editais e demais documentos normativos declarados no corpus.

------------------------------------------------------------------------

## RF008 --- Consultar base estruturada

O sistema deverá recuperar registros de contratos e contratações.

A recuperação deverá permitir filtros por metadados.

------------------------------------------------------------------------

## RF009 --- Consultar base conversacional

O sistema deverá recuperar informações provenientes das atas e dos
documentos conversacionais pertencentes ao corpus.

------------------------------------------------------------------------

## RF010 --- Consultar base agregada

O sistema deverá recuperar indicadores, séries, totais e valores
agregados.

Informações numéricas deverão preservar o contexto do recorte utilizado.

------------------------------------------------------------------------

## RF011 --- Executar recuperação densa

O sistema deverá utilizar embeddings para realizar recuperação
semântica.

Essa recuperação deverá favorecer consultas conceituais, como:

> "compra de equipamento hospitalar"

mesmo quando a expressão exata não estiver presente nos documentos.

------------------------------------------------------------------------

## RF012 --- Executar recuperação lexical

O sistema deverá possuir recuperação lexical baseada em BM25 ou
mecanismo equivalente.

A recuperação lexical deverá auxiliar especialmente na busca de:

-   CNPJ;
-   número de contrato;
-   número de edital;
-   códigos;
-   identificadores;
-   expressões exatas.

------------------------------------------------------------------------

## RF013 --- Executar recuperação híbrida

O sistema deverá combinar recuperação densa e lexical.

------------------------------------------------------------------------

## RF014 --- Aplicar filtros de metadados

Os filtros identificados pelo Query Analyzer deverão ser utilizados
antes ou durante a recuperação conforme a estratégia definida pela
equipe.

------------------------------------------------------------------------

## RF015 --- Executar fusão RRF

Os rankings produzidos pelos mecanismos de recuperação deverão ser
combinados utilizando **Reciprocal Rank Fusion (RRF)**.

------------------------------------------------------------------------

## RF016 --- Executar reranking

O sistema deverá possuir uma etapa de reranking para reordenar os
candidatos recuperados.

A implementação deverá permitir a utilização do modelo de rerank
disponibilizado para o desafio.

------------------------------------------------------------------------

## RF017 --- Aplicar guardrails

Antes da geração da resposta, o sistema deverá aplicar as regras de
sensibilidade e segurança.

------------------------------------------------------------------------

## RF018 --- Gerar resposta fundamentada

A resposta deverá ser produzida utilizando as evidências recuperadas.

O sistema deverá evitar apresentar como fato uma informação que não
esteja sustentada pelas fontes utilizadas.

------------------------------------------------------------------------

## RF019 --- Apresentar fontes

A resposta deverá informar as fontes utilizadas.

Quando disponíveis, deverão ser preservados:

-   documento;
-   base;
-   URL ou origem;
-   identificador;
-   chunk;
-   data;
-   vigência.

------------------------------------------------------------------------

## RF020 --- Informar evidência insuficiente

Quando o contexto recuperado não for suficiente, o sistema deverá
informar que não possui evidência suficiente para responder
adequadamente.

------------------------------------------------------------------------

## RF021 --- Validar resposta

A saída do pipeline deverá ser validada por schema estruturado,
preferencialmente utilizando modelos Pydantic.

------------------------------------------------------------------------

## RF022 --- Registrar histórico

O sistema deverá persistir o histórico das consultas e respostas.

------------------------------------------------------------------------

## RF023 --- Registrar feedback

O usuário deverá poder avaliar uma resposta.

O feedback poderá registrar:

-   avaliação positiva;
-   avaliação negativa;
-   comentário;
-   identificação da consulta;
-   data.

------------------------------------------------------------------------

# 10. Requisitos de Segurança e Sensibilidade

## RF024 --- Classificar dados na ingestão

A sensibilidade deverá ser definida durante a ingestão e registrada nos
metadados.

Categorias previstas:

``` text
publico
interno
restrito
```

------------------------------------------------------------------------

## RF025 --- Permitir informações públicas

O sistema poderá apresentar:

-   valores de contratos;
-   nomes de empresas fornecedoras;
-   CNPJ de pessoa jurídica;
-   objetos contratados;
-   demais dados classificados como públicos.

------------------------------------------------------------------------

## RF026 --- Mascarar dados pessoais

O sistema deverá mascarar, quando presentes:

-   CPF de pessoa física;
-   telefone pessoal;
-   e-mail pessoal.

------------------------------------------------------------------------

## RF027 --- Bloquear dados restritos

O sistema não deverá fornecer:

-   dados bancários;
-   credenciais;
-   informações classificadas como restritas.

------------------------------------------------------------------------

## RF028 --- Não emitir acusação

Perguntas como:

> "Esse contrato foi fraude?"

não deverão resultar em uma acusação produzida pelo modelo.

O sistema poderá apresentar as informações e evidências disponíveis, mas
não deverá substituir investigação ou decisão jurídica.

------------------------------------------------------------------------

# 11. Requisitos do Corpus

## RF029 --- Possuir manifesto declarativo

O corpus deverá ser definido em:

``` text
corpus_manifest.yaml
```

O manifesto deverá representar a fonte de verdade sobre os dados que
compõem o sistema.

------------------------------------------------------------------------

## RF030 --- Declarar metadados da fonte

Cada base deverá declarar, quando aplicável:

-   `base_id`;
-   natureza;
-   fonte;
-   tipo de acesso;
-   endpoint ou URLs;
-   parâmetros;
-   paginação;
-   rate limit;
-   seleção determinística;
-   volume esperado;
-   formato;
-   licença;
-   sensibilidade;
-   campo de vigência;
-   estratégia de chunking;
-   metadados extras.

------------------------------------------------------------------------

## RF031 --- Garantir seleção determinística

Sempre que possível, duas execuções utilizando o mesmo manifesto deverão
selecionar o mesmo conjunto de dados.

Períodos, IDs ou URLs deverão ser fixados para evitar seleção dependente
do momento da execução.

------------------------------------------------------------------------

## RF032 --- Coletar dados automaticamente

Os dados deverão entrar no corpus por meio do módulo:

``` text
src/fetch_corpus.py
```

------------------------------------------------------------------------

## RF033 --- Suportar operação fetch

Deverá existir operação equivalente a:

``` bash
python -m src.fetch_corpus fetch --manifest corpus_manifest.yaml
```

Ela será responsável pela coleta inicial.

------------------------------------------------------------------------

## RF034 --- Suportar operação update

Deverá existir operação equivalente a:

``` bash
python -m src.fetch_corpus update --manifest corpus_manifest.yaml
```

Ela deverá criar uma nova versão sem sobrescrever a anterior.

------------------------------------------------------------------------

## RF035 --- Suportar operação inventory

Deverá existir operação equivalente a:

``` bash
python -m src.fetch_corpus inventory --version v1
```

------------------------------------------------------------------------

## RF036 --- Garantir idempotência da coleta

Executar `fetch` novamente com o mesmo manifesto não deverá duplicar
arquivos já coletados.

------------------------------------------------------------------------

## RF037 --- Permitir retomada da coleta

Uma falha de rede não deverá exigir reiniciar toda a coleta.

------------------------------------------------------------------------

## RF038 --- Respeitar rate limit

A coleta deverá respeitar o `rate_limit_seg` definido para cada fonte.

------------------------------------------------------------------------

## RF039 --- Registrar falhas sem interromper toda a coleta

Uma fonte individual indisponível deverá ser registrada como falha no
inventário.

------------------------------------------------------------------------

## RF040 --- Versionar dados brutos

As versões deverão ser imutáveis.

Exemplo:

``` text
data/raw/
├── v1/
├── v2/
└── v3/
```

------------------------------------------------------------------------

## RF041 --- Gerar diff de versões

Atualizações deverão gerar relatório indicando:

-   arquivos adicionados;
-   arquivos removidos;
-   arquivos modificados;
-   arquivos inalterados.

Exemplo:

``` text
DIFF_v1_v2.md
```

------------------------------------------------------------------------

## RF042 --- Gerar inventário

O sistema deverá gerar:

``` text
CORPUS_INVENTORY.md
```

O inventário deverá registrar informações como:

-   quantidade de arquivos;
-   tamanho;
-   formatos;
-   quantidade por base;
-   divergência em relação ao volume esperado;
-   falhas;
-   data da coleta.

------------------------------------------------------------------------

# 12. Requisitos de Ingestão e Indexação

## RF043 --- Executar chunking específico por base

A estratégia de chunking deverá considerar a natureza da informação.

### Normativa

Preferencialmente divisão por cláusula, seção ou unidade hierárquica,
preservando contexto.

### Estruturada

Um contrato ou registro poderá constituir um chunk semântico completo.

### Conversacional

O chunk deverá preservar contexto suficiente da interação.

### Agregada

Os dados deverão preservar o recorte necessário para interpretação
correta dos números.

------------------------------------------------------------------------

## RF044 --- Possuir metadados obrigatórios

Todo chunk deverá conter:

``` text
source_file
doc_type
chunk_id
sensitivity
base_id
```

------------------------------------------------------------------------

## RF045 --- Permitir metadados adicionais

Poderão ser utilizados:

``` text
orgao
modalidade
valor
municipio
ano
fornecedor
data_publicacao
vigencia
objeto
```

------------------------------------------------------------------------

## RF046 --- Cachear embeddings

Embeddings deverão ser armazenados em disco.

O corpus não deverá ser reprocessado integralmente sem necessidade.

------------------------------------------------------------------------

## RF047 --- Persistir índices

Os índices deverão ser salvos e carregados posteriormente.

------------------------------------------------------------------------

# 13. Requisitos de Observabilidade

## RF048 --- Registrar logs estruturados

O pipeline deverá gerar logs estruturados em JSONL.

Formato previsto:

``` text
logs/pipeline-AAAA-MM-DD.jsonl
```

------------------------------------------------------------------------

## RF049 --- Registrar estágio

Os logs deverão identificar a etapa executada.

Estágios previstos:

``` text
query_analysis
roteamento
retrieval_denso
retrieval_bm25
fusao_rrf
rerank
geracao
validacao
feedback
```

------------------------------------------------------------------------

## RF050 --- Registrar latência

Cada etapa deverá registrar:

``` text
latencia_ms
```

------------------------------------------------------------------------

## RF051 --- Registrar tokens

Etapas que utilizam LLM deverão registrar:

``` json
{
  "prompt": 0,
  "completion": 0,
  "total": 0
}
```

------------------------------------------------------------------------

## RF052 --- Registrar modelo utilizado

Toda etapa que utilizar um modelo deverá registrar sua identificação.

------------------------------------------------------------------------

## RF053 --- Correlacionar logs

Todos os eventos de uma consulta deverão utilizar o mesmo `run_id`.

------------------------------------------------------------------------

# 14. Requisitos Não Funcionais

## RNF001 --- Reprodutibilidade

O corpus deverá poder ser reconstruído utilizando o manifesto e o módulo
de coleta.

------------------------------------------------------------------------

## RNF002 --- Desempenho

O sistema deverá evitar reprocessamento desnecessário por meio de cache
e índices persistentes.

------------------------------------------------------------------------

## RNF003 --- Escalabilidade

A arquitetura deverá permitir aumento do volume de documentos sem exigir
alteração completa do pipeline.

------------------------------------------------------------------------

## RNF004 --- Manutenibilidade

Os módulos deverão possuir responsabilidades separadas.

Estrutura de referência:

``` text
src/
├── fetch_corpus.py
├── ingest.py
├── index.py
├── router.py
├── retrieve.py
├── generate.py
├── schema.py
└── obs.py
```

------------------------------------------------------------------------

## RNF005 --- Testabilidade

Cada módulo deverá possuir testes de contrato compatíveis com sua
responsabilidade.

------------------------------------------------------------------------

## RNF006 --- Testes offline

Testes unitários e de contrato deverão executar sem dependência
obrigatória de rede ou LLM.

------------------------------------------------------------------------

## RNF007 --- Segurança

Segredos e credenciais não deverão ser armazenados no repositório.

------------------------------------------------------------------------

## RNF008 --- Privacidade

Dados pessoais deverão ser tratados de acordo com a política de
sensibilidade definida pelo projeto.

------------------------------------------------------------------------

## RNF009 --- Rastreabilidade

Deverá ser possível identificar quais evidências foram utilizadas para
produzir uma resposta.

------------------------------------------------------------------------

## RNF010 --- Observabilidade

Uma falha de resposta deverá poder ser investigada por meio dos
registros das etapas do pipeline.

------------------------------------------------------------------------

## RNF011 --- Persistência

Índices, histórico e demais dados definidos como persistentes pela
arquitetura deverão sobreviver ao reinício da aplicação.

------------------------------------------------------------------------

# 15. Regras de Negócio

## RN001

O tipo de evidência deverá determinar o roteamento da pergunta.

## RN002

Palavras-chave isoladas não deverão determinar obrigatoriamente a rota.

## RN003

Uma consulta poderá utilizar múltiplas bases.

## RN004

Filtros numéricos deverão ser tratados como filtros estruturados sempre
que a base permitir.

## RN005

CNPJ de pessoa jurídica poderá ser apresentado quando estiver
classificado como informação pública.

## RN006

CPF de pessoa física deverá ser mascarado conforme a política definida.

## RN007

Dados bancários e credenciais não deverão ser apresentados.

## RN008

O sistema não deverá acusar automaticamente uma contratação de fraude.

## RN009

Números agregados deverão ser acompanhados do contexto do recorte
utilizado.

## RN010

Cada chunk deverá possuir metadados obrigatórios.

## RN011

Versões antigas do corpus não deverão ser sobrescritas.

## RN012

`data/raw/` não deverá ser versionado no Git.

## RN013

O sistema deverá indicar ausência de evidência quando não houver
contexto suficiente.

------------------------------------------------------------------------

# 16. Casos de Uso

## UC001 --- Realizar consulta

**Ator:** Usuário

### Fluxo principal

1.  O usuário envia uma pergunta.
2.  O sistema cria um `run_id`.
3.  O Query Analyzer interpreta a consulta.
4.  O sistema extrai filtros.
5.  O roteador determina as bases.
6.  A recuperação é executada.
7.  Os rankings são combinados.
8.  O reranking é aplicado.
9.  Guardrails são executados.
10. A resposta é gerada.
11. A resposta é validada.
12. As fontes são associadas.
13. A resposta é apresentada.
14. O histórico é registrado.
15. Os logs são persistidos.

### Fluxo alternativo --- ausência de evidência

1.  A recuperação não encontra contexto suficiente.
2.  O sistema não inventa uma resposta.
3.  O usuário é informado da limitação.

### Fluxo alternativo --- conteúdo sensível

1.  O sistema recupera um trecho contendo dado sensível.
2.  O guardrail identifica a classificação.
3.  O dado é mascarado ou bloqueado.
4.  Somente o conteúdo permitido segue para geração.

------------------------------------------------------------------------

## UC002 --- Consulta com filtro

**Exemplo:**

> "Contratos de software acima de R\$ 500 mil em 2025."

### Fluxo

1.  Query Analyzer identifica o objeto.
2.  Identifica `ano = 2025`.
3.  Identifica `valor_minimo = 500000`.
4.  Roteador seleciona a base estruturada.
5.  Recuperação aplica filtros e busca semântica.
6.  Sistema apresenta os contratos encontrados e as fontes.

------------------------------------------------------------------------

## UC003 --- Consulta multi-base

**Exemplo:**

> "Sou MEI, posso participar de pregão eletrônico? Tem algum aberto pra
> material de escritório?"

### Fluxo

1.  Sistema identifica duas intenções.
2.  Primeira intenção é encaminhada à base normativa.
3.  Segunda intenção é encaminhada à base estruturada.
4.  Resultados são recuperados separadamente.
5.  Sistema sintetiza a resposta.
6.  Fontes de ambas as bases são apresentadas.

------------------------------------------------------------------------

## UC004 --- Registrar feedback

1.  Usuário recebe a resposta.
2.  Usuário informa sua avaliação.
3.  Feedback é associado à consulta.
4.  Informação é persistida para análise futura.

------------------------------------------------------------------------

## UC005 --- Atualizar corpus

**Ator:** Administrador

1.  Operador executa `update`.
2.  Sistema lê o manifesto.
3.  Dados são coletados.
4.  Conteúdo é comparado com a versão anterior.
5.  Nova versão é criada.
6.  Hashes são calculados.
7.  Inventário é atualizado.
8.  Diff é gerado.
9.  A nova versão poderá ser ingerida e indexada.

------------------------------------------------------------------------

# 17. Fluxo do Pipeline RAG

``` text
                         CONSULTA
                            |
                            v
                 +---------------------+
                 |   Query Analyzer    |
                 +---------------------+
                            |
                            v
                 +---------------------+
                 | Extração de filtros |
                 +---------------------+
                            |
                            v
                 +---------------------+
                 |   Adaptive Router   |
                 +---------------------+
                            |
          +-----------------+------------------+
          |                 |                  |
          v                 v                  v
      Normativa        Estruturada       Conversacional
          |                 |                  |
          +-----------------+------------------+
                            |
                       Agregada
                            |
                            v
                  Recuperação por base
                            |
                +-----------+-----------+
                |                       |
                v                       v
             Dense                    BM25
                |                       |
                +-----------+-----------+
                            |
                            v
                           RRF
                            |
                            v
                         Rerank
                            |
                            v
                       Guardrails
                            |
                            v
                         Geração
                            |
                            v
                        Validação
                            |
                            v
                Resposta + Citações
                            |
                 +----------+----------+
                 |                     |
                 v                     v
              Histórico             Feedback
```

------------------------------------------------------------------------

# 18. Modelo Conceitual de Resposta

A saída poderá seguir estrutura equivalente a:

``` json
{
  "query_id": "q_0042",
  "run_id": "f3a91c2e",
  "answer": "Resposta fundamentada nas evidências recuperadas.",
  "routes": [
    "estruturada",
    "agregada"
  ],
  "sources_used": [
    {
      "base_id": "contratos_estruturado",
      "source": "PNCP",
      "chunk_id": "ctr_00123"
    }
  ],
  "is_refusal": false
}
```

A estrutura definitiva deverá ser implementada e validada pelos schemas
do projeto.

------------------------------------------------------------------------

# 19. Estratégia de Testes

O projeto deverá possuir três camadas principais.

## 19.1 Testes Unitários

Testarão componentes isolados.

Exemplos:

-   normalização de filtros;
-   serialização;
-   chunking;
-   cálculo do RRF;
-   mascaramento;
-   validação de metadados.

------------------------------------------------------------------------

## 19.2 Testes de Contrato

Verificarão os contratos internos.

Exemplos:

-   todo chunk possui `base_id`;
-   todo chunk possui `sensitivity`;
-   todo chunk possui `chunk_id`;
-   a resposta respeita o schema;
-   recusas respeitam as regras de segurança.

Exemplo:

``` python
def test_todo_chunk_tem_metadado_obrigatorio(chunks_fixture):
    obrigatorios = {
        "source_file",
        "doc_type",
        "chunk_id",
        "sensitivity",
        "base_id",
    }

    for chunk in chunks_fixture:
        faltando = obrigatorios - set(chunk.metadata)
        assert not faltando
```

------------------------------------------------------------------------

## 19.3 Testes de Integração

Deverão executar o pipeline ponta a ponta com perguntas conhecidas.

Testes dependentes do modelo poderão utilizar:

``` python
@pytest.mark.llm
```

para que não sejam executados por padrão junto aos testes offline.

------------------------------------------------------------------------

# 20. Critérios de Aceite

## CA001 --- Consulta factual

Dado que existe evidência suficiente no corpus, quando o usuário fizer
uma pergunta factual, o sistema deverá responder utilizando evidências
recuperadas e apresentar as fontes.

------------------------------------------------------------------------

## CA002 --- Filtro numérico

Para:

> "Quais contratos de software acima de R\$ 500 mil foram assinados em
> 2025?"

o Query Analyzer deverá identificar pelo menos:

``` text
valor_minimo = 500000
ano = 2025
```

------------------------------------------------------------------------

## CA003 --- Busca lexical

Quando o usuário informar um identificador exato, como CNPJ ou número de
contrato, o mecanismo lexical deverá participar da recuperação.

------------------------------------------------------------------------

## CA004 --- Busca semântica

Consultas conceituais deverão ser recuperáveis mesmo quando a formulação
do usuário não corresponder exatamente ao texto do documento.

------------------------------------------------------------------------

## CA005 --- Multi-rota

Uma pergunta contendo necessidade normativa e necessidade estruturada
deverá poder utilizar ambas as bases.

------------------------------------------------------------------------

## CA006 --- Segurança

CPF, telefone pessoal e e-mail pessoal classificados para mascaramento
não deverão aparecer sem tratamento na resposta.

------------------------------------------------------------------------

## CA007 --- Ausência de evidência

O sistema não deverá fabricar uma resposta quando não houver evidência
suficiente.

------------------------------------------------------------------------

## CA008 --- Observabilidade

Todas as etapas de uma consulta deverão poder ser correlacionadas pelo
`run_id`.

------------------------------------------------------------------------

## CA009 --- Corpus reproduzível

O corpus deverá poder ser reconstruído utilizando o manifesto e o script
de coleta, respeitando a disponibilidade das fontes.

------------------------------------------------------------------------

## CA010 --- Persistência dos índices

Após a construção dos índices, uma nova execução deverá poder
carregá-los sem reprocessar todo o corpus.

------------------------------------------------------------------------

## CA011 --- Versionamento

Uma atualização do corpus deverá gerar uma nova versão sem modificar a
anterior.

------------------------------------------------------------------------

## CA012 --- Testabilidade

Os testes unitários e de contrato deverão executar sem depender do
endpoint de geração.

------------------------------------------------------------------------

# 21. Matriz de Rastreabilidade

  Necessidade                     Requisitos
  ------------------------------- ----------------
  Consulta em linguagem natural   RF001--RF004
  Adaptive RAG                    RF005--RF006
  Quatro bases                    RF007--RF010
  Busca híbrida                   RF011--RF016
  Resposta fundamentada           RF017--RF021
  Histórico e feedback            RF022--RF023
  Segurança                       RF024--RF028
  Corpus reproduzível             RF029--RF042
  Ingestão e índices              RF043--RF047
  Observabilidade                 RF048--RF053
  Qualidade e manutenção          RNF001--RNF011

------------------------------------------------------------------------

# 22. Restrições Técnicas

1.  A infraestrutura de modelos é compartilhada entre as equipes.
2.  Embeddings deverão ser cacheados em disco.
3.  Índices deverão ser persistidos.
4.  O processamento deverá evitar requisições desnecessárias.
5.  O corpus deverá ser controlado pelo `corpus_manifest.yaml`.
6.  Versões do corpus deverão ser imutáveis.
7.  `data/raw/` não deverá ser enviado para o Git.
8.  `.env` não deverá ser versionado.
9.  Logs não deverão ser versionados quando configurados como artefatos
    locais.
10. Testes dependentes de rede não deverão executar por padrão.
11. A política de sensibilidade deverá ser aplicada antes da geração.
12. A implementação deverá respeitar as limitações das fontes externas.

------------------------------------------------------------------------

# 23. Riscos

  -----------------------------------------------------------------------
  Risco                   Impacto                 Mitigação
  ----------------------- ----------------------- -----------------------
  Indisponibilidade do    Alto                    Coleta retomável e
  PNCP ou outra fonte                             corpus versionado

  Mudança na estrutura de Alto                    Cliente desacoplado e
  API                                             validação

  Corpus incompleto       Alto                    Inventário e comparação
                                                  com volume esperado

  Roteamento incorreto    Alto                    Perguntas de referência
                                                  e testes

  Filtros incorretos      Alto                    Testes do Query
                                                  Analyzer

  Alucinação do LLM       Alto                    Grounding, validação e
                                                  citações

  Exposição de dados      Alto                    Sensibilidade +
  pessoais                                        guardrails

  Busca semântica         Médio/Alto              Recuperação híbrida
  insuficiente                                    

  Busca lexical           Médio                   Dense + BM25
  insuficiente                                    

  Ranking inadequado      Médio                   RRF + rerank

  Consumo excessivo de    Médio                   Logging, cache e
  tokens                                          controle de contexto

  Reindexação excessiva   Médio                   Cache e índices
                                                  persistentes

  Atualização degradar    Alto                    Versionamento +
  recuperação                                     benchmark de regressão
  -----------------------------------------------------------------------

------------------------------------------------------------------------

# 24. Premissas

Considera-se que:

-   o cenário utilizado será Contratações Públicas do Estado do Rio de
    Janeiro;
-   serão mantidas quatro bases de naturezas diferentes;
-   as fontes utilizadas serão declaradas no manifesto;
-   o sistema utilizará predominantemente dados públicos;
-   o endpoint de geração estará disponível conforme as condições do
    desafio;
-   haverá mecanismo de embeddings;
-   o reranker será integrado quando disponibilizado;
-   o projeto possuirá perguntas de referência para avaliação;
-   fixtures permitirão testes offline.

------------------------------------------------------------------------

# 25. Estrutura Esperada do Repositório

``` text
├── acompanhamento/
│   └── ACOMPANHAMENTO_ALUNO_NOME.md
├── README.md
├── ARQUITETURA.md
├── PERSONAS.md
├── corpus_manifest.yaml
├── CORPUS_INVENTORY.md
├── .env.example
├── .gitignore
├── requirements.txt
├── config.py
├── src/
│   ├── fetch_corpus.py
│   ├── ingest.py
│   ├── index.py
│   ├── router.py
│   ├── retrieve.py
│   ├── generate.py
│   ├── schema.py
│   └── obs.py
├── tests/
│   ├── fixtures/
│   └── test_*.py
├── eval/
├── app/
├── data/
└── logs/
```

------------------------------------------------------------------------

# 26. Arquitetura Funcional Resumida

``` text
Fontes externas
      |
      v
corpus_manifest.yaml
      |
      v
fetch_corpus.py
      |
      v
Corpus versionado
      |
      v
Ingestão
      |
      v
Chunking + Metadados + Sensibilidade
      |
      +-----------------------+
      |                       |
      v                       v
Embeddings / Dense        BM25 / Lexical
      |                       |
      +-----------+-----------+
                  |
                  v
             Query Analyzer
                  |
                  v
            Adaptive Router
                  |
                  v
          Recuperação por base
                  |
                  v
              Dense + BM25
                  |
                  v
                  RRF
                  |
                  v
               Rerank
                  |
                  v
             Guardrails
                  |
                  v
               Geração
                  |
                  v
              Validação
                  |
                  v
       Resposta fundamentada
                  |
        +---------+---------+
        |                   |
        v                   v
     Histórico           Feedback

Em paralelo:
Logs + run_id + latência + tokens
```

------------------------------------------------------------------------

# 27. Conclusão

O sistema proposto não deverá funcionar apenas como um chatbot conectado
a uma base vetorial. O principal requisito arquitetural do cenário é a
capacidade de determinar **qual tipo de evidência responde à pergunta**
antes de executar a recuperação.

Uma mesma palavra ou entidade pode exigir rotas diferentes dependendo da
intenção do usuário. Uma consulta sobre o significado de uma modalidade
pode exigir uma fonte normativa, enquanto uma consulta sobre valores
praticados nessa modalidade exige registros estruturados ou dados
agregados.

O **Adaptive RAG** deverá, portanto, coordenar Query Analysis, extração
de filtros, roteamento, recuperação densa, BM25, RRF, reranking,
guardrails, geração e validação.

Além da qualidade da resposta, a solução deverá ser **reproduzível,
observável, testável e rastreável**, mantendo corpus versionado, índices
persistentes, logs estruturados, controle de tokens, política de
sensibilidade e evidências associadas às respostas.

Este documento deverá evoluir juntamente com as decisões arquiteturais e
com os resultados obtidos durante as sprints do Grande Desafio.
