"""
Testes da persistência da base normativa (app/modules/normative/persistencia.py).

Rodam sem internet e sem tocar no app.db: cada teste cria um banco SQLite em
memória e um JSONL pequeno numa pasta temporária (tmp_path).

    pytest tests/test_normative_persistencia.py -v
"""

import json
import os
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
    from app.core.database import create_sqlite_engine, init_db

from app.modules.corpus.model import CorpusVersion
from app.modules.documents.model import Document, DocumentChunk
from app.modules.normative import persistencia


# --- apoio -----------------------------------------------------------------

def trecho(chunk_id, source_file, texto="Art. 1º Esta lei entra em vigor.", **extra):
    metadata = {
        "source_file": source_file,
        "base_id": "editais_normativo",
        "sensitivity": "publico",
        "doc_type": "lei",
        "chunk_id": chunk_id,
        "data_publicacao": "2021-04-01",
        "ano": 2021,
        "parte": 1,
        "tem_overlap": False,
    }
    metadata.update(extra)
    return {"texto": texto, "metadata": metadata}


def escrever_jsonl(pasta, nome, itens):
    caminho = pasta / nome
    caminho.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in itens) + "\n",
        encoding="utf-8",
    )
    return caminho


@pytest.fixture
def db():
    engine = create_sqlite_engine("sqlite:///:memory:")
    init_db(bind=engine)
    with Session(engine) as sessao:
        yield sessao


@pytest.fixture
def versao(db):
    return persistencia.obter_ou_criar_versao(db, "v1")


# --- caminhos e nomes ------------------------------------------------------

def test_arquivo_da_versao():
    caminho = persistencia.arquivo_da_versao("v1")
    assert caminho.name == "v1_editais_normativo.jsonl"


def test_versao_do_normativo_nao_colide_com_a_do_pncp():
    assert persistencia.nome_da_versao("v1") == "normativo-v1"
    assert persistencia.nome_da_versao("v2") == "normativo-v2"


# --- partes_do_chunk_id: a citação sem coluna no banco ---------------------

def test_deriva_norma_artigo_e_parte_do_chunk_id():
    partes = persistencia.partes_do_chunk_id(
        "editais_normativo:decreto-48760-2023-pca:art-86:p2"
    )
    assert partes == {
        "base_id": "editais_normativo",
        "documento": "decreto-48760-2023-pca",
        "trecho": "art-86",
        "parte": 2,
    }


def test_chunk_id_fora_do_formato_devolve_none():
    assert persistencia.partes_do_chunk_id("editais_normativo:lei:art-1") is None
    assert persistencia.partes_do_chunk_id("a:b:c:parte2") is None


# --- linha_valida: os 5 metadados obrigatórios -----------------------------

def test_linha_sem_metadado_obrigatorio_e_invalida():
    item = trecho("editais_normativo:lei:art-1:p1", "lei.pdf")
    del item["metadata"]["sensitivity"]
    assert persistencia.linha_valida(item) is False


def test_linha_sem_texto_e_invalida():
    item = trecho("editais_normativo:lei:art-1:p1", "lei.pdf", texto="   ")
    assert persistencia.linha_valida(item) is False


def test_linha_completa_e_valida():
    assert persistencia.linha_valida(trecho("editais_normativo:lei:art-1:p1", "lei.pdf"))


# --- versão do corpus ------------------------------------------------------

def test_versao_e_criada_uma_vez_so(db):
    primeira = persistencia.obter_ou_criar_versao(db, "v1")
    segunda = persistencia.obter_ou_criar_versao(db, "v1")
    assert primeira.id == segunda.id
    assert len(db.scalars(select(CorpusVersion)).all()) == 1


# --- gravação --------------------------------------------------------------

def test_grava_documento_e_trecho(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html",
               numero_norma="Lei 14.133"),
    ])
    resumo = persistencia.persistir_jsonl(db, versao.id, arquivo)

    assert resumo["chunks_criados"] == 1
    assert resumo["documentos_criados"] == 1

    chunk = db.scalar(select(DocumentChunk))
    assert chunk.base_id == "editais_normativo"
    assert chunk.doc_type == "lei"
    assert chunk.sensitivity == "publico"
    assert chunk.content.startswith("Art. 1º")
    assert chunk.data_publicacao.isoformat() == "2021-04-01"


def test_numero_da_norma_vai_para_o_titulo(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:decreto-48760:art-1:p1", "decreto-48760.pdf",
               doc_type="decreto", numero_norma="Decreto RJ 48.760"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)
    assert db.scalar(select(Document)).title == "Decreto RJ 48.760"


