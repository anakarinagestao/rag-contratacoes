"""Erros de domínio seguros para indisponibilidade da recuperação vetorial."""

from __future__ import annotations

from typing import Final


FAISS_PUBLIC_MESSAGE: Final = "Recuperação documental temporariamente indisponível."


class FaissIndexMissingError(FileNotFoundError):
    """O conjunto de artefatos esperado para o índice não existe."""

    error_code = "faiss_index_missing"
    public_message = FAISS_PUBLIC_MESSAGE

    def __init__(self) -> None:
        super().__init__("O índice FAISS configurado está ausente.")


class FaissIndexCorruptError(RuntimeError):
    """Os artefatos existem, mas não formam um índice íntegro."""

    error_code = "faiss_index_corrupt"
    public_message = FAISS_PUBLIC_MESSAGE

    def __init__(self) -> None:
        super().__init__("O índice FAISS está corrompido ou inconsistente.")


class FaissIndexIncompatibleError(RuntimeError):
    """O índice é legível, mas incompatível com a consulta atual."""

    error_code = "faiss_index_incompatible"
    public_message = FAISS_PUBLIC_MESSAGE

    def __init__(self) -> None:
        super().__init__("O índice FAISS é incompatível com a configuração atual.")


FaissUnavailableError = (
    FaissIndexMissingError | FaissIndexCorruptError | FaissIndexIncompatibleError
)

FAISS_UNAVAILABLE_ERRORS: Final = (
    FaissIndexMissingError,
    FaissIndexCorruptError,
    FaissIndexIncompatibleError,
)


def faiss_public_detail(error: FaissUnavailableError) -> dict[str, str]:
    """Contrato HTTP estável, sem causa técnica, caminho ou payload interno."""

    return {
        "code": error.error_code,
        "message": error.public_message,
    }
