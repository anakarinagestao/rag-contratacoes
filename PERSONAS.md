# PERSONAS

## Contexto

Este documento define três personas adicionais para o sistema Adaptive RAG de
Contratações Públicas do Estado do Rio de Janeiro.

As personas foram escolhidas para representar situações de uso diferentes,
com diferentes níveis de urgência e necessidades de consulta às quatro bases
do sistema:

- **Normativa:** leis, decretos, resoluções e editais;
- **Estruturada:** contratos, contratações e registros do PNCP;
- **Conversacional:** atas, e-mails e registros textuais;
- **Agregada:** indicadores, valores homologados e séries estatísticas.

O objetivo das perguntas de referência não é apenas testar se o sistema
encontra uma resposta, mas verificar se o Adaptive RAG escolhe corretamente
a(s) base(s) necessária(s).

---

# 1. Personas

## Persona 1 — Felipe Almeida

**Profissão:** Auditor de controle interno de um órgão estadual  
**Localização:** Rio de Janeiro/RJ  
**Situação:** Está realizando uma verificação mensal de contratos e precisa
confirmar informações antes de elaborar um relatório de controle.

**Urgência:** Alta. As respostas são necessárias no mesmo dia para concluir
a análise de contratos.

**Necessidades principais:**

- localizar contratos por órgão, fornecedor e valor;
- comparar informações entre contratos e documentos da contratação;
- verificar datas e vigência;
- identificar possíveis divergências documentais sem tirar conclusões
  jurídicas automaticamente.

**Perfil de consulta:** perguntas objetivas, normalmente envolvendo filtros,
valores, datas e cruzamento de documentos.

---

## Persona 2 — Juliana Martins

**Profissão:** Servidora responsável pelo planejamento de compras de uma
secretaria municipal  
**Localização:** Região Metropolitana do Rio de Janeiro/RJ  
**Situação:** Está preparando uma nova contratação e pesquisa contratações
anteriores para obter referências de preços e modalidades utilizadas.

**Urgência:** Média. Precisa das informações durante a preparação da
contratação, mas não necessariamente imediatamente.

**Necessidades principais:**

- encontrar contratações semelhantes;
- consultar valores médios e totais;
- filtrar contratações por período, município e modalidade;
- comparar informações agregadas com registros individuais de contratos.

**Perfil de consulta:** perguntas exploratórias e comparativas, geralmente
envolvendo filtros e dados agregados.

---

## Persona 3 — Rafael Souza

**Profissão:** Representante comercial de uma empresa que fornece materiais e
serviços para órgãos públicos  
**Localização:** Niterói/RJ  
**Situação:** Está acompanhando oportunidades de contratação pública e
precisa verificar regras e informações disponíveis antes de participar de
uma disputa.

**Urgência:** Alta. Algumas consultas são feitas próximas ao prazo de
participação em uma contratação.

**Necessidades principais:**

- entender regras previstas em editais e normas;
- localizar contratações abertas ou recentes;
- verificar objeto, órgão e modalidade;
- identificar informações públicas sobre fornecedores e contratos.

**Perfil de consulta:** combina dúvidas normativas com buscas estruturadas
sobre contratações.

---

# 2. Perguntas de referência

As perguntas abaixo representam casos de teste para o roteamento do
Adaptive RAG.

A coluna **Rota esperada** indica qual base ou combinação de bases deve ser
consultada para produzir uma resposta adequada.

---

## Persona 1 — Felipe Almeida

### P1.1 — Factual

> "Qual foi o valor do contrato de licenciamento de software firmado pela
> Secretaria de Estado de Educação do RJ em 2025?"

**Rota esperada:** Estruturada

**Motivo:** A informação está associada a um registro de contratação,
envolvendo órgão, objeto, ano e valor.

---

### P1.2 — Filtro

> "Quais contratos de 2025 do Estado do RJ possuem valor superior a
> R$ 1 milhão e têm como objeto serviços de tecnologia?"

**Rota esperada:** Estruturada

**Motivo:** A pergunta exige filtros por ano, UF, valor e objeto, além da
recuperação dos contratos correspondentes.

---

### P1.3 — Cross-source + Vigência

> "O edital que originou o contrato de licenciamento de software da empresa
> vencedora ainda estava vigente na data de assinatura do contrato?"

**Rota esperada:** Normativa + Estruturada

**Motivo:** É necessário localizar informações do edital na base normativa
e relacioná-las ao contrato e às suas datas na base estruturada.

---

