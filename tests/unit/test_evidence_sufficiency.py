from pathlib import Path
from unittest.mock import Mock

from src.generate import (
    MENSAGEM_SEM_EVIDENCIA,
    gerar_resposta_rag,
    verificar_suficiencia_evidencias,
)
from src.retrieve import RankedChunk


def chunk(chunk_id: str, content: str) -> RankedChunk:
    return RankedChunk(chunk_id, content, 1.0, 1, 1.0, 1, 1.0, {})


def test_classifica_ausencia_e_cobertura_parcial():
    assert verificar_suficiencia_evidencias("pergunta", []).status == "absent"
    parcial = verificar_suficiencia_evidencias(
        "competências e atribuições",
        [chunk("a", "competências comuns dos fiscais")],
    )
    assert parcial.status == "partial"
    assert parcial.missing_aspects


def test_bloqueia_llm_sem_evidencia_e_registra_decisao(tmp_path: Path):
    llm = Mock(return_value="não deveria chamar")
    resposta = gerar_resposta_rag(
        None,
        "qualquer pergunta",
        retrieval_fn=lambda **_: [],
        llm_fn=llm,
        audit_dir=tmp_path,
    )
    assert resposta.answer == MENSAGEM_SEM_EVIDENCIA
    assert resposta.sources == []
    llm.assert_not_called()
