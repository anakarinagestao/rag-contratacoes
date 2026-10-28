"""
Processamento da base normativa (editais_normativo).

Lê os arquivos coletados em data/raw/<versao>/editais_normativo/, transforma
cada documento em chunks (trechos) com metadados e grava o resultado em
data/processed/<versao>_editais_normativo.jsonl, pronto para a indexação.

- Lei e decretos: 1 artigo = 1 chunk (Art. + seus §, incisos e alíneas juntos).
  Artigo muito longo é dividido em partes, repetindo "Art. X" no começo.
- Editais: divididos por cláusula numerada de primeiro nível (1., 2., 3. ...).
- Todo chunk tem os 5 metadados obrigatórios do enunciado:
  source_file, doc_type, chunk_id, sensitivity, base_id.

Uso:
    python -m app.modules.ingest.services --versao v1
"""

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

from app.core import logging as obs

BASE_ID = "editais_normativo"
SENSITIVITY = "publico"   # definido na ingestão, como o enunciado exige
MAX_CARACTERES = 4000     # acima disso, o artigo/cláusula é dividido em partes
OVERLAP_CARACTERES = 200  # trecho do fim da parte anterior repetido na seguinte
STAGE = obs.STAGE_INGESTAO

# ---------------------------------------------------------------------------
# 1. Extração de texto
# ---------------------------------------------------------------------------

class _TextoDoHTML(HTMLParser):
    """
    Tira as tags do HTML do Planalto e IGNORA o texto riscado (<strike>).
    No texto compilado do Planalto, o que está riscado é a redação antiga,
    revogada ou alterada. Se entrasse no corpus, o sistema responderia com
    regras que não valem mais.
    """
    BLOCOS = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__()
        self.partes = []
        self.dentro_de_strike = 0
        self.dentro_de_script = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("strike", "s", "del"):
            self.dentro_de_strike += 1
        elif tag in ("script", "style"):
            self.dentro_de_script += 1
        elif tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("strike", "s", "del") and self.dentro_de_strike:
            self.dentro_de_strike -= 1
        elif tag in ("script", "style") and self.dentro_de_script:
            self.dentro_de_script -= 1
        elif tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self.dentro_de_strike and not self.dentro_de_script:
            # No código do Planalto, as linhas quebram no meio das frases.
            # Aqui junta tudo com espaço; quebra de linha só vem das tags de bloco.
            self.partes.append(re.sub(r"\s+", " ", data))


def extrair_texto(caminho: Path) -> str:
    """Devolve o texto do documento, seja PDF ou HTML."""
    if caminho.suffix.lower() == ".pdf":
        leitor = PdfReader(str(caminho))
        # extraction_mode="layout" respeita a posição de cada texto na página.
        # Sem ele, nos decretos salvos do navegador o rótulo "Art. 3º" (que fica
        # numa coluna à esquerda) sai fora de ordem, longe do texto do artigo.
        return "\n".join(pagina.extract_text(extraction_mode="layout") or "" for pagina in leitor.pages)

    # HTML do Planalto: não declara a codificação e está em Windows-1252.
    # Lido como UTF-8, os acentos saem quebrados ("Licita  es").
    bruto = caminho.read_bytes().decode("cp1252", errors="replace")
    parser = _TextoDoHTML()
    parser.feed(bruto)
    return "".join(parser.partes)


# Cabeçalho e rodapé que o navegador coloca em cada página quando o documento é
# salvo com Ctrl+P: nome do site ou do sistema, data/hora da impressão, o link e
# o número da página. Se ficarem, aparecem no meio dos artigos e das cláusulas a
# cada troca de página, e vão para o índice como se fossem conteúdo.
LIXO_DE_IMPRESSAO = re.compile(
    r"^(Leis Estaduais|Rio de Janeiro"
    # Data e hora da impressão. O ano vem com 2 dígitos nos decretos
    # ("13/09/24") e com 4 nos editais do SEI ("21/02/2025").
    r"|\d{2}/\d{2}/\d{2,4}, \d{2}:\d{2} .*"
    # Rodapé com o link e o número da página ("... 10/60")
    r"|https?://(leisestaduais\.com\.br|sei\.rj\.gov\.br)/.*)$",
    re.M,
)


