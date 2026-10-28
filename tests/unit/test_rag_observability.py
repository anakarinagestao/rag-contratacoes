from unittest.mock import Mock, patch

from app.modules.rag.services import recuperar_chunks_ranqueados
from src.generate import RagAnswer
from src.retrieve import RankedChunk


def test_service_encaminha_collector_sem_alterar_parametros():
    collector = {"parameters": {}, "ranking_stages": {}}
    expected = [RankedChunk("doc:p1", "texto", 1.0, 1, 1.0, 1, 1.0, {})]
    with patch("app.modules.rag.services.retrieve_hibrido", return_value=expected) as retrieve:
        result = recuperar_chunks_ranqueados(
            db=None, pergunta="pergunta", top_k=5, trace_collector=collector
        )
    assert result == expected
    assert retrieve.call_args.kwargs["top_k"] == 5
    assert retrieve.call_args.kwargs["trace_collector"] is collector


def test_rag_answer_exposes_trace_id():
    answer = RagAnswer("resposta", [], trace_id="trace-123")
    assert answer.trace_id == "trace-123"
