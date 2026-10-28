"""Conversão validada de perguntas em planos de execução do RAG."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from pydantic import ValidationError

from app.core.logging import current_perf_run_id, novo_run_id, perf_stage, registrar
from app.core.llm_http_observability import llm_http_operation
from llm_ecoa import gerar_resposta_analyzer
from src.query.prompts import montar_prompt_analise
from src.query.schemas import (
    KnowledgeBase,
    QueryAnalysis,
    QueryEntity,
    QueryFilters,
    QueryIntent,
    QueryRoute,
)


AnalyzerLlmFn = Callable[[str], str]


_NUMERO_CONTROLE_PNCP_RE = re.compile(r"\b\d{14}-\d-\d{6}/\d{4}\b")


def _pede_media_por_quilo(pergunta: str) -> bool:
    """Reconhece somente pedido explícito de média monetária por massa."""

    texto = pergunta.casefold()
    pede_media = any(
        termo in texto for termo in ("média", "media", "médio", "medio")
    )
    por_quilo = re.search(
        r"(?:\bpor\b|/)\s*(?:kg\b|quilo(?:grama)?s?\b)",
        texto,
    )
    return pede_media and por_quilo is not None


def _observability_run_id() -> str:
    """Reutiliza a correlação HTTP quando disponível."""

    return current_perf_run_id() or novo_run_id()


def _analisar_pergunta_catalogo(pergunta: str) -> QueryAnalysis | None:
    """Reconhece consultas objetivas ao catálogo sem chamar a LLM.

    A regra exige número de controle PNCP e vocabulário de disponibilidade,
    URL ou estado de processamento. Assim, uma pergunta *sobre o conteúdo* do
    edital continua na rota RAG, mesmo que cite o mesmo processo.
    """

    texto = pergunta.casefold()
    controle = _NUMERO_CONTROLE_PNCP_RE.search(pergunta)
    termos_catalogo = (
        "catálogo", "catalogo", "listado", "listada", "disponível",
        "disponivel", "url", "link", "download", "baixar", "processado",
        "processada", "processamento", "pendente",
    )
    if controle is None or not any(termo in texto for termo in termos_catalogo):
        return None

    tipo_documento = None
    if "termo de referência" in texto or "termo de referencia" in texto:
        tipo_documento = "Termo de Referência"
    elif "edital" in texto:
        tipo_documento = "Edital"
    elif "ata de registro" in texto:
        tipo_documento = "Ata de Registro de Preço"
    elif "contrato" in texto:
        tipo_documento = "Contrato"

    return QueryAnalysis(
        route=QueryRoute.CATALOG,
        intent=QueryIntent.LOOKUP,
        entity=QueryEntity.DOCUMENTO,
        bases=[KnowledgeBase.AGREGADA],
        filters=QueryFilters(
            numero_controle_pncp=controle.group(0),
            tipo_documento=tipo_documento,
        ),
        semantic_query=None,
        needs_document_retrieval=False,
    )


class QueryAnalysisError(ValueError):
    """Indica que a resposta da LLM não pode ser usada como plano válido."""

    def __init__(
        self,
        mensagem: str,
        *,
        erros_validacao: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.erros_validacao = erros_validacao or []

    def detalhe_publico(self) -> dict[str, object]:
        """Retorna diagnóstico seguro para o cliente HTTP.

        A resposta bruta da LLM fica somente no log. Para o Swagger, expomos
        apenas os campos e mensagens do schema que falharam.
        """

        detalhe: dict[str, object] = {"mensagem": str(self)}
        if self.erros_validacao:
            detalhe["errosValidacao"] = self.erros_validacao
        return detalhe


def _resumir_erros_validacao(exc: ValidationError) -> list[dict[str, str]]:
    """Converte erros Pydantic em dados legíveis, sem incluir valores brutos."""

    return [
        {
            "campo": ".".join(str(parte) for parte in erro["loc"]),
            "mensagem": str(erro["msg"]),
            "tipo": str(erro["type"]),
        }
        for erro in exc.errors(include_url=False)
    ]


def _registrar_falha_validacao(
    pergunta: str,
    resposta_llm: str,
    erros_validacao: list[dict[str, str]],
) -> None:
    """Registra somente metadados da falha, sem pergunta ou saída da LLM."""

    try:
        registrar(
            _observability_run_id(),
            "query_analyzer",
            status="erro",
            question_length=len(pergunta),
            llm_output_length=len(resposta_llm),
            validation_error_count=len(erros_validacao),
            validation_error_fields=[
                f"{erro['campo']}:{erro['tipo']}" for erro in erros_validacao
            ],
            error_code="query_analysis_validation_failed",
        )
    except Exception:
        # Falha de observabilidade nunca deve ocultar a causa original do
        # Analyzer nem transformar uma resposta 422 em erro 500.
        pass


def _entidade_explicita_na_pergunta(pergunta: str) -> str | None:
    """Reconhece entidades estruturadas somente em texto inequívoco."""

    texto = pergunta.casefold()
    if any(termo in texto for termo in (
        "item do pca", "itens do pca", "item de pca", "itens de pca",
        "item do plano de contratações", "itens do plano de contratações",
    )):
        return "pca_item"
    if "plano de contratações anual" in texto or re.search(r"\bpcas?\b", texto):
        return "pca"
    if any(termo in texto for termo in (
        "ata de registro de preço", "ata de registro de preços",
        "atas de registro de preço", "atas de registro de preços",
    )) or re.search(r"\barp\b", texto):
        return "ata_registro_preco"
    if any(termo in texto for termo in (
        "resultado homologado", "resultados homologados",
        "resultado do item", "resultados dos itens",
    )):
        return "resultado_item"
    if any(termo in texto for termo in (
        "item da contratação", "itens da contratação",
        "item de contratação", "itens de contratação",
        "item licitado", "itens licitados",
    )):
        return "contratacao_item"
    if "contratação" in texto or "contratacao" in texto:
        return "contratacao"
    if "contrato" in texto:
        return "contrato"
    return None


def _normalizar_plano_conhecido(
    plano: dict[str, object],
    pergunta: str,
) -> tuple[dict[str, object], list[str]]:
    """Corrige deslizes recorrentes sem flexibilizar o schema.

    A LLM às vezes coloca ``lookup`` em ``operations``, embora seja uma
    intenção, ou escolhe ``rag`` ao pedir simultaneamente valor/modalidade e
    explicação documental. As correções só ocorrem sob condições explícitas e
    ficam registradas para auditoria; qualquer outro plano inválido continua
    sendo rejeitado pelo Pydantic.
    """

    normalizado = dict(plano)
    correcoes: list[str] = []
    operations = normalizado.get("operations")
    if isinstance(operations, list):
        operacoes_normalizadas: list[object] = []
        for operacao in operations:
            # Ordenação e limite já têm campos próprios. Aceitar somente
            # aliases conhecidos quando o plano traz a informação necessária;
            # nunca descartar uma operação arbitrária devolvida pelo modelo.
            if (
                operacao in ("limit", "top", "top_k")
                and normalizado.get("route") in {"structured", "hybrid"}
                and type(normalizado.get("limit")) is int
                and 1 <= normalizado["limit"] <= 100
            ):
                correcoes.append(f"operations.{operacao}_representado_por_limit")
                continue
            valor = "list" if operacao == "lookup" else operacao
            if (
                operacao in ("sort", "order")
                and normalizado.get("route") in {"structured", "hybrid"}
                and isinstance(normalizado.get("order_by"), list)
                and normalizado["order_by"]
            ):
                valor = "order_by"
                correcoes.append(f"operations.{operacao}_para_order_by")
            if valor not in operacoes_normalizadas:
                operacoes_normalizadas.append(valor)
        if operacoes_normalizadas != operations:
            normalizado["operations"] = operacoes_normalizadas
            if "lookup" in operations:
                correcoes.append("operations.lookup_para_list")

    # O modelo ocasionalmente preenche group_by/order_by, mas esquece de
    # declarar a operação correspondente. Esses campos são inequívocos e o
    # acréscimo preserva a intenção expressa no próprio plano.
    operations = normalizado.get("operations")
    if isinstance(operations, list) and all(
        isinstance(operacao, str) for operacao in operations
    ):
        operations_completas = list(operations)
        if (
            isinstance(normalizado.get("group_by"), list)
            and normalizado["group_by"]
            and "group_by" not in operations_completas
        ):
            operations_completas.append("group_by")
            correcoes.append("operations.group_by_adicionado")
        if (
            isinstance(normalizado.get("order_by"), list)
            and normalizado["order_by"]
            and "order_by" not in operations_completas
        ):
            operations_completas.append("order_by")
            correcoes.append("operations.order_by_adicionado")
        if operations_completas != operations:
            normalizado["operations"] = operations_completas

    # Um agrupamento sem agregação não produz uma resposta quantitativa útil
    # na Structured Tool. Quando o plano estruturado já escolheu group_by,
    # ``count`` é a agregação mínima e determinística para completar o plano.
    operations = normalizado.get("operations")
    aggregate_operations = {"count", "sum", "avg", "weighted_avg_kg", "min", "max"}
    if (
        normalizado.get("route") in {"structured", "hybrid"}
        and isinstance(normalizado.get("group_by"), list)
        and normalizado["group_by"]
        and isinstance(operations, list)
        and all(isinstance(operacao, str) for operacao in operations)
        and not aggregate_operations.intersection(operations)
    ):
        normalizado["operations"] = [*operations, "count"]
        correcoes.append("operations.count_adicionado_por_group_by")

    # "Média por quilo" não é AVG(valor_total): exige dividir a soma dos
    # valores homologados pela massa total comprovadamente conversível. Como
    # a intenção é inequívoca, corrigimos aqui a variação recorrente do LLM
    # e deixamos a SQL Tool aplicar as conversões auditáveis de unidade.
    if (
        _pede_media_por_quilo(pergunta)
        and normalizado.get("entity") == "resultado_item"
    ):
        operations = normalizado.get("operations")
        preserved_operations = []
        if isinstance(operations, list):
            preserved_operations = [
                operation
                for operation in operations
                if operation in {"group_by", "order_by"}
            ]
        normalizado.update(
            {
                "route": "structured",
                "intent": "aggregate",
                "entity": "resultado_item",
                "bases": ["estruturada"],
                "operations": ["weighted_avg_kg", *preserved_operations],
                "aggregation_field": "valor_homologado",
                "semantic_query": None,
                "needs_document_retrieval": False,
            }
        )
        filtros_media = normalizado.get("filters")
        if isinstance(filtros_media, dict):
            filtros_media = dict(filtros_media)
            # Não filtrar literalmente por "kg": "quilograma" e embalagens
            # com massa explícita também são elegíveis para conversão.
            filtros_media["unidade_medida"] = None
            normalizado["filters"] = filtros_media
        correcoes.append("aggregation.media_por_kg_ponderada")

    filtros = normalizado.get("filters")
    # ``valor_estimado`` é um campo de saída, não um filtro aceito. Para
    # perguntas com limiar explícito, convertemos a chave recorrente da LLM
    # para o filtro semântico correto, sem aceitar campos livres no schema.
    if isinstance(filtros, dict) and "valor_estimado" in filtros:
        valor = filtros.get("valor_estimado")
        texto = pergunta.casefold()
        filtros_normalizados = dict(filtros)
        if (
            isinstance(valor, (int, float))
            and not isinstance(valor, bool)
            and filtros_normalizados.get("valor_minimo") is None
            and any(termo in texto for termo in (
                "acima de", "acima do", "mais de", "maior que",
                "maiores que", "superior a", "superiores a", "a partir de",
            ))
        ):
            filtros_normalizados["valor_minimo"] = valor
            filtros_normalizados["campo_valor"] = "valor_estimado"
            filtros_normalizados.pop("valor_estimado", None)
            normalizado["filters"] = filtros_normalizados
            filtros = filtros_normalizados
            correcoes.append("filters.valor_estimado_para_valor_minimo")
        elif (
            isinstance(valor, (int, float))
            and not isinstance(valor, bool)
            and filtros_normalizados.get("valor_maximo") is None
            and any(termo in texto for termo in (
                "abaixo de", "abaixo do", "menos de", "menor que",
                "menores que", "inferior a", "inferiores a", "até ",
            ))
        ):
            filtros_normalizados["valor_maximo"] = valor
            filtros_normalizados["campo_valor"] = "valor_estimado"
            filtros_normalizados.pop("valor_estimado", None)
            normalizado["filters"] = filtros_normalizados
            filtros = filtros_normalizados
            correcoes.append("filters.valor_estimado_para_valor_maximo")

    tem_pncp = isinstance(filtros, dict) and bool(
        filtros.get("numero_controle_pncp")
    )
    # O número de controle PNCP é a chave precisa do registro. O modelo costuma
    # acrescentar ``objeto`` como uma paráfrase da pergunta, que pode não ser
    # uma substring literal do objeto persistido (ou nem existir no metadado
    # do chunk). Nesse cenário o filtro redundante transformava um PNCP válido
    # em uma consulta sem linhas e sem evidências documentais.
    if tem_pncp and isinstance(filtros, dict) and filtros.get("objeto"):
        filtros_normalizados = dict(filtros)
        filtros_normalizados["objeto"] = None
        normalizado["filters"] = filtros_normalizados
        filtros = filtros_normalizados
        correcoes.append("filters.objeto_removido_por_pncp")
    aggregation_field = normalizado.get("aggregation_field")
    campos_estruturados = {
        "valor_global",
        "valor_inicial",
        "valor_acumulado",
        "valor_parcela",
        "valor_estimado",
        "valor_homologado",
        "valor_unitario",
        "valor_total",
        "valor_planejado",
        "quantidade",
        "ano",
        "data_publicacao",
        "data_assinatura",
        "data_resultado",
        "data_desejada",
        "vigencia_inicio",
        "vigencia_fim",
        "data_cancelamento",
        "municipio",
        "orgao",
        "cnpj_orgao",
        "esfera",
        "codigo_unidade",
        "fornecedor",
        "modalidade",
        "objeto",
        "numero_item",
        "sequencial_resultado",
        "unidade_medida",
        "categoria",
        "situacao",
        "beneficio_me_epp",
        "qualidade_dado",
        "numero_parcelas",
        "possibilidade_adesao",
        "cobertura",
        "formato_fonte",
        "nome_unidade",
    }
    pede_dado_estruturado = (
        isinstance(aggregation_field, str)
        and aggregation_field in campos_estruturados
    )

    # Uma resposta RAG com valor/modalidade e PNCP explícito precisa de SQL e
    # de recuperação documental. Sem PNCP, não inferimos a rota para evitar
    # ampliar uma consulta ampla indevidamente.
    if (
        normalizado.get("route") == "rag"
        and tem_pncp
        and pede_dado_estruturado
    ):
        normalizado["route"] = "hybrid"
        bases = normalizado.get("bases")
        if isinstance(bases, list) and "estruturada" not in bases:
            normalizado["bases"] = ["estruturada", *bases]
        normalizado["needs_document_retrieval"] = True
        correcoes.append("route.rag_para_hybrid_por_dado_estruturado")

    # Para perguntas estritamente documentais, alguns modelos ainda colocam
    # ``list`` como se fosse uma operação da rota RAG. ``list`` não é usado
    # pelo retriever: ele recebe a pergunta semântica e os filtros. Como a
    # intenção e a entidade são inequivocamente documentais, removemos apenas
    # esses campos estruturados residuais. Isso evita rejeitar uma pergunta
    # válida por uma falha de formatação do plano da LLM.
    if (
        normalizado.get("route") == "rag"
        and normalizado.get("intent") == "documental"
        # A rota documental não executa operações SQL. Aceitamos somente uma
        # lista formada por operações conhecidas, pois o modelo pode variar
        # entre ``list``, ``count`` ou ``group_by`` mesmo após o prompt. Tipos
        # malformados e operações desconhecidas continuam chegando ao
        # Pydantic, onde serão rejeitados normalmente.
        and isinstance(normalizado.get("operations"), list)
        and all(
            isinstance(operacao, str)
            and operacao in {
                "count", "sum", "avg", "weighted_avg_kg", "min", "max", "group_by",
                "order_by", "list",
            }
            for operacao in normalizado["operations"]
        )
        and bool(normalizado["operations"])
    ):
        normalizado["operations"] = []
        normalizado["aggregation_field"] = None
        normalizado["group_by"] = []
        normalizado["order_by"] = []
        correcoes.append("operations.removidas_da_rota_rag_documental")

    # Para um registro identificado por PNCP, a operação list já retorna os
    # campos canônicos, inclusive valor e modalidade. aggregation_field não é
    # necessário sem sum/avg/min/max e pode confundir o plano.
    operations = normalizado.get("operations")
    operacoes_textuais = {
        operacao for operacao in operations if isinstance(operacao, str)
    } if isinstance(operations, list) else set()
    if (
        tem_pncp
        and isinstance(operations, list)
        and not {"sum", "avg", "min", "max"}.intersection(operacoes_textuais)
        and normalizado.get("aggregation_field") is not None
    ):
        normalizado["aggregation_field"] = None
        correcoes.append("aggregation_field.removido_de_lookup")

    entidade_esperada = _entidade_explicita_na_pergunta(pergunta)
    if (
        tem_pncp
        and normalizado.get("route") in {"structured", "hybrid"}
        and entidade_esperada is not None
        and normalizado.get("entity") != entidade_esperada
    ):
        normalizado["entity"] = entidade_esperada
        correcoes.append("entity.alinhada_com_pergunta")

    # A contratação (compra) não tem data de assinatura: ela existe em contratos
    # e atas. Quando o modelo pede esse campo para a entidade errada, a consulta
    # falharia na Structured Tool. Se a pergunta fala de contratos, a entidade
    # é corrigida; caso contrário, usamos a data de publicação, a única
    # disponível para a contratação.
    if (
        normalizado.get("route") in {"structured", "hybrid"}
        and normalizado.get("entity") == "contratacao"
        and _usa_campo_plano(normalizado, "data_assinatura")
    ):
        if _entidade_explicita_na_pergunta(pergunta) == "contrato":
            normalizado["entity"] = "contrato"
            correcoes.append("entity.contrato_por_data_assinatura")
        else:
            _trocar_campo_plano(normalizado, "data_assinatura", "data_publicacao")
            correcoes.append("campo_data.assinatura_para_publicacao")

    return normalizado, correcoes


def _usa_campo_plano(plano: dict[str, object], campo: str) -> bool:
    filtros = plano.get("filters")
    if isinstance(filtros, dict) and filtros.get("campo_data") == campo:
        return True
    if plano.get("aggregation_field") == campo:
        return True
    group_by = plano.get("group_by")
    if isinstance(group_by, list) and campo in group_by:
        return True
    order_by = plano.get("order_by")
    return isinstance(order_by, list) and any(
        isinstance(item, dict) and item.get("field") == campo for item in order_by
    )


def _trocar_campo_plano(plano: dict[str, object], antigo: str, novo: str) -> None:
    filtros = plano.get("filters")
    if isinstance(filtros, dict) and filtros.get("campo_data") == antigo:
        plano["filters"] = {**filtros, "campo_data": novo}
    if plano.get("aggregation_field") == antigo:
        plano["aggregation_field"] = novo
    group_by = plano.get("group_by")
    if isinstance(group_by, list) and antigo in group_by:
        # dict.fromkeys evita duplicar o campo se o novo já estiver agrupado.
        plano["group_by"] = list(
            dict.fromkeys(novo if item == antigo else item for item in group_by)
        )
    order_by = plano.get("order_by")
    if isinstance(order_by, list):
        plano["order_by"] = [
            {**item, "field": novo}
            if isinstance(item, dict) and item.get("field") == antigo
            else item
            for item in order_by
        ]


def _registrar_normalizacao(
    pergunta: str,
    resposta_llm: str,
    correcoes: list[str],
) -> None:
    """Registra a normalização sem pergunta ou saída bruta da LLM."""

    if not correcoes:
        return
    try:
        registrar(
            _observability_run_id(),
            "query_analyzer",
            status="ajustado",
            question_length=len(pergunta),
            llm_output_length=len(resposta_llm),
            correcoes=correcoes,
        )
    except Exception:
        pass


def _extrair_json(resposta: str) -> str:
    """Extrai o objeto JSON mesmo se a LLM incluir texto ou Markdown."""

    conteudo = resposta.strip()
    if not conteudo:
        raise QueryAnalysisError("A LLM retornou uma análise vazia.")

    # Alguns modelos devolvem "Aqui está o JSON:" ou uma cerca Markdown
    # apesar do prompt. Localizamos o primeiro objeto para manter o Analyzer
    # tolerante à apresentação, sem afrouxar a validação Pydantic posterior.
    conteudo = re.sub(r"^```(?:json)?\s*", "", conteudo, flags=re.IGNORECASE)
    conteudo = re.sub(r"\s*```\s*$", "", conteudo).strip()
    inicio = conteudo.find("{")
    fim = conteudo.rfind("}")
    if inicio == -1 or fim <= inicio:
        raise QueryAnalysisError("A LLM não retornou um objeto JSON válido.")
    return conteudo[inicio: fim + 1]


def analisar_pergunta(
    pergunta: str,
    llm_fn: AnalyzerLlmFn = gerar_resposta_analyzer,
    *,
    rota_obrigatoria: QueryRoute | None = None,
) -> QueryAnalysis:
    """Gera e valida um plano estruturado sem executar qualquer ferramenta."""

    pergunta_limpa = pergunta.strip()
    if not pergunta_limpa:
        raise QueryAnalysisError("A pergunta não pode estar vazia.")

    # Esta pré-classificação reduz latência e elimina uma dependência
    # desnecessária da Ecoa/Groq para perguntas puramente administrativas.
    if rota_obrigatoria is None:
        plano_catalogo = _analisar_pergunta_catalogo(pergunta_limpa)
        if plano_catalogo is not None:
            return plano_catalogo

    prompt = montar_prompt_analise(
        pergunta_limpa,
        rota_obrigatoria=(
            rota_obrigatoria.value if rota_obrigatoria is not None else None
        ),
    )
    with perf_stage("analyzer_llm"):
        with llm_http_operation("analyzer"):
            resposta = llm_fn(prompt)
    try:
        plano_bruto = json.loads(_extrair_json(resposta))
    except json.JSONDecodeError as exc:
        raise QueryAnalysisError("A LLM não retornou um JSON válido.") from exc

    if not isinstance(plano_bruto, dict):
        raise QueryAnalysisError("A análise deve ser um objeto JSON.")

    plano_bruto, correcoes = _normalizar_plano_conhecido(
        plano_bruto,
        pergunta_limpa,
    )
    _registrar_normalizacao(pergunta_limpa, resposta, correcoes)

    try:
        analysis = QueryAnalysis.model_validate(plano_bruto)
    except ValidationError as exc:
        erros_validacao = _resumir_erros_validacao(exc)
        _registrar_falha_validacao(
            pergunta_limpa,
            resposta,
            erros_validacao,
        )
        raise QueryAnalysisError(
            "A LLM retornou um plano inválido.",
            erros_validacao=erros_validacao,
        ) from exc

    if rota_obrigatoria is not None and analysis.route is not rota_obrigatoria:
        raise QueryAnalysisError(
            "A LLM retornou a rota "
            f"'{analysis.route.value}', mas este endpoint exige "
            f"'{rota_obrigatoria.value}'."
        )
    return analysis


def analisar_pergunta_estruturada(
    pergunta: str,
    llm_fn: AnalyzerLlmFn = gerar_resposta_analyzer,
) -> QueryAnalysis:
    """Analisa uma pergunta para o endpoint que aceita somente SQL seguro."""

    return analisar_pergunta(
        pergunta,
        llm_fn=llm_fn,
        rota_obrigatoria=QueryRoute.STRUCTURED,
    )


_FOLLOW_UP_RE = re.compile(
    r"^\s*(?:e\b|agora\b|somente\b|apenas\b|desses?\b|dessas?\b)",
    re.IGNORECASE,
)


def _atualizacoes_contextuais_explicitas(pergunta: str) -> dict[str, object]:
    """Extrai apenas mudanças inequívocas aceitas no MVP conversacional."""

    texto = pergunta.casefold()
    updates: dict[str, object] = {}
    if re.search(r"\b(?:estadual|estaduais)\b", texto):
        updates["esfera"] = "E"
    elif re.search(r"\b(?:municipal|municipais)\b", texto):
        updates["esfera"] = "M"
    elif re.search(r"\b(?:federal|federais)\b", texto):
        updates["esfera"] = "F"

    years = re.findall(r"\b(20\d{2})\b", texto)
    if len(set(years)) == 1:
        updates["ano"] = int(years[0])
    return updates


def analisar_pergunta_com_contexto(
    pergunta: str,
    contexto: QueryAnalysis,
    llm_fn: AnalyzerLlmFn = gerar_resposta_analyzer,
) -> QueryAnalysis:
    """Aplica um acompanhamento seguro ou analisa uma nova pergunta.

    A função nunca concatena o histórico ao prompt. Apenas um plano Pydantic
    previamente validado pode ser reutilizado, e somente filtros expressos de
    forma inequívoca são substituídos.
    """

    clean = pergunta.strip()
    if not clean:
        raise QueryAnalysisError("A pergunta não pode estar vazia.")

    explicit_entity = _entidade_explicita_na_pergunta(clean)
    is_follow_up = bool(_FOLLOW_UP_RE.search(clean))
    if not is_follow_up or (
        explicit_entity is not None and explicit_entity != contexto.entity.value
    ):
        return analisar_pergunta(clean, llm_fn=llm_fn)

    if contexto.route is not QueryRoute.STRUCTURED:
        raise QueryAnalysisError(
            "O acompanhamento documental ficou ambíguo. Repita a pergunta completa."
        )

    updates = _atualizacoes_contextuais_explicitas(clean)
    if not updates:
        raise QueryAnalysisError(
            "Não foi possível identificar com segurança o filtro alterado. "
            "Informe a entidade, o período ou o filtro desejado."
        )

    filters = contexto.filters.model_copy(update=updates)
    return contexto.model_copy(update={"filters": filters}, deep=True)