# Persona 2 — Juliana Martins

### P2.1 — Agregada

> "Qual foi o valor médio das contratações de material de escritório feitas
> por municípios do RJ em 2025?"

**Rota esperada:** Agregada

**Motivo:** A pergunta solicita uma estatística sobre um conjunto de
contratações, e não apenas um contrato individual.

---

### P2.2 — Filtro

> "Quais municípios do RJ contrataram serviços de limpeza em 2025 por
> pregão eletrônico?"

**Rota esperada:** Estruturada

**Motivo:** A consulta depende de filtros por UF, ano, objeto e modalidade,
retornando registros individuais de contratação.

---

### P2.3 — Cross-source

> "Encontrei uma contratação de computadores por R$ 800 mil. Qual edital
> corresponde a essa contratação e qual era o valor estimado originalmente?"

**Rota esperada:** Estruturada + Normativa

**Motivo:** A contratação deve ser localizada na base estruturada e depois
relacionada ao edital correspondente, onde está a informação sobre o valor
estimado.

---

# Persona 3 — Rafael Souza

### P3.1 — Normativa

> "Quais são as regras previstas na legislação para participação de
> microempresas e empresas de pequeno porte em licitações públicas?"

**Rota esperada:** Normativa

**Motivo:** A resposta depende de legislação e normas aplicáveis, e não de
um contrato específico.

---

### P3.2 — Cross-source

> "Quais empresas venceram contratações de materiais de informática em 2025
> e quais eram os valores homologados dos respectivos itens?"

**Rota esperada:** Estruturada + Agregada

**Motivo:** É necessário localizar as contratações e fornecedores e
relacioná-los aos valores homologados dos itens.

---

### P3.3 — Fora de escopo

> "Qual empresa de material de escritório você recomenda que eu contrate
> para minha empresa?"

**Rota esperada:** Fora de escopo

**Motivo:** A pergunta solicita uma recomendação comercial. O sistema foi
projetado para consultar informações públicas sobre contratações e normas,
não para recomendar fornecedores ou tomar decisões comerciais pelo usuário.

---

# 3. Resumo das perguntas

| ID   | Persona | Tipo                    | Rota esperada           |
| ---- | ------- | ----------------------- | ----------------------- |
| P1.1 | Felipe  | Factual                 | Estruturada             |
| P1.2 | Felipe  | Filtro                  | Estruturada             |
| P1.3 | Felipe  | Cross-source + Vigência | Normativa + Estruturada |
| P2.1 | Juliana | Factual / Agregada      | Agregada                |
| P2.2 | Juliana | Filtro                  | Estruturada             |
| P2.3 | Juliana | Cross-source            | Estruturada + Normativa |
| P3.1 | Rafael  | Factual                 | Normativa               |
| P3.2 | Rafael  | Cross-source            | Estruturada + Agregada  |
| P3.3 | Rafael  | Fora de escopo          | Nenhuma                 |

---

# 4. Pergunta com maior risco de erro

> "Encontrei uma contratação de computadores por R$ 800 mil. Qual edital
> corresponde a essa contratação e qual era o valor estimado originalmente?"

### Hipótese do erro

Esta pergunta pode apresentar dificuldade porque exige **cruzamento entre
diferentes tipos de informação**.

O sistema precisa:

1. localizar a contratação pelo valor e pelo objeto;
2. identificar corretamente o edital relacionado;
3. recuperar o documento normativo correspondente;
4. encontrar o valor estimado no edital;
5. relacionar as duas evidências sem confundir o valor estimado com o valor
   homologado ou contratado.

A hipótese principal é que o Adaptive RAG possa encaminhar a pergunta somente
para a base estruturada por reconhecer "contratação" e "R$ 800 mil", deixando
de consultar a base normativa onde está o edital.

Outra possibilidade é o problema ocorrer no **retrieval**, caso a contratação
e o edital utilizem descrições diferentes para o mesmo objeto.

### O que deverá ser medido posteriormente

Nas próximas Sprints, essa pergunta deverá ser reavaliada para verificar:

- se o roteador selecionou as duas bases esperadas;
- se a recuperação encontrou a contratação correta;
- se o edital correspondente foi recuperado;
- se o valor estimado foi localizado;
- se o reranker colocou as evidências corretas no topo;
- se a resposta final diferenciou valor estimado, homologado e contratado.

A pergunta será mantida como um caso de diagnóstico, mesmo que o sistema
passe a respondê-la corretamente, permitindo comparar o comportamento entre
as versões do sistema.