def test_numero_do_edital_tambem_vai_para_o_titulo(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:edital-48:clausula-1:p1", "edital-48.pdf",
               doc_type="edital", numero_edital="Edital 48/2025",
               orgao="Prefeitura de Bom Jardim"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)
    documento = db.scalar(select(Document))
    assert documento.title == "Edital 48/2025"
    assert db.scalar(select(DocumentChunk)).orgao == "Prefeitura de Bom Jardim"


def test_vigencia_fica_vazia(db, versao, tmp_path):
    # vigencia significa fim de vigência; a base normativa tem data de
    # publicação, que já vai na coluna própria.
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)
    assert db.scalar(select(DocumentChunk)).vigencia is None


def test_trechos_do_mesmo_arquivo_dividem_um_documento(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
        trecho("editais_normativo:lei-14133:art-2:p1", "lei-14133.html"),
        trecho("editais_normativo:decreto-48760:art-1:p1", "decreto-48760.pdf"),
    ])
    resumo = persistencia.persistir_jsonl(db, versao.id, arquivo)
    assert resumo["chunks_criados"] == 3
    assert resumo["documentos_criados"] == 2


def test_rodar_de_novo_nao_duplica(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
        trecho("editais_normativo:lei-14133:art-2:p1", "lei-14133.html"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)
    segunda = persistencia.persistir_jsonl(db, versao.id, arquivo)

    assert segunda["chunks_criados"] == 0
    assert segunda["chunks_ja_existentes"] == 2
    assert len(db.scalars(select(DocumentChunk)).all()) == 2


def test_continua_de_onde_parou(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)

    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
        trecho("editais_normativo:lei-14133:art-2:p1", "lei-14133.html"),
    ])
    segunda = persistencia.persistir_jsonl(db, versao.id, arquivo)

    assert segunda["chunks_criados"] == 1
    assert segunda["chunks_ja_existentes"] == 1
    assert len(db.scalars(select(DocumentChunk)).all()) == 2


def test_linha_quebrada_nao_derruba_o_arquivo(db, versao, tmp_path):
    caminho = tmp_path / "v1_editais_normativo.jsonl"
    caminho.write_text(
        json.dumps(trecho("editais_normativo:lei:art-1:p1", "lei.html")) + "\n"
        + "{ isso nao e json\n"
        + json.dumps(trecho("editais_normativo:lei:art-2:p1", "lei.html")) + "\n",
        encoding="utf-8",
    )
    resumo = persistencia.persistir_jsonl(db, versao.id, caminho)
    assert resumo["chunks_criados"] == 2
    assert resumo["invalidas"] == 1


def test_data_invalida_nao_derruba_o_trecho(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei:art-1:p1", "lei.html",
               data_publicacao="indisponivel"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)
    assert db.scalar(select(DocumentChunk)).data_publicacao is None


def test_arquivo_que_nao_existe_avisa(db, versao, tmp_path):
    with pytest.raises(FileNotFoundError):
        persistencia.persistir_jsonl(db, versao.id, tmp_path / "nao_existe.jsonl")


def test_commit_em_lotes_grava_tudo(db, versao, tmp_path):
    itens = [
        trecho(f"editais_normativo:lei:art-{n}:p1", "lei.html")
        for n in range(1, 11)
    ]
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", itens)
    resumo = persistencia.persistir_jsonl(db, versao.id, arquivo, tamanho_commit=3)
    assert resumo["chunks_criados"] == 10
    assert len(db.scalars(select(DocumentChunk)).all()) == 10


def test_tamanho_commit_invalido_avisa(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei:art-1:p1", "lei.html"),
    ])
    with pytest.raises(ValueError):
        persistencia.persistir_jsonl(db, versao.id, arquivo, tamanho_commit=0)


# --- título que chega depois: a extração melhorou -------------------------

