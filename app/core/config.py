from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    normalized_database_url: str | None = None
    rag_perf_log_enabled: bool = False
    rag_llm_http_log_enabled: bool = False
    generation_disable_thinking: bool = False
    rag_faiss_index_dir: Path = Path("data/index/faiss/v1")
    # Provedor principal explícito. O padrão Ecoa preserva ambientes antigos;
    # cada integrante pode selecionar OpenAI sem consultar o Ecoa primeiro.
    llm_primary_provider: Literal["ecoa", "openai", "groq"] = "ecoa"
    llm_primary_openai_model: str = "gpt-4.1-mini"
    llm_primary_groq_model: str = "openai/gpt-oss-120b"
    # Configuração canônica do fallback, compartilhada por Analyzer e geração.
    # "none" desabilita a troca; OpenAI e Groq são selecionados apenas pelo .env.
    llm_fallback_provider: Literal["none", "openai", "groq"] = "none"
    llm_fallback_openai_model: str = ""
    llm_fallback_groq_model: str = "openai/gpt-oss-120b"
    llm_fallback_timeout_seconds: float = Field(default=45.0, gt=0, le=600)
    groq_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    ecoa_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    embedding_provider: Literal["ecoa", "openai"] = "ecoa"
    embedding_model: str = ""
    embedding_dimensions: int = Field(default=1024, gt=0, le=3072)
    embedding_timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    # Compatibilidade temporária com a configuração que entrou na default.
    # Se o provedor canônico estiver "none", ela ainda vale só para Analyzer.
    analyzer_openai_fallback_enabled: bool = False
    analyzer_openai_model: str = "gpt-4.1-mini"
    analyzer_ecoa_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, gt=0, le=10080)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def resolver_caminho_indice_faiss(caminho: Path | str | None = None) -> Path:
    """Resolve em um só lugar o diretório FAISS efetivamente usado pelo RAG."""

    selecionado = settings.rag_faiss_index_dir if caminho is None else Path(caminho)
    return selecionado.expanduser().resolve()
