"""
Testes do processamento da base normativa (app/modules/ingest/services.py).

Rodam sem internet e em segundos: usam um documento de exemplo pequeno,
guardado em tests/fixtures/lei_trecho.html, e não dependem de data/raw/.

    pytest tests/test_ingest_normativa.py -v
"""

import json
from pathlib import Path

import pytest
import yaml

from app.modules.ingest import services

FIXTURE = Path(__file__).parent / "fixtures" / "lei_trecho.html"


@pytest.fixture(scope="module")
def chunks():
    """Processa o documento de exemplo uma vez e reaproveita nos testes."""
    return services.gerar_chunks(FIXTURE)


# --- 1. Metadados obrigatórios -------------------------------------------

def test_todo_chunk_tem_metadado_obrigatorio(chunks):
    obrigatorios = {"source_file", "doc_type", "chunk_id", "sensitivity", "base_id"}
    for c in chunks:
        faltando = obrigatorios - set(c["metadata"])
        assert not faltando, f"{c['metadata'].get('chunk_id')} sem: {faltando}"


def test_sensibilidade_definida_na_ingestao(chunks):
    # A classificação é feita agora, e não na hora de responder
    assert all(c["metadata"]["sensitivity"] == "publico" for c in chunks)


def test_chunk_id_nao_se_repete(chunks):
    ids = [c["metadata"]["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))


# --- 2. O artigo não é cortado ao meio -----------------------------------

def _artigo(chunks, nome):
    return [c for c in chunks if c["metadata"].get("artigo") == nome]


def test_artigo_fica_com_seus_paragrafos_e_incisos(chunks):
    art1 = _artigo(chunks, "Art. 1")
    assert len(art1) == 1, "o Art. 1 deveria estar em um chunk só"

    texto = art1[0]["texto"]
    assert texto.startswith("Art. 1")
    for trecho in ["§ 1", "I - a administração direta", "II - as autarquias", "§ 2"]:
        assert trecho in texto, f"faltou no chunk do Art. 1: {trecho}"


def test_citacao_no_inicio_da_linha_nao_vira_artigo_novo(chunks):
    # No texto, "Art. 2º desta Lei" é uma citação dentro do § 2º do Art. 1º.
    # Ela não pode abrir um chunk novo, senão o artigo é partido ao meio.
    assert "Art. 2º desta Lei aos casos omissos" in _artigo(chunks, "Art. 1")[0]["texto"]


def test_artigos_na_ordem_e_sem_faltar(chunks):
    artigos = [c["metadata"]["artigo"] for c in chunks if c["metadata"].get("artigo", "").startswith("Art.")]
    assert artigos == ["Art. 1", "Art. 2", "Art. 3"]


# --- 3. Acentuação (HTML do Planalto é cp1252) ---------------------------

def test_acentos_saem_corretos(chunks):
    texto = " ".join(c["texto"] for c in chunks)
    assert "licitação" in texto
    assert "vigência" in texto
    assert "�" not in texto, "apareceu caractere quebrado: leia o HTML como cp1252"


# --- 4. Texto revogado (riscado) não entra no corpus ---------------------

def test_texto_riscado_nao_entra(chunks):
    texto = " ".join(c["texto"] for c in chunks)
    assert "REDACAO ANTIGA REVOGADA" not in texto


def test_versao_vigente_do_artigo_entra(chunks):
    # O mesmo Art. 2º tem uma redação riscada (antiga) e a vigente.
    # Só a vigente pode aparecer.
    assert "O prazo de vigência do contrato será de 12 (doze) meses" in _artigo(chunks, "Art. 2")[0]["texto"]


# --- 5. Política de sensibilidade: mascaramento na ingestão --------------

def test_cpf_e_telefone_sao_mascarados():
    texto = ("O pregoeiro, CPF 046.368.117-25, telefone (34) 99678-7950, "
             "conduzirá a sessão.")
    saida, achados = services.mascarar_dados_pessoais(texto)

    assert "046.368.117-25" not in saida
    assert "(34) 99678-7950" not in saida
    assert "[CPF MASCARADO]" in saida and "[TELEFONE MASCARADO]" in saida
    assert achados == {"cpf": 1, "telefone": 1}


def test_cnpj_e_valor_nao_sao_mascarados():
    # Público por lei: CNPJ de empresa, nome de fornecedor e valor do contrato
    texto = "Empresa X LTDA, CNPJ 12.345.678/0001-99, valor de R$ 500.000,00."
    saida, achados = services.mascarar_dados_pessoais(texto)

    assert saida == texto
    assert achados == {}


def test_chunk_com_dado_pessoal_fica_marcado_no_metadado(tmp_path):
    arquivo = tmp_path / "edital_teste.html"
    arquivo.write_text(
        "<html><body><p>EDITAL N° 1/2025</p>"
        "<p>1 - DO OBJETO: aquisição de material.</p>"
        "<p>2 - DO PREGOEIRO: CPF 046.368.117-25.</p></body></html>",
        encoding="cp1252")

    chunks = services.gerar_chunks(arquivo)
    com_dado = [c for c in chunks if "dados_pessoais" in c["metadata"]]

    assert com_dado, "o chunk com CPF deveria ser marcado no metadado"
    assert com_dado[0]["metadata"]["dados_pessoais"]["cpf"] == 1
    assert "046.368.117-25" not in " ".join(c["texto"] for c in chunks)

# --- 6. Vigência: o edital recebe a data oficial do PNCP -----------------

def test_edital_usa_a_data_de_publicacao_do_pncp(tmp_path):
    arquivo = tmp_path / "pncp_42498600000171_2025_921_arq1.html"
    arquivo.write_text("<html><body><p>EDITAL N° 169/2024</p>"
                       "<p>1 - DO OBJETO: aquisição de insumos.</p></body></html>",
                       encoding="cp1252")
    (tmp_path / "pncp_42498600000171_2025_921_arq1.meta.json").write_text(
        '{"data_publicacao": "2025-02-24", "titulo": "EDITAL 169.pdf"}',
        encoding="utf-8")

    chunks = services.gerar_chunks(arquivo)

    assert all(c["metadata"]["data_publicacao"] == "2025-02-24" for c in chunks)
    assert all(c["metadata"]["ano"] == 2025 for c in chunks)
    assert all(c["metadata"]["fonte_data"] == "pncp" for c in chunks)


def test_edital_sem_ficha_nao_quebra_a_ingestao(tmp_path):
    # Se a consulta ao PNCP falhar, o edital continua sendo processado:
    # fica sem data, e o metadado diz isso em vez de inventar uma.
    arquivo = tmp_path / "pncp_00000000000000_2025_1_arq1.html"
    arquivo.write_text("<html><body><p>EDITAL N° 1/2025</p>"
                       "<p>1 - DO OBJETO: aquisição de material.</p></body></html>",
                       encoding="cp1252")

    chunks = services.gerar_chunks(arquivo)

    assert chunks, "o edital deveria ser processado mesmo sem ficha"
    assert chunks[0]["metadata"]["data_publicacao"] is None
    assert chunks[0]["metadata"]["fonte_data"] == "indisponivel"

# --- 7. Overlap entre partes de um artigo longo --------------------------

def test_parte_seguinte_repete_o_final_da_anterior():
    # O texto precisa ter quebras de linha: _partir_se_longo corta por linha,
    # para não partir no meio de uma frase.
    texto = "Art. 1 " + ("palavra palavra palavra palavra\n" * 300)
    partes = services._partir_se_longo("Art. 1", texto)

    assert len(partes) > 1
    cauda = services._cauda(partes[0])
    assert cauda, "a cauda não pode ser vazia"
    assert cauda in partes[1], "a parte seguinte deveria repetir o final da anterior"


def test_cauda_comeca_em_palavra_inteira():
    # Cortar no meio de uma palavra deixaria o trecho recuperado sujo.
    cauda = services._cauda("a" * 500 + " palavra inteira no final do trecho")
    assert not cauda.startswith("a" * 10)
    assert cauda.endswith("trecho")


def test_nao_ha_overlap_entre_artigos_diferentes(chunks):
    # Artigo é unidade fechada: o Art. 2 não pode começar com texto do Art. 1.
    art2 = _artigo(chunks, "Art. 2")[0]["texto"]
    assert art2.startswith("Art. 2")


def test_chunk_diz_se_repete_texto_da_parte_anterior(chunks):
    # A indexação precisa saber quais trechos repetem texto, para não contar
    # o mesmo conteúdo duas vezes.
    assert all("parte" in c["metadata"] for c in chunks)
    assert all(c["metadata"]["tem_overlap"] == (c["metadata"]["parte"] > 1)
               for c in chunks)

# --- 8. Lixo de impressão não entra no corpus ----------------------------

def test_cabecalho_de_impressao_do_sei_nao_entra():
    # Editais salvos do SEI trazem, a cada página, a data/hora da impressão e
    # o link com o número da página. Isso ia para o índice como se fosse
    # conteúdo do edital.
    texto = ("2 - DO OBJETO\n"
             "21/02/2025, 08:48 SEI/ERJ - 90500290 - Edital de Licitação\n"
             "https://sei.rj.gov.br/sei/controlador.php?acao=x 10/60\n"
             "Aquisição de insumos laboratoriais.")

    limpo = services.limpar(texto)

    assert "SEI/ERJ" not in limpo
    assert "sei.rj.gov.br" not in limpo
    assert "DO OBJETO" in limpo
    assert "insumos laboratoriais" in limpo


def test_data_de_impressao_com_ano_de_dois_digitos_tambem_sai():
    # Os decretos vêm com "13/09/24" e os editais com "21/02/2025".
    limpo = services.limpar("13/09/24, 10:15 Leis Estaduais\nArt. 1 O texto vale.")

    assert "10:15" not in limpo
    assert "Art. 1 O texto vale." in limpo    

# --- aviso_sem_data: o risco de rodar a ingestão sem a coleta -------------

def _chunk(source_file, data_publicacao):
    return {"texto": "Art. 1 ...",
            "metadata": {"source_file": source_file,
                         "data_publicacao": data_publicacao}}


def test_corpus_com_data_nao_gera_aviso():
    chunks = [_chunk("lei-14133.html", "2021-04-01"),
              _chunk("decreto-48760.pdf", "2023-10-23")]

    assert services.aviso_sem_data(chunks) == []


def test_corpus_sem_data_avisa_e_conta_os_chunks():
    chunks = [_chunk("edital-93.pdf", None),
              _chunk("edital-93.pdf", None),
              _chunk("lei-14133.html", "2021-04-01")]

    aviso = services.aviso_sem_data(chunks)

    assert aviso, "deveria avisar quando falta data"
    assert "2 chunk(s) sem data" in aviso[0]
    assert "1 arquivo(s)" in aviso[0]
    assert "edital-93.pdf" in aviso[0]
    assert "lei-14133.html" not in aviso[0]


def test_aviso_lista_todos_os_arquivos_afetados():
    chunks = [_chunk("edital-93.pdf", None), _chunk("edital-96.pdf", None)]

    aviso = services.aviso_sem_data(chunks)

    assert "2 arquivo(s)" in aviso[0]
    assert "edital-93.pdf" in aviso[0]
    assert "edital-96.pdf" in aviso[0]


def test_aviso_ensina_o_comando_da_coleta_da_versao_certa():
    aviso = services.aviso_sem_data([_chunk("edital-93.pdf", None)], versao="v2")

    assert "fetch --versao v2" in aviso[-1]


# --- numero_do_edital: a regra principal e a reserva ----------------------

def test_titulo_curto_sai_pela_regra_principal():
    inicio = "Governo do Estado do Rio de Janeiro\nPREGÃO ELETRÔNICO N° 210/2024\n"

    assert services.numero_do_edital(inicio) == "PREGÃO ELETRÔNICO N° 210/2024"


def test_sigla_entre_o_tipo_e_o_numero_tambem_sai():
    inicio = "EDITAL DE LICITAÇÃO\nPREGÃO ELETRÔNICO SRP N° 096/2025\n"

    assert services.numero_do_edital(inicio) == "PREGÃO ELETRÔNICO SRP N° 096/2025"


def test_titulo_longo_sai_pela_regra_reserva():
    # "PARA REGISTRO DE PREÇOS" passa dos 20 caracteres da regra principal.
    inicio = ("EDITAL DE LICITAÇÃO\n"
              "PREGÃO ELETRÔNICO PARA REGISTRO DE PREÇOS N° 019/2025\n")

    assert services.numero_do_edital(inicio) == "PREGÃO ELETRÔNICO N° 019/2025"


def test_a_reserva_nao_atropela_quem_ja_saia_certo():
    # Com a regra principal ampliada, este viraria "EDITAL DE PREGÃO
    # ELETRÔNICO N° 210/2024". A reserva só roda quando a principal falha.
    inicio = "EDITAL DE PREGÃO ELETRÔNICO N° 210/2024\n"

    assert services.numero_do_edital(inicio) == "PREGÃO ELETRÔNICO N° 210/2024"


def test_documento_sem_numero_devolve_none():
    assert services.numero_do_edital("EDITAL DE LICITAÇÃO\nOBJETO\n") is None


# --- contrato entre o manifesto e o corpus processado ----------------------
# Estes dois testes leem os arquivos versionados no repositório: o manifesto e
# o JSONL com os 794 trechos. Rodam sem rede e sem banco.

RAIZ = Path(__file__).resolve().parents[1]
JSONL_NORMATIVO = RAIZ / "data" / "processed" / "v1_editais_normativo.jsonl"
OBRIGATORIOS = {"source_file", "doc_type", "chunk_id", "sensitivity", "base_id"}


def _trechos_processados():
    if not JSONL_NORMATIVO.is_file():
        pytest.skip("JSONL processado não está no repositório")
    with JSONL_NORMATIVO.open(encoding="utf-8") as arquivo:
        return [json.loads(linha) for linha in arquivo if linha.strip()]


def test_todo_trecho_tem_os_metadados_obrigatorios():
    for trecho in _trechos_processados():
        faltando = OBRIGATORIOS - set(trecho["metadata"])
        assert not faltando, f"{trecho['metadata'].get('chunk_id')} sem: {faltando}"


def test_todo_metadado_declarado_no_manifesto_existe_nos_trechos():
    # O manifesto promete certos metadados para a base; se um deles não existir
    # em nenhum trecho, a promessa é falsa (foi o caso de "objeto").
    manifesto = yaml.safe_load((RAIZ / "corpus_manifest.yaml").read_text(encoding="utf-8"))
    base = next(b for b in manifesto["bases"] if b["base_id"] == services.BASE_ID)
    declarados = set(base["metadados_extras"])
    existentes = {chave for t in _trechos_processados() for chave in t["metadata"]}

    assert declarados - existentes == set(), (
        f"declarados no manifesto e ausentes em todos os trechos: {declarados - existentes}"
    )
