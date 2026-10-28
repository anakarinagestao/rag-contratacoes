"""Protótipo offline de seleção por cobertura; não é importado pela produção."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / "logs/queries/2026-10-22/a981f0a8-eacb-4bfc-bb6c-372ea3b481c5.json"
PREFIX = "editais_normativo:decreto-48817-2023-gestao-fiscalizacao:"
QUESTION = (
    "Quais são as competências comuns dos fiscais de contrato e quais são as "
    "atribuições específicas dos fiscais técnicos e administrativos?"
)


def words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    aliases = {"competencias": "dever", "atribuicoes": "dever", "fiscais": "fiscal"}
    stop = {"quais", "sao", "dos", "das", "comuns", "especificas", "conforme"}
    return {aliases.get(x, x) for x in re.findall(r"[a-z]{4,}", text) if x not in stop}


def aspects(question: str) -> list[str]:
    return ["competencia comum dos fiscais", "atribuicao do fiscal tecnico", "atribuicao do fiscal administrativo"]


def coverage(content: str, aspect: str) -> bool:
    tokens = words(content)
    actions = {"verificar", "cobrar", "assegurar", "receber", "acompanhar", "solicitar", "instruir", "cumprir", "conhecer"}
    role = "tecnico" if "tecnico" in aspect else "administrativo" if "administrativo" in aspect else "fiscal"
    if role != "fiscal" and role not in tokens:
        return False
    if role == "fiscal" and "fiscal" not in tokens:
        return False
    # Papel isolado não cobre uma atribuição; exige verbo/obrigação concreta.
    return bool(tokens & actions)


def prototype_select(candidates: list[dict], question: str, limit: int) -> list[dict]:
    wanted = aspects(question)
    selected: list[dict] = []
    covered: set[str] = set()
    remaining = list(candidates)
    while remaining and len(selected) < limit:
        def utility(item):
            gained = sum(coverage(item["content"], aspect) for aspect in wanted if aspect not in covered)
            redundant = sum(coverage(item["content"], aspect) for aspect in wanted if aspect in covered)
            continuity = int(any(item["chunk_id"].rsplit(":", 1)[0] + ":p" + str(int(item["chunk_id"].rsplit(":p", 1)[-1]) - 1) == x["chunk_id"] for x in selected))
            relevance = item["score"] if gained or redundant else -1.0
            return (gained * 10) + (continuity * 0.5) + relevance - (redundant * 0.2)
        choice = max(remaining, key=utility)
        selected.append(choice)
        covered.update(a for a in wanted if coverage(choice["content"], a))
        remaining.remove(choice)
    return selected


@pytest.fixture(scope="module")
def real_trace():
    if not TRACE.exists():
        pytest.skip("trace persistido não disponível")
    return json.loads(TRACE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def candidates(real_trace, tmp_path_factory):
    stage = real_trace["retrieval"]["ranking_stages"]["rrf"]
    ids = [item["chunk_id"] for item in stage]
    snapshot = tmp_path_factory.mktemp("coverage") / "app.db"
    source = sqlite3.connect(f"file:{(ROOT / 'app.db').resolve()}?mode=ro", uri=True)
    target = sqlite3.connect(snapshot)
    source.backup(target)
    source.close(); target.close()
    db = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    rows = dict(db.execute("SELECT chunk_id, content FROM document_chunks WHERE chunk_id IN (%s)" % ",".join("?" * len(ids)), ids).fetchall())
    db.close()
    return [{**item, "content": rows.get(item["chunk_id"], "")} for item in stage]


@pytest.mark.parametrize("limit", [5, 10])
def test_compara_selecao_original_e_prototipo(real_trace, candidates, limit, capsys):
    original = [x["chunk_id"] for x in real_trace["retrieval"]["chunks"][:limit]]
    proposed = prototype_select(candidates, QUESTION, limit)
    print(json.dumps({"limit": limit, "original": original, "proposed": [x["chunk_id"] for x in proposed]}, ensure_ascii=False))
    assert len(proposed) == limit
    assert len({x["chunk_id"] for x in proposed}) == limit


@pytest.mark.parametrize("question", [
    "Quais são as competências dos fiscais técnicos e administrativos?",
    "Quais obrigações o edital impõe ao fornecedor e ao órgão?",
    "Quais cláusulas do contrato tratam de prazo, pagamento e penalidades?",
])
def test_fixtures_offline_sao_deterministicas(candidates, question):
    first = prototype_select(candidates, question, 5)
    second = prototype_select(candidates, question, 5)
    assert [x["chunk_id"] for x in first] == [x["chunk_id"] for x in second]