def limpar(texto: str) -> str:
    """Padroniza espaços e quebras de linha e tira o lixo de impressão."""
    texto = texto.replace("\xa0", " ")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"^ +| +$", "", texto, flags=re.M)
    texto = LIXO_DE_IMPRESSAO.sub("", texto)
    texto = re.sub(r"\n\s*\n+", "\n", texto)
    return texto.strip()


# ---------------------------------------------------------------------------
# 1.1 Política de sensibilidade: mascaramento na ingestão
# ---------------------------------------------------------------------------

# O manifesto e o ARQUITETURA.md definem: CPF de pessoa física e telefone
# pessoal são MASCARADOS. CNPJ, nome de empresa e valores são públicos por lei
# e continuam no texto. O mascaramento é feito aqui, na ingestão, e não na hora
# de responder, porque filtrar por metadado é barato e determinístico.
CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
TELEFONE = re.compile(r"\(\d{2}\)\s?\d{4,5}-\d{4}")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


def mascarar_dados_pessoais(texto: str):
    """
    Devolve (texto_mascarado, achados). `achados` conta o que foi encontrado,
    para registrar no metadado do chunk e no relatório da ingestão.
    """
    achados = {
        "cpf": len(CPF.findall(texto)),
        "telefone": len(TELEFONE.findall(texto)),
        "email": len(EMAIL.findall(texto)),
    }
    texto = CPF.sub("[CPF MASCARADO]", texto)
    texto = TELEFONE.sub("[TELEFONE MASCARADO]", texto)
    return texto, {k: v for k, v in achados.items() if v}


# ---------------------------------------------------------------------------
# 2. Metadados do documento
# ---------------------------------------------------------------------------

MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
         "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
         "novembro": 11, "dezembro": 12}


def _data_por_extenso(dia, mes, ano):
    """'13', 'dezembro', '2023' -> '2023-12-13'. Mês desconhecido -> None."""
    numero_mes = MESES.get(mes.lower())
    if not numero_mes:
        return None
    return f"{int(ano):04d}-{numero_mes:02d}-{int(dia):02d}"

def ficha_do_documento(caminho: Path) -> dict:
    """
    Lê o <arquivo>.meta.json gravado na coleta. É de onde vem a data oficial de
    publicação dos editais, que o texto do PDF não informa de forma confiável.

    Documento sem ficha (a Lei 14.133 e os decretos, que não vêm do PNCP, ou um
    edital cuja consulta falhou) devolve {} — quem chama trata a ausência, e a
    ingestão continua rodando offline, sem tocar na rede.
    """
    ficha = caminho.parent / f"{caminho.stem}.meta.json"
    if ficha.exists():
        return json.loads(ficha.read_text(encoding="utf-8"))
    return {}


def numero_do_edital(inicio: str) -> str | None:
    """
    Tira o número do edital da linha de título.

    A regra principal aceita até 20 caracteres entre o tipo e o "Nº", o que
    cobre "PREGÃO ELETRÔNICO SRP N° 096/2025". Quando o título é mais longo —
    "PREGÃO ELETRÔNICO PARA REGISTRO DE PREÇOS N° 019/2025" —, ela não acha
    nada, e aí entra a regra reserva, que aceita um intervalo maior mas só
    guarda o tipo e o número. Ampliar a regra principal não serve: ela
    passaria a casar "EDITAL DE PREGÃO ELETRÔNICO N° 210/2024" e os títulos
    que já saem certos ficariam compridos.
    """
    achado = re.search(
        r"(PREG[ÃA]O ELETR[ÔO]NICO[^\n]{0,20}?N[º°o]\.?\s*[\d\./]+"
        r"|EDITAL[^\n]{0,20}?N[º°o]\.?\s*[\d\./]+)",
        inicio, re.I)
    if achado:
        return re.sub(r"\s+", " ", achado.group(1)).strip()

    reserva = re.search(
        r"(PREG[ÃA]O ELETR[ÔO]NICO|EDITAL)[^\n]{0,60}?(N[º°o]\.?\s*\d{1,4}/\d{4})",
        inicio, re.I)
    if reserva:
        tipo = re.sub(r"\s+", " ", reserva.group(1)).strip()
        numero = re.sub(r"\s+", " ", reserva.group(2)).strip()
        return f"{tipo} {numero}"
    return None


