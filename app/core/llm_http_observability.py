"""Observabilidade HTTP da ECOA usando event hooks públicos do HTTPX."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.core.logging import current_perf_run_id, perf_event


@dataclass
class _OperationState:
    kind: str
    provider: str | None = None
    attempt: int = 0
    started: float | None = None
    last_response_finished: float | None = None
    response_received: bool = False


_operation: ContextVar[_OperationState | None] = ContextVar(
    "rag_llm_http_operation", default=None
)


def _enabled() -> bool:
    from app.core.config import settings

    return settings.rag_llm_http_log_enabled and current_perf_run_id() is not None


def _stage(kind: str, suffix: str) -> str:
    return f"{kind}_http_{suffix}"


@contextmanager
def llm_http_operation(kind: str, provider: str | None = None):
    """Identifica analyzer, generation ou embedding para os hooks HTTP."""
    if not _enabled():
        yield
        return
    state = _OperationState(kind=kind, provider=provider)
    token = _operation.set(state)
    try:
        yield
    except BaseException as error:
        if state.started is not None and not state.response_received:
            perf_event(
                _stage(kind, "attempt"), "erro",
                (time.perf_counter() - state.started) * 1000,
                type(error).__name__, tentativa=state.attempt,
                **({"provedor": provider} if provider else {}),
            )
        raise
    finally:
        _operation.reset(token)


def http_request_hook(_request) -> None:
    """Chamado pelo HTTPX imediatamente antes de cada envio HTTP."""
    state = _operation.get()
    if state is None or not _enabled():
        return
    now = time.perf_counter()
    if state.started is not None and not state.response_received:
        perf_event(
            _stage(state.kind, "attempt"), "erro_sem_resposta",
            (now - state.started) * 1000, tentativa=state.attempt,
            **({"provedor": state.provider} if state.provider else {}),
        )
    state.attempt += 1
    provider = {"provedor": state.provider} if state.provider else {}
    if state.attempt > 1:
        metrics = {"tentativa": state.attempt, "retry": 1}
        if state.last_response_finished is not None:
            metrics["espera_ms"] = (now - state.last_response_finished) * 1000
        perf_event(_stage(state.kind, "retry"), "inicio", **metrics, **provider)
    state.started = now
    state.response_received = False


def http_response_hook(response) -> None:
    """Chamado pelo HTTPX após receber a resposta de cada tentativa."""
    state = _operation.get()
    if state is None or state.started is None or not _enabled():
        return
    now = time.perf_counter()
    perf_event(
        _stage(state.kind, "attempt"),
        "ok" if response.status_code < 400 else "erro_http",
        (now - state.started) * 1000,
        tentativa=state.attempt,
        status_http=response.status_code,
        retry=1 if state.attempt > 1 else 0,
        **({"provedor": state.provider} if state.provider else {}),
    )
    state.response_received = True
    state.last_response_finished = now


def log_current_usage(usage) -> None:
    """Registra somente contagens fornecidas pelo provedor, nunca conteúdo."""
    state = _operation.get()
    if state is None or usage is None or not _enabled():
        return
    input_tokens = getattr(usage, "prompt_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    metrics = {}
    if isinstance(input_tokens, int):
        metrics["tokens_entrada"] = input_tokens
    if isinstance(output_tokens, int):
        metrics["tokens_saida"] = output_tokens
    if isinstance(total_tokens, int):
        metrics["tokens_total"] = total_tokens
    if metrics:
        if state.provider:
            metrics["provedor"] = state.provider
        perf_event(_stage(state.kind, "usage"), "ok", **metrics)