def test_titulo_vazio_e_preenchido_numa_passada_seguinte(db, versao, tmp_path):
    # Primeiro carregamento: a extração ainda não achava o número do edital.
    arquivo = escrever_jsonl(tmp_path, "sem_titulo.jsonl", [
        trecho("editais_normativo:edital-19:clausula-1:p1", "edital-19.pdf",
               doc_type="edital"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)
    assert db.scalar(select(Document)).title is None

    # Segundo carregamento, com o JSONL reprocessado: o número apareceu.
    arquivo = escrever_jsonl(tmp_path, "com_titulo.jsonl", [
        trecho("editais_normativo:edital-19:clausula-1:p1", "edital-19.pdf",
               doc_type="edital", numero_edital="PREGÃO ELETRÔNICO N° 019/2025"),
    ])
    resumo = persistencia.persistir_jsonl(db, versao.id, arquivo)

    assert resumo["titulos_preenchidos"] == 1
    assert resumo["chunks_criados"] == 0, "nao pode duplicar trecho"
    assert db.scalar(select(Document)).title == "PREGÃO ELETRÔNICO N° 019/2025"


def test_titulo_ja_gravado_nao_e_sobrescrito(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html",
               numero_norma="Lei 14.133"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)

    documento = db.scalar(select(Document))
    documento.title = "Lei 14.133 (corrigido à mão)"
    db.commit()

    resumo = persistencia.persistir_jsonl(db, versao.id, arquivo)

    assert resumo["titulos_preenchidos"] == 0
    assert db.scalar(select(Document)).title == "Lei 14.133 (corrigido à mão)"


# --- chave do documento: a mesma convenção do carregador do Luciano --------

def test_chave_do_documento_leva_o_prefixo_da_base():
    metadata = {"base_id": "editais_normativo", "source_file": "planalto_l14133.html"}

    assert persistencia.chave_do_documento(metadata) == (
        "editais_normativo|planalto_l14133.html"
    )


def test_documento_novo_e_gravado_com_a_chave_com_prefixo(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)

    assert db.scalar(select(Document)).source_file == "editais_normativo|lei-14133.html"


# --- URL de origem, vinda do inventário da coleta --------------------------

def escrever_inventario(pasta, registros):
    caminho = pasta / "v1_editais_normativo.json"
    caminho.write_text(json.dumps(registros), encoding="utf-8")
    return caminho


def test_inventario_liga_o_nome_do_arquivo_a_url(tmp_path):
    inventario = escrever_inventario(tmp_path, [
        {"path": "data/raw/v1/editais_normativo/lei-14133.html",
         "origem": "https://www.planalto.gov.br/lei14133"},
    ])

    assert persistencia.urls_do_inventario(inventario) == {
        "lei-14133.html": "https://www.planalto.gov.br/lei14133",
    }


def test_inventario_que_nao_existe_devolve_vazio(tmp_path):
    assert persistencia.urls_do_inventario(tmp_path / "nao_existe.json") == {}
    assert persistencia.urls_do_inventario(None) == {}


def test_inventario_quebrado_nao_derruba_a_carga(tmp_path):
    quebrado = tmp_path / "v1_editais_normativo.json"
    quebrado.write_text("{ isso nao e json", encoding="utf-8")

    assert persistencia.urls_do_inventario(quebrado) == {}


def test_documento_novo_recebe_a_url_do_inventario(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
    ])
    persistencia.persistir_jsonl(
        db, versao.id, arquivo,
        urls_por_arquivo={"lei-14133.html": "https://www.planalto.gov.br/lei14133"},
    )

    assert db.scalar(select(Document)).source_url == "https://www.planalto.gov.br/lei14133"


def test_url_que_chega_depois_e_preenchida_sem_duplicar(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
    ])
    persistencia.persistir_jsonl(db, versao.id, arquivo)
    assert db.scalar(select(Document)).source_url is None

    resumo = persistencia.persistir_jsonl(
        db, versao.id, arquivo,
        urls_por_arquivo={"lei-14133.html": "https://www.planalto.gov.br/lei14133"},
    )

    assert resumo["urls_preenchidas"] == 1
    assert resumo["chunks_criados"] == 0
    assert db.scalar(select(Document)).source_url == "https://www.planalto.gov.br/lei14133"


def test_url_ja_gravada_nao_e_sobrescrita(db, versao, tmp_path):
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
    ])
    persistencia.persistir_jsonl(
        db, versao.id, arquivo,
        urls_por_arquivo={"lei-14133.html": "https://original.example/lei"},
    )

    resumo = persistencia.persistir_jsonl(
        db, versao.id, arquivo,
        urls_por_arquivo={"lei-14133.html": "https://outra.example/lei"},
    )

    assert resumo["urls_preenchidas"] == 0
    assert db.scalar(select(Document)).source_url == "https://original.example/lei"


# --- bancos carregados antes do prefixo ------------------------------------

def documento_com_chave_antiga(db, versao, source_file):
    """Reproduz o que o carregador gravava antes de adotar o prefixo."""
    documento = Document(
        corpus_version_id=versao.id, source_file=source_file,
        base_id="editais_normativo", doc_type="lei", sensitivity="publico",
        title="Lei 14.133",
    )
    db.add(documento)
    db.flush()
    db.add(DocumentChunk(
        document_id=documento.id, chunk_id="editais_normativo:lei-14133:art-1:p1",
        content="Art. 1º Esta lei entra em vigor.", doc_type="lei",
        base_id="editais_normativo", sensitivity="publico",
    ))
    db.commit()
    return documento


