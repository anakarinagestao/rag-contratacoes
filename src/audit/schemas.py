from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credentials",
    "jwt",
    "openai_api_key",
    "password",
    "secret",
    "senha",
    "set-cookie",
    "token",
}

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bsk-or-v1-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9_\-\.=]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|password|senha|secret)\s*[:=]\s*[^\s,;]+"),
)


AuditTrace = dict[str, Any]

_TECHNICAL_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/#"
)
_SENSITIVE_IDENTIFIER_PATTERNS = (
    re.compile(r"(?i)^sk-(?:proj-|or-v1-)?[a-z0-9_-]{12,}$"),
    re.compile(r"(?i)^bearer[._:/-]?[a-z0-9._=-]{12,}$"),
    re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$"),
    re.compile(r"^\d{3}\.\d{3}\.\d{3}-\d{2}$"),
)
_SAFE_PROVIDERS = frozenset({"custom", "ecoa", "groq", "openai"})
_SAFE_STATUSES = frozenset({"blocked", "error", "running", "success"})
_SAFE_STAGES = frozenset({
    "completed", "generation", "guardrail", "persistence", "retrieval", "started",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_trace_id() -> str:
    return str(uuid4())


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _technical_identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if any(character not in _TECHNICAL_IDENTIFIER_CHARS for character in value):
        return None
    if value.startswith(("/", "./", "../")) or "/../" in value:
        return None
    if any(pattern.search(value) for pattern in _SENSITIVE_IDENTIFIER_PATTERNS):
        return None
    numeric_candidate = value.translate(str.maketrans("", "", ".-_:/#"))
    if len(numeric_candidate) >= 9 and numeric_candidate.isdigit():
        return None
    return value


def _safe_uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        return None


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _safe_bool(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _content_length(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes, list, tuple, dict)):
        return len(value)
    return None


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def minimize_audit_trace(trace: AuditTrace) -> AuditTrace:
    """Mantém somente metadados operacionais previamente permitidos.

    Esta allowlist é a barreira primária de privacidade. A sanitização por
    padrões continua sendo aplicada depois apenas como defesa adicional.
    """

    request = _mapping(trace.get("request"))
    filters = _mapping(request.get("filters"))
    structured = _mapping(request.get("structured_result"))
    guardrail = _mapping(trace.get("guardrail"))
    retrieval = _mapping(trace.get("retrieval"))
    generation = _mapping(trace.get("generation"))
    persistence = _mapping(trace.get("persistence"))
    error = _mapping(trace.get("error"))

    chunks = retrieval.get("chunks")
    chunks = chunks if isinstance(chunks, list) else []
    evidence = []
    for source in chunks:
        item = _mapping(source)
        evidence.append({
            key: value
            for key, value in (
                ("rank", _safe_number(item.get("rank"))),
                ("chunk_id", _technical_identifier(item.get("chunk_id"))),
                ("document_id", _safe_number(item.get("document_id"))),
                ("bm25_rank", _safe_number(item.get("bm25_rank"))),
                ("bm25_score", _safe_number(item.get("bm25_score"))),
                ("dense_rank", _safe_number(item.get("dense_rank"))),
                ("dense_score", _safe_number(item.get("dense_score"))),
                ("rrf_score", _safe_number(item.get("rrf_score"))),
            )
            if value is not None
        })

    bases = request.get("bases")
    safe_bases = [
        identifier
        for value in (bases if isinstance(bases, list) else [])
        if (identifier := _technical_identifier(value)) is not None
    ]
    base_ids = retrieval.get("base_ids_permitidos")
    safe_base_ids = [
        value
        for value in (base_ids if isinstance(base_ids, list) else [])
        if isinstance(value, int) and not isinstance(value, bool)
    ]

    minimized: AuditTrace = {
        "trace_id": _safe_uuid(trace.get("trace_id")),
        "started_at": _safe_timestamp(trace.get("started_at")),
        "finished_at": _safe_timestamp(trace.get("finished_at")),
        "elapsed_ms": _safe_number(trace.get("elapsed_ms")),
        "status": (
            trace.get("status") if trace.get("status") in _SAFE_STATUSES else None
        ),
        "stage": trace.get("stage") if trace.get("stage") in _SAFE_STAGES else None,
        "request": {
            "question_length": _content_length(request.get("question")),
            "top_k": _safe_number(request.get("top_k")),
            "filter_fields": sorted(
                identifier
                for key, value in filters.items()
                if value is not None
                and (identifier := _technical_identifier(str(key))) is not None
            ),
            "bases": safe_bases,
            "structured_result": {
                "entity": _technical_identifier(structured.get("entity")),
                "row_count": _safe_number(structured.get("row_count")),
                "limit_applied": _safe_number(structured.get("limit_applied")),
                "columns": [
                    identifier
                    for value in (
                        structured.get("columns")
                        if isinstance(structured.get("columns"), list)
                        else []
                    )
                    if (identifier := _technical_identifier(value)) is not None
                ],
            } if structured else None,
        },
        "guardrail": {
            "checked": _safe_bool(guardrail.get("checked")),
            "triggered": _safe_bool(guardrail.get("triggered")),
            "reason": _technical_identifier(guardrail.get("reason")),
            "message_length": _content_length(guardrail.get("message")),
        },
        "retrieval": {
            "strategy": _technical_identifier(retrieval.get("strategy")),
            "base_ids_permitidos": safe_base_ids,
            "evidence_count": len(chunks),
            "evidence": evidence,
        },
        "generation": {
            "model": _technical_identifier(generation.get("model")),
            "provider": (
                generation.get("provider")
                if generation.get("provider") in _SAFE_PROVIDERS
                else None
            ),
            "fallback_used": _safe_bool(generation.get("fallback_used")),
            "fallback_reason": _technical_identifier(
                generation.get("fallback_reason")
            ),
            "max_tokens": _safe_number(generation.get("max_tokens")),
            "elapsed_ms": _safe_number(generation.get("elapsed_ms")),
            "context_length": _content_length(generation.get("contexto")),
            "prompt_length": _content_length(generation.get("prompt")),
            "answer_length": _content_length(generation.get("answer")),
        },
        "persistence": {
            "enabled": _safe_bool(persistence.get("enabled")),
            "query_id": _safe_number(persistence.get("query_id")),
            "response_id": _safe_number(persistence.get("response_id")),
            "run_id": _safe_uuid(persistence.get("run_id")),
        },
        "error": (
            {"type": _technical_identifier(error.get("type"))}
            if error else None
        ),
    }
    return minimized


def sanitize_for_audit(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower().replace("-", "_") in SENSITIVE_KEYS:
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = sanitize_for_audit(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_audit(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_audit(item) for item in value]
    if isinstance(value, str):
        sanitized = value
        for pattern in SECRET_PATTERNS:
            sanitized = pattern.sub("[REDACTED]", sanitized)
        return sanitized
    return value
