"""
Log estruturado em JSONL (item 2.4 do enunciado).

Todo módulo do pipeline registra por onde passou, em
logs/pipeline-AAAA-MM-DD.jsonl, uma linha por evento. O `run_id` amarra todas
as etapas de uma mesma execução: sem ele, os logs são ruído.

A pasta logs/ é ignorada pelo Git.
"""

import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

PASTA_LOGS = Path("logs")

# Etapas da preparação do corpus. Complementam PIPELINE_STAGES
# (app/modules/rag/model.py), que cobre só a fase de consulta.
# Proposta ao time: acrescentar "coleta" e "ingestao" à lista canônica.
STAGE_COLETA = "coleta"
STAGE_INGESTAO = "ingestao"


_perf_run_id: ContextVar[str | None] = ContextVar("rag_perf_run_id", default=None)
_perf_logger = logging.getLogger("uvicorn.error")
_PERF_METRICS = frozenset({
    "chunks_elegiveis", "candidatos", "fontes", "vetores",
    "tentativa", "status_http", "retry", "espera_ms", "fallback",
    "tokens_entrada", "tokens_saida", "tokens_total",
})
_PERF_TEXT_METRICS = frozenset({"provedor", "motivo"})

# Campos adicionais aceitos no JSONL operacional. A lista positiva evita que
# novos chamadores persistam acidentalmente perguntas, prompts, respostas,
# cabeçalhos ou payloads completos. Textos livres não fazem parte deste
# contrato: identificadores precisam usar somente o alfabeto técnico abaixo.
_SAFE_EXTRA_NUMBERS = frozenset({
    "bytes", "error_count", "llm_output_length", "question_length",
    "result_count", "status_http", "validation_error_count",
})
_SAFE_EXTRA_BOOLEANS = frozenset({"baixado", "fallback", "retry"})
_SAFE_EXTRA_IDENTIFIERS = frozenset({
    "base_id", "error_code", "error_type", "provider", "resource_type",
    "route", "versao",
})
_SAFE_EXTRA_IDENTIFIER_LISTS = frozenset({
    "correcoes", "evidence_types", "validation_error_fields",
})
_TECHNICAL_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/"
)
_SENSITIVE_IDENTIFIER_PATTERNS = (
    re.compile(r"(?i)^sk-(?:proj-|or-v1-)?[a-z0-9_-]{12,}$"),
    re.compile(r"(?i)^bearer[._:/-]?[a-z0-9._=-]{12,}$"),
    re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$"),
    re.compile(r"^\d{3}\.\d{3}\.\d{3}-\d{2}$"),
)
_EXCEPTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_SAFE_PROVIDERS = frozenset({"custom", "ecoa", "groq", "openai"})
_SAFE_ROUTES = frozenset({"catalog", "hybrid", "rag", "structured"})


def _content_metadata(value):
    """Descreve conteúdo sem persistir seu valor bruto."""

    if value is None:
        return None
    if isinstance(value, str):
        return {"type": "text", "length": len(value)}
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "field_count": len(value)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {"type": "collection", "item_count": len(value)}
    return {"type": type(value).__name__}


def _technical_identifier(value):
    """Aceita apenas identificadores internos curtos, nunca texto livre."""

    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if any(character not in _TECHNICAL_IDENTIFIER_CHARS for character in value):
        return None
    if value.startswith(("/", "./", "../")) or "/../" in value:
        return None
    if any(pattern.search(value) for pattern in _SENSITIVE_IDENTIFIER_PATTERNS):
        return None
    numeric_candidate = value.translate(str.maketrans("", "", ".-_:/"))
    if len(numeric_candidate) >= 9 and numeric_candidate.isdigit():
        return None
    return value


def _exception_name(value):
    """Aceita somente o nome de uma classe de exceção, nunca sua mensagem."""

    if not isinstance(value, str) or _EXCEPTION_NAME.fullmatch(value) is None:
        return None
    return value