def metadados_do_documento(caminho: Path, texto: str) -> dict:
    """
    Descobre, pelo próprio texto, que documento é: lei, decreto ou edital,
    com número e data. Olha só o começo do documento.
    """
    inicio = texto[:3000]
    meta = {"source_file": caminho.name, "base_id": BASE_ID, "sensitivity": SENSITIVITY}

    # O tipo vem da LINHA DE TÍTULO (começo de linha). Decretos e editais citam a
    # "Lei nº 14.133" logo no início; se procurássemos em qualquer lugar do
    # texto, todo documento seria classificado como lei.
    # O "DE" antes do mês é opcional: o título oficial do Decreto 48.843 saiu
    # como "13 DEZEMBRO DE 2023", sem o "DE".
    data = r",?\s+DE\s+(\d{1,2})[º°o]?\s+(?:DE\s+)?(\w+)\s+DE\s+(\d{4})"
    lei = re.search(r"^LEI N[º°o]\s*([\d\.]+)" + data, inicio, re.I | re.M)
    decreto = re.search(r"^DECRETO N[º°o]\s*([\d\.]+)" + data, inicio, re.I | re.M)
    menciona_edital = re.search(r"EDITAL|PREG[ÃA]O", inicio, re.I)

    if decreto and (not menciona_edital or decreto.start() < menciona_edital.start()):
        tipo = "decreto"
    elif lei and (not menciona_edital or lei.start() < menciona_edital.start()):
        tipo = "lei"
    else:
        tipo = "edital"

    if tipo == "lei":
        meta.update(doc_type="lei", numero_norma=f"Lei {lei.group(1)}",
                    data_publicacao=_data_por_extenso(*lei.groups()[1:]))
    elif tipo == "decreto":
        meta.update(doc_type="decreto", numero_norma=f"Decreto RJ {decreto.group(1)}",
                    data_publicacao=_data_por_extenso(*decreto.groups()[1:]))
        # Vigência: guarda qual decreto este altera, para não usar a regra antiga
        altera = re.search(r"ALTERA O DECRETO N[º°o]\s*([\d\.]+)", inicio, re.I)
        if altera:
            meta["altera"] = f"Decreto RJ {altera.group(1)}"
    else:
        meta.update(doc_type="edital", numero_edital=numero_do_edital(inicio))
        orgao = re.search(r"(Governo do Estado do Rio de Janeiro\s+[^\n]+|Prefeitura Municipal de [^\n]+)", inicio)
        meta["orgao"] = orgao.group(1).strip() if orgao else None
        # Vigência do edital: data oficial do PNCP, gravada na coleta em
        # <arquivo>.meta.json. O texto do PDF não traz a data de publicação de
        # forma confiável — cita dezenas de datas de outras normas, e qualquer
        # uma delas seria pega por engano.
        ficha = ficha_do_documento(caminho)
        meta["data_publicacao"] = ficha.get("data_publicacao")
        meta["fonte_data"] = "pncp" if ficha.get("data_publicacao") else "indisponivel"

    ano = re.search(r"(20\d{2})", meta.get("data_publicacao") or caminho.name)
    meta["ano"] = int(ano.group(1)) if ano else None
    return meta


# ---------------------------------------------------------------------------
# 3. Divisão em chunks
# ---------------------------------------------------------------------------

