"""Seleção configurável de LLM para Analyzer e geração RAG.

O módulo não participa da criação de embeddings. A seleção do provedor de
embeddings é independente e o índice registra modelo e dimensão.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Literal

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.core.config import settings
from app.core.llm_http_observability import llm_http_operation
from app.core.logging import perf_event


ProviderName = Literal["ecoa", "openai", "groq"]
TextGenerator = Callable[[str], str]


class LlmProviderError(RuntimeError):
    """Nenhum provedor configurado conseguiu gerar uma resposta."""


@dataclass(frozen=True)
class LlmGeneration:
    content: str
    provider: ProviderName
    model: str
    fallback_used: bool = False
    fallback_reason: str | None = None

    def audit_metadata(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }


def _provedor_fallback(operation: str) -> str:
    """Resolve o provedor canônico, preservando a flag legada do Analyzer."""

    if settings.llm_fallback_provider != "none":
        return settings.llm_fallback_provider
    if operation == "analyzer" and settings.analyzer_openai_fallback_enabled:
        return "openai"
    return "none"


def _modelo_fallback(provider: str) -> str:
    if provider == "openai":
        # A variável nova prevalece; a antiga mantém ambientes já configurados.
        return settings.llm_fallback_openai_model or settings.analyzer_openai_model
    if provider == "groq":
        return settings.llm_fallback_groq_model
    raise ValueError(f"Provedor de fallback não suportado: {provider}.")


def _modelo_primario(provider: str) -> str:
    if provider == "openai":
        return settings.llm_primary_openai_model
    if provider == "groq":
        return settings.llm_primary_groq_model
    raise ValueError(f"Provedor principal não suportado: {provider}.")


def _motivo_fallback(error: Exception) -> str | None:
    if isinstance(error, APITimeoutError):
        return "timeout"
    if isinstance(error, APIConnectionError):
        return "erro_conexao"
    if isinstance(error, APIStatusError):
        if error.status_code == 429:
            return "http_429"
        if error.status_code >= 500:
            return "http_5xx"
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return "timeout_or_connection"
    return None


def _conteudo(response: object, provider: str) -> str:
    content = getattr(response, "content", None)
    if content is None or not str(content).strip():
        raise LlmProviderError(f"{provider} retornou uma resposta vazia.")
    return str(content)


def _gerar_openai(prompt: str, model: str) -> str:
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key:
        raise LlmProviderError("Fallback OpenAI indisponível: configure OPENAI_API_KEY no .env.")
    client = OpenAI(
        api_key=api_key,
        timeout=settings.llm_fallback_timeout_seconds,
        max_retries=0,
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = response.choices[0].message.content
    if content is None or not content.strip():
        raise LlmProviderError("OpenAI retornou uma resposta vazia.")
    return content


def _gerar_groq(prompt: str, model: str) -> str:
    api_key = settings.groq_api_key.get_secret_value()
    if not api_key:
        raise LlmProviderError("Fallback Groq indisponível: configure GROQ_API_KEY no .env.")
    try:
        from langchain_groq import ChatGroq
    except ImportError as error:
        raise LlmProviderError(
            "Fallback Groq indisponível: instale langchain-groq pelas dependências do projeto."
        ) from error
    llm = ChatGroq(
        model=model,
        temperature=0,
        timeout=settings.llm_fallback_timeout_seconds,
        max_retries=0,
    )
    return _conteudo(llm.invoke(prompt), "Groq")


def _executar_fallback(prompt: str, provider: str, model: str, operation: str) -> str:
    started = time.perf_counter()
    perf_event(f"{operation}_fallback", "inicio", provedor=provider, modelo=model)
    try:
        with llm_http_operation(operation, provider=provider):
            if provider == "openai":
                content = _gerar_openai(prompt, model)
            elif provider == "groq":
                content = _gerar_groq(prompt, model)
            else:
                raise ValueError(f"Provedor de fallback inválido: {provider}.")
    except BaseException as error:
        perf_event(
            f"{operation}_fallback", "erro", (time.perf_counter() - started) * 1000,
            type(error).__name__, provedor=provider,
        )
        raise
    perf_event(
        f"{operation}_fallback", "ok", (time.perf_counter() - started) * 1000,
        provedor=provider, modelo=model,
    )
    return content


def gerar_com_fallback(
    prompt: str,
    *,
    operation: str,
    ecoa_fn: TextGenerator,
    ecoa_model: str,
    fallback_fn: Callable[[str, str, str], str] | None = None,
) -> LlmGeneration:
    """Executa o provedor principal e aplica fallback apenas quando permitido.

    ``ecoa`` continua sendo o padrão por compatibilidade. Quando OpenAI ou
    Groq é selecionado como principal, o Ecoa não é chamado.
    """

    primary_provider = settings.llm_primary_provider
    if primary_provider != "ecoa":
        model = _modelo_primario(primary_provider)
        try:
            if fallback_fn is not None:
                content = fallback_fn(prompt, primary_provider, model)
            else:
                content = _executar_fallback(
                    prompt, primary_provider, model, operation
                )
        except Exception as primary_error:
            raise LlmProviderError(
                "O provedor principal de LLM está indisponível "
                f"({primary_provider}/{type(primary_error).__name__})."
            ) from primary_error
        return LlmGeneration(
            content=content,
            provider=primary_provider,
            model=model,
        )

    try:
        with llm_http_operation(operation, provider="ecoa"):
            content = ecoa_fn(prompt)
        return LlmGeneration(content=content, provider="ecoa", model=ecoa_model)
    except Exception as ecoa_error:
        motivo = _motivo_fallback(ecoa_error)
        provider = _provedor_fallback(operation)
        if motivo is None:
            raise

        # Um timeout do Ecoa sem fallback configurado é indisponibilidade de
        # serviço, não erro interno da aplicação. Ao unificar a exceção, as
        # rotas conseguem responder HTTP 503 em vez de HTTP 500.
        if provider == "none":
            raise LlmProviderError(
                "Ecoa não respondeu a tempo e não há fallback configurado. "
                "Configure LLM_FALLBACK_PROVIDER=groq ou openai no .env."
            ) from ecoa_error

        model = _modelo_fallback(provider)
        try:
            if fallback_fn is not None:
                content = fallback_fn(prompt, provider, model)
            else:
                content = _executar_fallback(prompt, provider, model, operation)
        except Exception as fallback_error:
            raise LlmProviderError(
                "Ecoa falhou e o fallback também não respondeu. "
                f"Ecoa={type(ecoa_error).__name__}; "
                f"Fallback={provider}/{type(fallback_error).__name__}."
            ) from fallback_error
        return LlmGeneration(
            content=content,
            provider=provider,
            model=model,
            fallback_used=True,
            fallback_reason=motivo,
        )