def _safe_enum_identifier(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def _safe_extra(extra):
    """Aplica uma allowlist estrutural aos metadados adicionais."""

    safe = {}
    for key, value in extra.items():
        if key in _SAFE_EXTRA_NUMBERS and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            safe[key] = value
        elif key in _SAFE_EXTRA_BOOLEANS and isinstance(value, bool):
            safe[key] = value
        elif key in _SAFE_EXTRA_IDENTIFIERS:
            if key == "provider":
                identifier = _safe_enum_identifier(value, _SAFE_PROVIDERS)
            elif key == "route":
                identifier = _safe_enum_identifier(value, _SAFE_ROUTES)
            elif key == "error_type":
                identifier = _exception_name(value)
            else:
                identifier = _technical_identifier(value)
            if identifier is not None:
                safe[key] = identifier
        elif key in _SAFE_EXTRA_IDENTIFIER_LISTS and isinstance(value, (list, tuple)):
            identifiers = [
                identifier
                for item in value
                if (identifier := _technical_identifier(item)) is not None
            ]
            safe[key] = identifiers
    return safe


def _safe_tokens(tokens):
    """Mantém somente contadores numéricos do contrato de tokens."""

    source = tokens if isinstance(tokens, dict) else {}
    return {
        key: value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
        for key, value in (
            ("prompt", source.get("prompt", 0)),
            ("completion", source.get("completion", 0)),
            ("total", source.get("total", 0)),
        )
    }


def _safe_query_id(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _technical_identifier(value)


def _perf_event(stage, status, duration_ms=None, exception=None, metrics=None):
    """Emite apenas campos técnicos; falhas de logging não afetam a consulta."""
    run_id = _perf_run_id.get()
    if run_id is None:
        return
    try:
        safe_run_id = _technical_identifier(run_id)
        safe_stage = _technical_identifier(stage)
        safe_status = _technical_identifier(status)
        if safe_run_id is None or safe_stage is None or safe_status is None:
            return
        fields = [f"[RAG-PERF] run_id={safe_run_id}", f"etapa={safe_stage}"]
        if duration_ms is not None:
            fields.append(f"duracao_ms={duration_ms:.3f}")
        fields.append(f"status={safe_status}")
        safe_exception = _exception_name(exception)
        if safe_exception is not None:
            fields.append(f"excecao={safe_exception}")
        for key, value in (metrics or {}).items():
            if key in _PERF_METRICS and isinstance(value, (int, float)):
                fields.append(f"{key}={value}")
            elif key in _PERF_TEXT_METRICS and isinstance(value, str):
                seguro = (
                    _safe_enum_identifier(value, _SAFE_PROVIDERS)
                    if key == "provedor"
                    else _technical_identifier(value)
                )
                if seguro:
                    fields.append(f"{key}={seguro}")
        _perf_logger.info(" ".join(fields))
    except Exception:
        pass


def perf_event(stage, status, duration_ms=None, exception=None, **metrics):
    """Interface segura para eventos observacionais correlacionados."""
    _perf_event(stage, status, duration_ms, exception, metrics)


def current_perf_run_id():
    return _perf_run_id.get()


@contextmanager
def perf_stage(stage):
    """Mede somente etapas executadas dentro de uma requisição instrumentada."""
    metrics = {}
    from app.core.config import settings

    if _perf_run_id.get() is None or not settings.rag_perf_log_enabled:
        yield metrics
        return
    started = time.perf_counter()
    _perf_event(stage, "inicio")
    try:
        yield metrics
    except BaseException as error:
        _perf_event(stage, "erro", (time.perf_counter() - started) * 1000,
                    type(error).__name__, metrics)
        raise
    else:
        _perf_event(stage, "ok", (time.perf_counter() - started) * 1000, metrics=metrics)


class RagPerfMiddleware:
    """Observa as consultas RAG HTTP sem consumir ou alterar mensagens."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from app.core.config import settings

        perf_enabled = settings.rag_perf_log_enabled
        llm_http_enabled = settings.rag_llm_http_log_enabled
        observed_paths = {"/query/ask", "/query/generate"}
        if (not (perf_enabled or llm_http_enabled) or scope["type"] != "http"
                or scope.get("method") != "POST"
                or scope.get("path") not in observed_paths):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        token = _perf_run_id.set(novo_run_id())
        if perf_enabled:
            _perf_event("recebimento", "ok", (time.perf_counter() - started) * 1000)
            _perf_event("total", "inicio")
        response_status = None
        error_type = None

        async def observed_send(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except BaseException as error:
            error_type = type(error).__name__
            raise
        finally:
            status = "erro" if error_type or (response_status is not None and response_status >= 400) else "ok"
            if perf_enabled:
                _perf_event("total", status, (time.perf_counter() - started) * 1000, error_type)
            _perf_run_id.reset(token)


def novo_run_id() -> str:
    """Identificador curto de uma execução inteira."""
    return uuid.uuid4().hex[:8]


def registrar(run_id, stage, status="ok", latencia_ms=None, entrada=None,
              saida=None, tokens=None, modelo=None, query_id=None, **extra):
    """Grava somente telemetria minimizada; conteúdo bruto nunca vai ao JSONL."""
    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    evento = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_id": _technical_identifier(run_id),
        "query_id": _safe_query_id(query_id),
        "stage": _technical_identifier(stage),
        "status": _technical_identifier(status),
        "latencia_ms": (
            latencia_ms
            if isinstance(latencia_ms, (int, float)) and not isinstance(latencia_ms, bool)
            else None
        ),
        "input": _content_metadata(entrada),
        "output": _content_metadata(saida),
        # Coleta e ingestão não chamam o LLM: os tokens ficam zerados de
        # propósito, para o campo existir em toda etapa.
        "tokens": _safe_tokens(tokens),
        "modelo": _technical_identifier(modelo),
        "extra": _safe_extra(extra),
    }
    arquivo = PASTA_LOGS / f"pipeline-{datetime.now():%Y-%m-%d}.jsonl"
    with arquivo.open("a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")
    return evento


@contextmanager
def etapa(run_id, stage, entrada=None, **extra):
    """
    Mede a duração da etapa e registra o resultado. Se estourar exceção, grava
    com status "erro" e deixa a exceção subir — o log não engole o problema.

        with etapa(run_id, "coleta", entrada=url) as ev:
            conteudo = baixar(url)
            ev["saida"] = f"{len(conteudo)} bytes"
    """
    inicio = time.perf_counter()
    dados = {"saida": None}
    try:
        yield dados
    except Exception as erro:
        registrar(
            run_id,
            stage,
            "erro",
            int((time.perf_counter() - inicio) * 1000),
            entrada,
            None,
            error_type=type(erro).__name__,
            **extra,
        )
        raise
    registrar(run_id, stage, "ok", int((time.perf_counter() - inicio) * 1000),
              entrada, dados["saida"], **extra)