# "Art. 1º O ...", "Art. 10. A ...", "Art. 75-A. ..." no começo da linha.
# Só conta como início de artigo se depois vier letra MAIÚSCULA ou "(VETADO)".
# Assim, uma citação que caiu no começo da linha ("Art. 9º da Lei nº 14.133")
# não é confundida com um artigo novo.
INICIO_ARTIGO = re.compile(
    # O espaço depois do ponto é opcional: nos decretos há "Art. 44.Fixada ...".
    r"^\s*Art\.\s*(\d+(?:-[A-Z])?)\s*[º°o]?\s*[\.\-–]?\s*(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ\(])", re.M)
# Cláusula de primeiro nível do edital: "1.", "2 -", "3 –" seguido de texto em maiúsculas
INICIO_CLAUSULA = re.compile(r"^\s*(\d{1,2})\s*[\.\-–]\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3})", re.M)


def _numero(marcador):
    """'75-A' -> 75"""
    return int(re.match(r"\d+", marcador).group())


def _manter_sequencia(marcas, salto_maximo=5):
    """
    Artigos de uma norma vêm em ordem (1, 2, 3 ... 75, 75-A, 76 ...).
    Um "Art." fora da sequência é citação ou texto inserido em outra lei
    (ex.: a Lei 14.133 inclui os arts. 337-E a 337-P no Código Penal) e
    continua dentro do artigo atual, sem abrir um chunk novo.
    """
    aceitas, ultimo = [], 0
    for m in marcas:
        n = _numero(m.group(1))
        if ultimo <= n <= ultimo + salto_maximo:
            aceitas.append(m)
            ultimo = n
    return aceitas


def _dividir_por_marcador(texto, padrao, sequencial=False):
    """Corta o texto em cada marcador. O que vem antes do 1º vira 'preambulo'."""
    marcas = list(padrao.finditer(texto))
    if sequencial:
        marcas = _manter_sequencia(marcas)
    if not marcas:
        return [("documento", texto)]
    pedacos = []
    if texto[:marcas[0].start()].strip():
        pedacos.append(("preambulo", texto[:marcas[0].start()]))
    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        pedacos.append((m.group(1), texto[m.start():fim]))
    return pedacos


def _cauda(texto: str) -> str:
    """
    Últimos OVERLAP_CARACTERES do trecho, começando em palavra inteira.

    É o overlap declarado no manifesto. Ele existe SÓ entre as partes de um
    mesmo artigo ou cláusula longa: sem ele, um inciso que caiu na parte
    seguinte perde a frase que o introduzia. Entre artigos diferentes não há
    overlap, porque o artigo é uma unidade fechada e misturar dois faria a
    busca devolver um artigo com o texto do outro.
    """
    if OVERLAP_CARACTERES <= 0 or len(texto) <= OVERLAP_CARACTERES:
        return texto.strip()
    pedaco = texto[-OVERLAP_CARACTERES:]
    return pedaco[pedaco.find(" ") + 1:].strip()


def _partir_se_longo(rotulo, texto):
    """
    Artigo/cláusula grande demais é dividido por parágrafo (linha), sem cortar
    no meio de uma frase. Cada parte recebe o rótulo no começo, para o trecho
    continuar fazendo sentido sozinho (ex.: "Art. 75 (parte 2)").
    """
    if len(texto) <= MAX_CARACTERES:
        return [texto]
    partes, atual = [], ""
    for linha in texto.split("\n"):
        if len(atual) + len(linha) > MAX_CARACTERES and atual:
            partes.append(atual)
            atual = f"[{rotulo} (continuação)]\n{_cauda(atual)}\n"
        atual += linha + "\n"
    partes.append(atual)
    return partes