def test_chave_antiga_e_renomeada_e_o_trecho_continua_ligado(db, versao):
    antigo = documento_com_chave_antiga(db, versao, "lei-14133.html")

    resultado = persistencia.migrar_chaves_antigas(db, versao.id)
    db.commit()

    assert resultado == {"migradas": 1, "em_conflito": 0}
    db.refresh(antigo)
    assert antigo.source_file == "editais_normativo|lei-14133.html"
    assert db.scalar(select(DocumentChunk)).document_id == antigo.id


def test_banco_antigo_recarregado_nao_cria_documento_novo(db, versao, tmp_path):
    documento_com_chave_antiga(db, versao, "lei-14133.html")
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html"),
    ])

    resumo = persistencia.persistir_jsonl(
        db, versao.id, arquivo,
        urls_por_arquivo={"lei-14133.html": "https://www.planalto.gov.br/lei14133"},
    )

    assert resumo["chaves_migradas"] == 1
    assert resumo["documentos_criados"] == 0
    assert len(db.scalars(select(Document)).all()) == 1
    assert db.scalar(select(Document)).source_url == "https://www.planalto.gov.br/lei14133"


def test_migracao_so_mexe_na_base_normativa(db, versao):
    pncp = Document(
        corpus_version_id=versao.id, source_file="pncp_rj_estruturada|contrato|1|1",
        base_id="pncp_rj_estruturada", doc_type="contrato", sensitivity="publico",
    )
    db.add(pncp)
    db.commit()

    resultado = persistencia.migrar_chaves_antigas(db, versao.id)

    assert resultado["migradas"] == 0
    assert pncp.source_file == "pncp_rj_estruturada|contrato|1|1"


def test_conflito_de_chave_e_contado_e_nada_e_apagado(db, versao):
    documento_com_chave_antiga(db, versao, "lei-14133.html")
    db.add(Document(
        corpus_version_id=versao.id, source_file="editais_normativo|lei-14133.html",
        base_id="editais_normativo", doc_type="lei", sensitivity="publico",
    ))
    db.commit()

    resultado = persistencia.migrar_chaves_antigas(db, versao.id)

    assert resultado == {"migradas": 0, "em_conflito": 1}
    assert len(db.scalars(select(Document)).all()) == 2


def test_migrar_duas_vezes_nao_faz_nada_na_segunda(db, versao):
    documento_com_chave_antiga(db, versao, "lei-14133.html")
    persistencia.migrar_chaves_antigas(db, versao.id)
    db.commit()

    assert persistencia.migrar_chaves_antigas(db, versao.id) == {
        "migradas": 0, "em_conflito": 0,
    }


# --- compatibilidade com o carregador do Luciano ---------------------------

def _carregador_do_luciano():
    # Esse módulo depende de pacotes que a base normativa não usa; sem eles o
    # teste é pulado em vez de falhar.
    pytest.importorskip("langchain_text_splitters")
    pytest.importorskip("rich")
    from app.modules.documents.services import persistir_chunks_jsonl_normativos
    return persistir_chunks_jsonl_normativos


def _documentos_sem_trecho(db):
    return db.execute(text(
        "SELECT count(*) FROM documents d LEFT JOIN document_chunks c "
        "ON c.document_id = d.id WHERE c.id IS NULL"
    )).scalar()


@pytest.mark.parametrize("primeiro", ["nosso", "dele"])
def test_os_dois_carregadores_em_qualquer_ordem_nao_deixam_documento_orfao(
    db, versao, tmp_path, primeiro,
):
    dele = _carregador_do_luciano()
    arquivo = escrever_jsonl(tmp_path, "v1_editais_normativo.jsonl", [
        trecho("editais_normativo:lei-14133:art-1:p1", "lei-14133.html",
               numero_norma="Lei 14.133"),
        trecho("editais_normativo:lei-14133:art-2:p1", "lei-14133.html",
               numero_norma="Lei 14.133"),
        trecho("editais_normativo:decreto-48760:art-1:p1", "decreto-48760.pdf",
               doc_type="decreto", numero_norma="Decreto RJ 48.760"),
    ])
    urls = {"lei-14133.html": "https://www.planalto.gov.br/lei14133",
            "decreto-48760.pdf": "https://www.rj.gov.br/decreto48760"}

    def rodar_nosso():
        persistencia.persistir_jsonl(db, versao.id, arquivo, urls_por_arquivo=urls)

    def rodar_dele():
        dele(db, versao.id, arquivo, urls_por_arquivo=urls)

    ordem = (rodar_nosso, rodar_dele) if primeiro == "nosso" else (rodar_dele, rodar_nosso)
    for rodar in ordem:
        rodar()

    assert len(db.scalars(select(Document)).all()) == 2
    assert len(db.scalars(select(DocumentChunk)).all()) == 3
    assert _documentos_sem_trecho(db) == 0
