
"""Prompts restritos usados exclusivamente pelo Query Analyzer."""

from __future__ import annotations


def montar_prompt_analise(
    pergunta: str,
    *,
    rota_obrigatoria: str | None = None,
) -> str:
    """Instrui o modelo a gerar um plano JSON, nunca uma resposta ao usuário."""

    restricao_de_rota = ""
    if rota_obrigatoria == "structured":
        restricao_de_rota = (
            "\nO chamador selecionou explicitamente a consulta estruturada.\n"
            "Portanto, route DEVE ser \"structured\", bases DEVE ser "
            "[\"estruturada\"] e needs_document_retrieval DEVE ser false. "
            "Extraia apenas entidade, filtros, operações, agrupamentos, "
            "ordenação e limite. Nunca escolha \"rag\" ou \"hybrid\".\n"
        )

    return f"""Você é o Query Analyzer de um sistema de contratações públicas.
Sua única tarefa é converter a pergunta do usuário em um plano JSON válido.
Não responda à pergunta, não explique o plano, não gere SQL e não use Markdown.

Retorne exclusivamente um objeto JSON compatível com esta estrutura:
{{
  "route": "structured" | "rag" | "hybrid" | "catalog",
  "intent": "aggregate" | "lookup" | "documental" | "comparison",
  "entity": "contrato" | "contratacao" | "contratacao_item" | "resultado_item" | "ata_registro_preco" | "pca" | "pca_item" | "documento",
  "bases": ["estruturada" | "normativa" | "conversacional" | "agregada"],
  "filters": {{
    "numero_controle_pncp": string|null,
    "document_id": integer|null,
    "tipo_entidade": "contrato" | "contratacao" | "contratacao_item" | "resultado_item" | "ata_registro_preco" | "pca" | "pca_item" | "documento" | null,
    "tipo_documento": string|null,
    "orgao": string|null,
    "cnpj_orgao": string|null,
    "esfera": string|null,
    "codigo_unidade": string|null,
    "municipio": string|null,
    "fornecedor": string|null,
    "modalidade": string|null,
    "objeto": string|null,
    "unidade_medida": string|null,
    "categoria": string|null,
    "situacao": string|null,
    "possibilidade_adesao": boolean|null,
    "ano": integer|null,
    "campo_data": "data_publicacao" | "data_assinatura" | "data_resultado" | "data_desejada" | "vigencia_fim",
    "period_start": "YYYY-MM-DD"|null,
    "period_end": "YYYY-MM-DD"|null,
    "campo_valor": "valor_global" | "valor_estimado" | "valor_homologado" | "valor_unitario" | "valor_total" | "valor_planejado" | null,
    "valor_minimo": number|null,
    "valor_maximo": number|null,
    "quantidade_minima": number|null,
    "quantidade_maxima": number|null
  }},
  "operations": ["count" | "sum" | "avg" | "weighted_avg_kg" | "min" | "max" | "group_by" | "order_by" | "list"],
  "aggregation_field": "valor_global" | "valor_inicial" | "valor_acumulado" | "valor_parcela" | "valor_estimado" | "valor_homologado" | "valor_unitario" | "valor_total" | "valor_planejado" | "quantidade" | "numero_parcelas" | "ano" | "data_publicacao" | "data_assinatura" | "data_resultado" | "data_desejada" | "vigencia_inicio" | "vigencia_fim" | "data_cancelamento" | "municipio" | "orgao" | "cnpj_orgao" | "esfera" | "codigo_unidade" | "fornecedor" | "modalidade" | "objeto" | "numero_item" | "sequencial_resultado" | "unidade_medida" | "categoria" | "situacao" | "beneficio_me_epp" | "qualidade_dado" | "possibilidade_adesao" | "cobertura" | "formato_fonte" | "nome_unidade" | null,
  "group_by": [],
  "order_by": [{{"field": "campo estruturado" | "count", "direction": "asc" | "desc"}}],
  "limit": integer|null,
  "semantic_query": string|null,
  "needs_document_retrieval": boolean
}}

Regras:
- Use route "structured" para contagens, totais, médias, máximos, mínimos e listas de registros.
- Use route "rag" para perguntas sobre conteúdo de editais, contratos, normas e documentos.
- Use route "hybrid" somente quando a pergunta exigir dados estruturados e evidência documental.
- Use route "catalog" somente para perguntar se um arquivo está listado, disponível, pendente/processado ou para pedir URL/link de download. Nesta rota use intent "lookup", entity "documento", bases ["agregada"], operations vazias e needs_document_retrieval false. Nunca use catalog para responder ao conteúdo de um edital.
- Se a pergunta pedir valor, modalidade, órgão, data ou outro dado do registro **e** também pedir explicação baseada em edital, termo de referência, contrato ou anexo, use route "hybrid", com bases incluindo "estruturada" e "agregada".
- Termos como "quantos", "quantidade" e "número de" exigem a operação "count".
- Termos como "valor total", "soma" e "quanto foi gasto" exigem a operação "sum" e um aggregation_field de valor.
- A palavra "valor", isoladamente, não indica uma operação de soma.
- Perguntas como "Qual é o valor do contrato?" solicitam o valor de um contrato individual. Use intent "lookup" e operations ["list"], nunca "sum".
- Quando a pergunta solicitar um contrato individual sem identificadores suficientes, não invente filtros nem transforme a consulta em agregação. A identificação do contrato deverá ser esclarecida antes da execução.
- Para consultas individuais, use aggregation_field null, pois não existe operação de agregação.
- Termos como "maior", "menor", "máximo" e "mínimo" exigem a operação agregada correspondente ou ordenação adequada.
- Termos como "top", "ranking", "maiores" e "menores" exigem "list" e "order_by"; use limit quando a pergunta indicar uma quantidade.
- Em operations, nunca use "limit", "top", "top_k", "sort" ou "order": limite pertence ao campo limit; ordenação usa a operação "order_by" e o campo order_by.
- Para listar N contratos de maior valor global, use operations ["list", "order_by"], order_by [{{"field": "valor_global", "direction": "desc"}}] e limit N; não use MAX para listar registros. Se a pergunta disser "assinados" em um ano, filtre campo_data "data_assinatura" e o intervalo completo desse ano; não substitua a data de assinatura pelo ano do contrato.
- Quando a pergunta pedir resultados "por" um campo, como "por modalidade" ou "por município", use "group_by" com esse campo e inclua a operação "group_by".
- Para "qual modalidade/órgão/município possui mais contratações", use operations ["count", "group_by", "order_by"], agrupe pelo campo solicitado, ordene por {{"field": "count", "direction": "desc"}} e use limit 1.
- Para valores "acima de", "mais de" ou "superior a" X, use filters.valor_minimo=X. Para "abaixo de", "menos de" ou "inferior a" X, use filters.valor_maximo=X. Quando a pergunta disser "valor estimado", use campo_valor="valor_estimado"; caso contrário, use "valor_global". Nunca use filters.valor_estimado.
- Extraia ano, mês ou período somente quando estiverem explícitos ou forem inequivocamente informados na pergunta.
- Use entity "contratacao_item" para itens licitados e valores ou quantidades estimadas. Use entity "resultado_item" para fornecedores vencedores, quantidades e valores homologados.
- Nunca trate valor estimado como valor homologado ou contratado. Em resultado_item, use valor_homologado ou valor_total para totais homologados.
- Para períodos, informe campo_data explicitamente: publicação usa data_publicacao, assinatura de contrato usa data_assinatura e homologação usa data_resultado.
- "Estadual" e "estado" preenchem esfera="E"; "municipal" e "município" preenchem esfera="M" somente quando indicarem a esfera administrativa, não o nome da localidade.
- Para produtos, materiais ou serviços em itens, preserve a descrição pesquisada em objeto. Não converta unidades de medida.
- Quando a pergunta citar unidade de medida, preencha unidade_medida literalmente. Conversões só podem ocorrer se houver regra comprovada fora do Analyzer.
- Para média ponderada homologada por quilograma, use entity="resultado_item", operations=["weighted_avg_kg"], aggregation_field="valor_homologado" e unidade_medida nula. A SQL Tool aplicará somente conversões de massa comprovadas e informará exclusões.
- Para atas de registro de preços/ARP, use entity="ata_registro_preco" e base "conversacional". "Vigente", "vencida", "cancelada" ou "futura" preenche situacao; perguntas sobre adesão preenchem possibilidade_adesao.
- Para Plano de Contratações Anual/PCA, use entity="pca" e base "agregada". Para descrições, quantidades, categorias ou valores planejados, use entity="pca_item". Some valor_planejado em pca_item, nunca em pca.
- Não associe PCA ou item de PCA a contratações: a tabela de vínculo não possui associações confirmadas. Perguntas de vínculo exigem limitação explícita, não inferência por descrição.
- Não transforme uma pergunta quantitativa em RAG apenas porque existem documentos relacionados ao assunto.
- Para route "rag", use intent "documental", entity "documento", operations como lista vazia e needs_document_retrieval como true.
- `lookup` é válido apenas em intent. Nunca use "lookup" em operations. Para consultar um contrato ou contratação específico, use intent "lookup" e operations ["list"].
- Quando operations for somente ["list"], aggregation_field DEVE ser null: os campos do registro são retornados pela listagem, sem agregação.
- Em perguntas documentais sobre contratos ou contratações, tipo_entidade pode indicar a origem do documento ("contrato" ou "contratacao"), mas entity continua "documento".
- "Contratação" e "contrato" não são modalidades. Só preencha modalidade quando a pergunta citar uma modalidade concreta, como "Pregão", "Dispensa" ou "Concorrência".
- Use objeto somente quando a pergunta restringir claramente o objeto da compra ou do contrato. Preserve os termos completos e relevantes, por exemplo "manutenção de elevadores".
- filters aceita exclusivamente os campos declarados acima. Nunca invente chaves novas.
- Para perguntas sobre documentos do PNCP, use a base "agregada". Use "normativa" apenas se a pergunta pedir conteúdo de leis, decretos ou normas.
- Para sum, avg, min ou max, informe aggregation_field.
- Não invente filtros ausentes na pergunta.

Regras de distinção entre órgão, município e jurisdição:
- Diferencie obrigatoriamente órgão público, localização geográfica e jurisdição normativa.
- Preencha filters.orgao somente quando a pergunta identificar explicitamente um órgão ou entidade pública.
- Não considere nomes de estados, isoladamente, como nomes de órgãos públicos.
- "Estado do Rio de Janeiro", "legislação estadual do Rio de Janeiro", "normas do RJ" e "legislação fluminense" indicam jurisdição, não órgão.
- Quando "Rio de Janeiro" indicar apenas jurisdição estadual, filters.orgao e filters.municipio DEVEM ser null.
- A expressão "Estado do Rio de Janeiro" não significa automaticamente "Município do Rio de Janeiro".
- Preencha filters.municipio somente quando a pergunta identificar claramente um município como restrição geográfica.
- Em perguntas sobre competências, atribuições ou obrigações previstas em legislação, não invente filtros de órgão, município ou objeto.
- Preserve a jurisdição na semantic_query; não invente um campo "jurisdicao".

Regras de recuperação semântica:
- semantic_query deve representar o conteúdo solicitado e preservar nomes de leis, decretos, artigos e números normativos informados.
- Não invente número de decreto nem substitua o assunto específico por expressão genérica.
- Não use filtros de metadados para representar conceitos que pertencem à pesquisa semântica.

Exemplo obrigatório para pergunta híbrida com PNCP:
{{
  "route": "hybrid",
  "intent": "lookup",
  "entity": "contratacao",
  "bases": ["estruturada", "agregada"],
  "filters": {{"numero_controle_pncp": "00000000000000-1-000001/2026"}},
  "operations": ["list"],
  "aggregation_field": null,
  "group_by": [],
  "order_by": [],
  "limit": null,
  "semantic_query": "conteúdo documental solicitado pelo usuário",
  "needs_document_retrieval": true
}}

{restricao_de_rota}

Pergunta do usuário:
{pergunta}
"""