def gerar_chunks(caminho: Path) -> list[dict]:
    """Um documento -> lista de chunks {texto, metadata}."""
    texto = limpar(extrair_texto(caminho))
    meta_doc = metadados_do_documento(caminho, texto)

    if meta_doc["doc_type"] in ("lei", "decreto"):
        pedacos = _dividir_por_marcador(texto, INICIO_ARTIGO, sequencial=True)
        rotulo = "Art. {}"
    else:
        pedacos = _dividir_por_marcador(texto, INICIO_CLAUSULA, sequencial=True)
        rotulo = "Cláusula {}"

    chunks = []
    vistos = {}
    for marcador, trecho in pedacos:
        nome = "Preâmbulo" if marcador == "preambulo" else rotulo.format(marcador)
        # Evita chunk_id repetido se o mesmo número aparecer duas vezes
        vistos[marcador] = vistos.get(marcador, 0) + 1
        sufixo = "" if vistos[marcador] == 1 else f"_{vistos[marcador]}"
        for n, parte in enumerate(_partir_se_longo(nome, trecho.strip()), start=1):
            chunk_id = f"{BASE_ID}:{caminho.stem}:{marcador}{sufixo}:p{n}"
            # Política de sensibilidade aplicada na ingestão
            texto_chunk, achados = mascarar_dados_pessoais(parte.strip())
            metadata = {**meta_doc, "chunk_id": chunk_id,
                        "artigo" if meta_doc["doc_type"] != "edital" else "clausula": nome,
                        "parte": n, "tem_overlap": n > 1}
            if achados:
                metadata["dados_pessoais"] = achados
            chunks.append({"texto": texto_chunk, "metadata": metadata})
    return chunks


def aviso_sem_data(chunks, versao="v1"):
    """
    Monta o aviso dos chunks que ficaram sem data de vigência.

    A data dos editais vem das fichas .meta.json que a coleta grava em
    data/raw/, pasta que o Git ignora. Quem clona o projeto e roda só a
    ingestão gera o corpus inteiro sem data e não percebe: o JSONL sai com o
    número certo de chunks e nenhum erro aparece.

    Devolve lista vazia quando está tudo certo.
    """
    sem_data = [c for c in chunks if not c["metadata"].get("data_publicacao")]
    if not sem_data:
        return []
    arquivos = sorted({c["metadata"]["source_file"] for c in sem_data})
    return [
        f"ATENÇÃO: {len(sem_data)} chunk(s) sem data de vigência, "
        f"em {len(arquivos)} arquivo(s): {', '.join(arquivos)}",
        "  Rode a coleta antes da ingestão, para gerar as fichas do PNCP:",
        f"  python -m app.modules.normative.services fetch --versao {versao}",
    ]


def processar(versao="v1", raiz="data/raw", saida=None):
    """Processa todos os documentos da base e grava os chunks em JSONL."""
    pasta = Path(raiz) / versao / BASE_ID
    todos = []
    run_id = obs.novo_run_id()
    for caminho in sorted(pasta.iterdir()):
        if caminho.suffix.lower() not in (".pdf", ".html", ".htm"):
            continue
        with obs.etapa(run_id, STAGE, entrada=caminho.name,
                       base_id=BASE_ID, versao=versao) as ev:
            chunks = gerar_chunks(caminho)
            m = chunks[0]["metadata"]
            mascarados = sum(1 for c in chunks if "dados_pessoais" in c["metadata"])
            ev["saida"] = f"{len(chunks)} chunks; {m['doc_type']}; {mascarados} mascarado(s)"
        aviso = f" | {mascarados} chunk(s) com dado pessoal mascarado" if mascarados else ""
        print(f"  {caminho.name}: {m['doc_type']} | {len(chunks)} chunks{aviso}")
        todos.extend(chunks)

    # Os chunks vão para data/processed/, como define a arquitetura do time
    if saida:
        saida = Path(saida)
    else:
        pasta_processed = Path("data/processed")
        pasta_processed.mkdir(parents=True, exist_ok=True)
        saida = pasta_processed / f"{versao}_{BASE_ID}.jsonl"
    with saida.open("w", encoding="utf-8") as f:
        for c in todos:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"Total: {len(todos)} chunks em {saida}")
    for linha in aviso_sem_data(todos, versao):
        print(linha)
    print(f"Log em logs/ · run_id {run_id}")
    return todos


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Processamento da base normativa")
    parser.add_argument("--versao", default="v1")
    parser.add_argument("--raiz", default="data/raw")
    parser.add_argument("--saida", default=None)
    args = parser.parse_args()
    processar(args.versao, args.raiz, args.saida)
