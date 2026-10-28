"""
Coleta da base normativa (editais_normativo).

Lê o bloco `editais_normativo` do corpus_manifest.yaml, grava os documentos em
data/raw/<versao>/editais_normativo/ e os registros da coleta em
data/inventory/<versao>_editais_normativo.json, seguindo as regras do
enunciado (item 2.3):

- idempotente: arquivo já baixado não é baixado de novo;
- retomável: se a coleta cair no meio, rodar de novo continua de onde parou;
- respeita o rate_limit_seg do manifesto;
- registra falhas sem abortar: um link com problema vira status "falhou".

Uso:
    python -m app.modules.normative.services --manifest corpus_manifest.yaml --versao v1
"""

import argparse
import hashlib
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
import truststore
import yaml

from app.core import logging as obs

# Os sites do governo (Planalto, PNCP) usam certificados da ICP-Brasil, que o
# Python não reconhece sozinho (dá SSLError). O truststore faz o Python usar os
# certificados do Windows, que já confiam na ICP-Brasil.
truststore.inject_into_ssl()

BASE_ID = "editais_normativo"
TENTATIVAS = 3              # o PNCP às vezes responde 422 e funciona segundos depois
ESPERA_ENTRE_TENTATIVAS = 5  # segundos
STAGE = obs.STAGE_COLETA

sessao = requests.Session()
# O Planalto não responde se o programa não se identificar como navegador.
sessao.headers.update({"User-Agent": "Mozilla/5.0 (compatible; rag-grande-desafio-grupo03)"})


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def agora():
    """Data e hora atual com fuso, no formato usado no inventário."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_de(conteudo: bytes) -> str:
    """Impressão digital do arquivo: muda se qualquer byte mudar."""
    return hashlib.sha256(conteudo).hexdigest()


def detectar_tipo(conteudo: bytes) -> str | None:
    """
    Descobre o tipo pelo começo do arquivo, e não pelo que o site diz.
    Isso pega os casos que já vimos: a página de erro do PNCP (JSON) e a
    "verificação de segurança" do Senado (HTML no lugar do PDF).
    """
    inicio = conteudo[:1024].lstrip().lower()
    if inicio.startswith(b"%pdf"):
        return "pdf"
    if inicio.startswith(b"pk"):
        return "zip"
    if inicio.startswith(b"<!doctype html") or inicio.startswith(b"<html"):
        return "html"
    return None


def nome_do_arquivo(url: str) -> str:
    """
    Gera um nome fixo para cada URL (sem extensão), para a coleta saber se o
    arquivo já foi baixado. O mesmo link sempre gera o mesmo nome.

    PNCP:     .../orgaos/<cnpj>/compras/<ano>/<seq>/arquivos/<n>  -> pncp_<cnpj>_<ano>_<seq>_arq<n>
    Planalto: .../lei/l14133.htm                                 -> planalto_l14133
    """
    partes = urlparse(url).path.strip("/").split("/")
    if "pncp.gov.br" in url and "orgaos" in partes:
        i = partes.index("orgaos")
        cnpj, ano, seq, n = partes[i + 1], partes[i + 3], partes[i + 4], partes[i + 6]
        return f"pncp_{cnpj}_{ano}_{seq}_arq{n}"
    if "planalto.gov.br" in url:
        return "planalto_" + Path(partes[-1]).stem
    # Qualquer outro site: usa o final do caminho
    return Path(partes[-1]).stem or "documento"


def ja_baixado(pasta: Path, nome: str) -> Path | None:
    """
    Idempotência: procura o documento com qualquer extensão.

    Ignora o `<nome>.meta.json` gravado ao lado dos editais do PNCP. Sem isso,
    na segunda coleta a ficha podia ser encontrada antes do PDF e entrar no
    inventário no lugar do documento, com o tamanho e o hash errados.
    """
    encontrados = [p for p in pasta.glob(nome + ".*") if not p.name.endswith(".meta.json")]
    return encontrados[0] if encontrados else None


def baixar_com_retentativa(url: str) -> bytes:
    """
    Baixa a URL tentando de novo quando dá erro. Se todas as tentativas
    falharem, levanta exceção com o último erro (quem chama decide o que fazer).
    """
    ultimo_erro = None
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resposta = sessao.get(url, timeout=120)
            if resposta.status_code == 200:
                return resposta.content
            ultimo_erro = f"HTTP {resposta.status_code}"
        except requests.RequestException as erro:
            ultimo_erro = type(erro).__name__
        if tentativa < TENTATIVAS:
            time.sleep(ESPERA_ENTRE_TENTATIVAS)
    raise RuntimeError(f"{ultimo_erro} após {TENTATIVAS} tentativas")
def url_da_lista(url: str) -> str | None:
    """
    De .../compras/<ano>/<seq>/arquivos/<n> para .../compras/<ano>/<seq>/arquivos.

    O link do manifesto aponta para UM arquivo; tirando o número do fim,
    chega-se à lista de arquivos daquela compra, que o PNCP responde em JSON
    com a ficha de cada um. Devolve None para links que não são do PNCP
    (a Lei 14.133 do Planalto, por exemplo).
    """
    if "pncp.gov.br" not in url or "/arquivos/" not in url:
        return None
    return url.rsplit("/", 1)[0]


def ficha_do_pncp(url: str) -> dict | None:
    """
    Pergunta ao PNCP a ficha do documento: data de publicação, título e tipo.

    É daqui que sai o `campo_vigencia` dos editais (data_publicacao). Sem isso,
    a data teria de ser garimpada do texto do PDF, que cita dezenas de datas de
    outras normas ("Lei nº 14.133, de 1º de abril de 2021", "Decreto nº 48.843,
    de 13 de dezembro de 2023"), e qualquer uma delas seria pega por engano.

    Devolve None se o link não for do PNCP ou se a consulta falhar: a coleta do
    documento não pode quebrar por causa da ficha.
    """
    lista = url_da_lista(url)
    if not lista:
        return None
    try:
        sequencial = int(url.rsplit("/", 1)[1])
        resposta = sessao.get(lista, timeout=60, headers={"Accept": "application/json"})
        if resposta.status_code != 200:
            return None
        for item in resposta.json():
            if item.get("sequencialDocumento") == sequencial:
                return {
                    "data_publicacao": (item.get("dataPublicacaoPncp") or "")[:10] or None,
                    "titulo": item.get("titulo"),
                    "tipo_documento": item.get("tipoDocumentoNome"),
                    "origem": url,
                }
    except (requests.RequestException, ValueError):
        return None
    return None


def salvar_ficha(pasta: Path, nome: str, url: str):
    """
    Grava <nome>.meta.json ao lado do documento, com a ficha do PNCP.

    A ingestão lê esse arquivo em vez de consultar a rede: assim a rede fica só
    na coleta, que é o lugar certo, e os testes do processamento continuam
    rodando offline. Ficha já existente não é baixada de novo (idempotência).
    """
    destino = pasta / f"{nome}.meta.json"
    if destino.exists():
        return
    ficha = ficha_do_pncp(url)
    if ficha:
        destino.write_text(json.dumps(ficha, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [ficha]     {destino.name} · publicado em {ficha['data_publicacao']}")

SCRIPT_HTML = re.compile(rb"<script.*?</script>", re.S | re.I)


def sha256_do_conteudo(conteudo: bytes, tipo: str | None) -> str | None:
    """
    Impressão digital do CONTEÚDO, ignorando o que muda a cada download sem que
    o documento tenha mudado. No HTML do Planalto, por exemplo, há um script de
    segurança com um código diferente a cada acesso: sem isso, toda atualização
    acusaria que a lei mudou (alarme falso no diff entre versões).
    """
    if conteudo is None:
        return None
    if tipo == "html":
        conteudo = SCRIPT_HTML.sub(b"", conteudo)
    return sha256_de(conteudo)


def registro(caminho, tipo, conteudo, origem, status="ok", erro=None):
    """Uma linha do inventário, no formato do enunciado."""
    return {
        "path": str(caminho).replace("\\", "/") if caminho else None,
        "base_id": BASE_ID,
        "tipo": tipo,
        "bytes": len(conteudo) if conteudo else 0,
        "sha256": sha256_de(conteudo) if conteudo else None,
        "sha256_conteudo": sha256_do_conteudo(conteudo, tipo),
        "origem": origem,
        "coletado_em": agora(),
        "status": status,
        "erro": erro,
    }


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------

def carregar_bloco_normativo(caminho_manifesto: str) -> dict:
    """Lê o manifesto e devolve só o bloco da base normativa."""
    with open(caminho_manifesto, encoding="utf-8") as f:
        manifesto = yaml.safe_load(f)
    for base in manifesto["bases"]:
        if base["base_id"] == BASE_ID:
            return base
    raise ValueError(f"Base {BASE_ID} não encontrada no manifesto")


def _registrar_documento(run_id, r, latencia_ms=None, **extra):
    """
    Uma linha de log por documento, emitida onde o trabalho acontece.

    Sem `run_id` não registra nada: é assim que os testes chamam a coleta sem
    escrever na pasta logs/ do projeto.
    """
    if run_id is None:
        return
    obs.registrar(
        run_id,
        STAGE,
        r["status"],
        latencia_ms,
        entrada=r["origem"],
        saida=r["path"],
        base_id=BASE_ID,
        bytes=r["bytes"],
        error_type="document_fetch_error" if r.get("erro") else None,
        **extra,
    )


def coletar_urls(urls, pasta, rate_limit, run_id=None):
    """Parte 1: documentos baixados por URL (Lei 14.133 e editais do PNCP)."""
    registros = []
    for url in urls:
        nome = nome_do_arquivo(url)

        existente = ja_baixado(pasta, nome)
        if existente:
            conteudo = existente.read_bytes()
            print(f"  [já existe] {existente.name}")
            r = registro(existente, existente.suffix[1:], conteudo, url)
            registros.append(r)
            # Sem duração: nada foi baixado, o arquivo já estava aqui.
            _registrar_documento(run_id, r, baixado=False)
            salvar_ficha(pasta, nome, url)
            continue

        inicio = time.perf_counter()
        try:
            conteudo = baixar_com_retentativa(url)
        except RuntimeError as erro:
            ms = int((time.perf_counter() - inicio) * 1000)
            print(f"  [FALHOU]    {url} -> {erro}")
            r = registro(None, None, None, url, "falhou", str(erro))
            registros.append(r)
            # A duração aqui inclui as 3 tentativas e as pausas entre elas:
            # é o que denuncia o 422 intermitente do PNCP.
            _registrar_documento(run_id, r, ms, baixado=True)
            time.sleep(rate_limit)
            continue
        ms = int((time.perf_counter() - inicio) * 1000)

        tipo = detectar_tipo(conteudo)
        if tipo not in ("pdf", "html", "zip"):
            # Veio uma página de erro ou algo inesperado: não salva como documento
            print(f"  [FALHOU]    {url} -> conteúdo não é PDF/HTML/ZIP")
            r = registro(None, None, None, url, "falhou",
                         "conteúdo inesperado (não é PDF/HTML/ZIP)")
            registros.append(r)
            _registrar_documento(run_id, r, ms, baixado=True)
        else:
            destino = pasta / f"{nome}.{tipo}"
            destino.write_bytes(conteudo)
            print(f"  [baixado]   {destino.name} ({len(conteudo):,} bytes)")
            r = registro(destino, tipo, conteudo, url)
            registros.append(r)
            _registrar_documento(run_id, r, ms, baixado=True)
            salvar_ficha(pasta, nome, url)

        time.sleep(rate_limit)  # respeita o site entre um download e outro
    return registros


def coletar_arquivos_locais(bloco, pasta, run_id=None):
    """
    Parte 2: decretos baixados à mão (data/manual/...). Copia para a versão
    e confere o sha256 anotado no manifesto. Hash diferente vira falha.
    """
    registros = []
    if not bloco:
        return registros
    origem_pasta = Path(bloco["pasta"])
    for item in bloco["arquivos"]:
        origem = origem_pasta / item["arquivo"]
        if not origem.exists():
            print(f"  [FALHOU]    {origem} não encontrado")
            r = registro(None, None, None, item["origem"], "falhou", "arquivo local não encontrado")
            registros.append(r)
            _registrar_documento(run_id, r, local=True)
            continue

        conteudo = origem.read_bytes()
        if sha256_de(conteudo) != item["sha256"].lower():
            print(f"  [FALHOU]    {origem.name}: sha256 diferente do manifesto")
            r = registro(None, None, None, item["origem"], "falhou", "sha256 diferente do manifesto")
            registros.append(r)
            _registrar_documento(run_id, r, local=True)
            continue

        destino = pasta / item["arquivo"]
        if not destino.exists():
            shutil.copy2(origem, destino)
            print(f"  [copiado]   {destino.name}")
        else:
            print(f"  [já existe] {destino.name}")
        # Sem duração: vem do disco, não da rede.
        r = registro(destino, detectar_tipo(conteudo), conteudo, item["origem"])
        registros.append(r)
        _registrar_documento(run_id, r, local=True)
    return registros


def coletar(caminho_manifesto="corpus_manifest.yaml", versao="v1", raiz="data/raw"):
    """Coleta completa da base normativa. Devolve a lista de registros."""
    base = carregar_bloco_normativo(caminho_manifesto)
    acesso = base["acesso"]
    pasta = Path(raiz) / versao / BASE_ID
    pasta.mkdir(parents=True, exist_ok=True)

    print(f"Coletando {BASE_ID} em {pasta}")
    run_id = obs.novo_run_id()
    with obs.etapa(run_id, STAGE, entrada=str(pasta),
                   base_id=BASE_ID, versao=versao) as ev:
        registros = coletar_urls(acesso.get("urls", []), pasta,
                                 acesso.get("rate_limit_seg", 1.0), run_id)
        registros += coletar_arquivos_locais(acesso.get("arquivos_locais"), pasta, run_id)
        ok = sum(r["status"] == "ok" for r in registros)
        falhas = len(registros) - ok
        ev["saida"] = f"{ok} ok, {falhas} falhas de {len(registros)}"

    preservar_coletado_em(registros, versao)

    print(f"Resumo: {ok} ok, {falhas} falhas | volume_alvo {base['volume_alvo']} "
          f"| divergência {base['volume_alvo'] - ok}")
    print(f"Log em logs/ · run_id {run_id}")

    # Guarda os registros para quem monta o inventário (data/inventory/)
    pasta_inventario = Path("data/inventory")
    pasta_inventario.mkdir(parents=True, exist_ok=True)
    saida = pasta_inventario / f"{versao}_{BASE_ID}.json"
    saida.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Registros salvos em {saida}")
    return registros


# ---------------------------------------------------------------------------
# Versionamento: update (nova versão) e inventory (inventário legível)
# ---------------------------------------------------------------------------

PASTA_INVENTARIO = Path("data/inventory")


def versoes_existentes(raiz="data/raw"):
    """Versões já coletadas, em ordem: ['v1', 'v2', ...]."""
    pasta = Path(raiz)
    if not pasta.exists():
        return []
    versoes = [p.name for p in pasta.iterdir() if p.is_dir() and re.fullmatch(r"v\d+", p.name)]
    return sorted(versoes, key=lambda v: int(v[1:]))


def proxima_versao(raiz="data/raw"):
    """'v1' se não houver nenhuma; senão, a seguinte à última ('v2', 'v3'...)."""
    existentes = versoes_existentes(raiz)
    return f"v{int(existentes[-1][1:]) + 1}" if existentes else "v1"


def carregar_registros(versao):
    """Lê os registros de uma versão já coletada (data/inventory/vN_<base>.json)."""
    caminho = PASTA_INVENTARIO / f"{versao}_{BASE_ID}.json"
    if not caminho.exists():
        raise FileNotFoundError(f"Inventário não encontrado: {caminho}")
    return json.loads(caminho.read_text(encoding="utf-8"))


def preservar_coletado_em(registros, versao):
    """
    Mantém a data da coleta original quando o documento não mudou.

    O `registro()` carimba a hora da execução. Como o fetch é idempotente, um
    arquivo já baixado não é baixado de novo — mas o carimbo mudava assim
    mesmo, e o inventário passava a dizer que o documento tinha sido coletado
    hoje. Aqui a data anterior é restaurada quando a origem e o sha256 batem.

    Só vale dentro da mesma versão: numa versão nova, a coleta é outra e a data
    é a de agora, mesmo que o conteúdo seja idêntico.

    A comparação usa o `sha256_conteudo`, como o diff entre versões: o sha256
    bruto do HTML do Planalto muda a cada download por causa do script de
    segurança do site, e por isso difere até entre duas máquinas do time.
    """
    def _hash(r):
        return r.get("sha256_conteudo") or r.get("sha256")

    try:
        anteriores = _por_origem(carregar_registros(versao))
    except FileNotFoundError:
        return registros          # primeira coleta desta versão
    for r in registros:
        antigo = anteriores.get(r["origem"])
        if antigo and _hash(antigo) == _hash(r) and antigo.get("coletado_em"):
            r["coletado_em"] = antigo["coletado_em"]
    return registros


def _por_origem(registros):
    """Indexa os registros pela origem (URL ou link do decreto), que não muda entre versões."""
    return {r["origem"]: r for r in registros}


def gerar_diff(versao_anterior, versao_nova):
    """
    Compara duas versões e grava data/inventory/DIFF_<ant>_<nova>.md respondendo:
    o que entrou, o que saiu, o que mudou de conteúdo (sha256 diferente) e o que
    permaneceu idêntico. Esse diff é o insumo do teste de regressão.
    """
    antes = _por_origem(carregar_registros(versao_anterior))
    depois = _por_origem(carregar_registros(versao_nova))

    # Compara pelo sha256 do conteúdo (ignora o que muda a cada download sem
    # que o documento tenha mudado). Inventários antigos caem no sha256 do arquivo.
    def _hash(r):
        return r.get("sha256_conteudo") or r["sha256"]

    entraram = [o for o in depois if o not in antes]
    sairam = [o for o in antes if o not in depois]
    mudaram = [o for o in depois if o in antes and _hash(depois[o]) != _hash(antes[o])]
    identicos = [o for o in depois if o in antes and _hash(depois[o]) == _hash(antes[o])]

    def _nome(origem, mapa):
        caminho = mapa[origem].get("path")
        return Path(caminho).name if caminho else "(sem arquivo)"

    linhas = [
        f"# DIFF {versao_anterior} -> {versao_nova} · {BASE_ID}",
        "",
        f"Gerado em {agora()}",
        "",
        "| Situação | Quantidade |",
        "| --- | --- |",
        f"| Entraram | {len(entraram)} |",
        f"| Saíram | {len(sairam)} |",
        f"| Mudaram de conteúdo | {len(mudaram)} |",
        f"| Permaneceram idênticos | {len(identicos)} |",
        "",
    ]
    for titulo, origens, mapa in [("## Entraram", entraram, depois),
                                  ("## Saíram", sairam, antes),
                                  ("## Mudaram de conteúdo", mudaram, depois),
                                  ("## Permaneceram idênticos", identicos, depois)]:
        linhas.append(titulo)
        linhas.append("")
        if not origens:
            linhas += ["Nenhum.", ""]
            continue
        for origem in sorted(origens, key=lambda o: _nome(o, mapa)):
            nome = _nome(origem, mapa)
            if titulo == "## Mudaram de conteúdo":
                linhas.append(f"- `{nome}` · sha256 {_hash(antes[origem])[:12]} -> "
                              f"{_hash(depois[origem])[:12]} · {origem}")
            else:
                linhas.append(f"- `{nome}` · {origem}")
        linhas.append("")

    PASTA_INVENTARIO.mkdir(parents=True, exist_ok=True)
    saida = PASTA_INVENTARIO / f"DIFF_{versao_anterior}_{versao_nova}.md"
    saida.write_text("\n".join(linhas), encoding="utf-8")
    print(f"Diff: {len(entraram)} entraram, {len(sairam)} saíram, "
          f"{len(mudaram)} mudaram, {len(identicos)} idênticos")
    print(f"Diff salvo em {saida}")
    return saida


def atualizar(caminho_manifesto="corpus_manifest.yaml", raiz="data/raw"):
    """
    Cria uma versão nova (v(n+1)) SEM sobrescrever a anterior e gera o diff
    entre as duas. Se ainda não existir nenhuma versão, funciona como o fetch.
    """
    anterior = versoes_existentes(raiz)[-1] if versoes_existentes(raiz) else None
    nova = proxima_versao(raiz)
    print(f"Atualizando: {anterior or '(nenhuma)'} -> {nova}")

    coletar(caminho_manifesto, nova, raiz)
    if anterior:
        gerar_diff(anterior, nova)
    else:
        print("Primeira versão coletada: não há diff a gerar.")
    return nova


def escrever_inventario(versao):
    """
    Gera o inventário legível da base (data/inventory/<versao>_<base>.md), com
    os totais, a tabela de arquivos e a lista de falhas. É o insumo da seção da
    base normativa no CORPUS_INVENTORY.md.
    """
    registros = carregar_registros(versao)
    ok = [r for r in registros if r["status"] == "ok"]
    falhas = [r for r in registros if r["status"] != "ok"]
    formatos = {}
    for r in ok:
        formatos[r["tipo"]] = formatos.get(r["tipo"], 0) + 1
    total_bytes = sum(r["bytes"] for r in ok)

    linhas = [
        f"# Inventário {versao} · {BASE_ID}",
        "",
        f"Coletado em {max((r['coletado_em'] for r in ok), default='-')}",
        "",
        "| | |",
        "| --- | --- |",
        f"| Arquivos | {len(ok)} |",
        f"| Bytes | {total_bytes:,} |".replace(",", "."),
        f"| Formatos | {', '.join(f'{n} {t}' for t, n in sorted(formatos.items()))} |",
        f"| Falhas | {len(falhas)} |",
        "",
        "## Arquivos",
        "",
        "| Arquivo | Tipo | Bytes | sha256 | Origem |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in ok:
        linhas.append(f"| `{Path(r['path']).name}` | {r['tipo']} | {r['bytes']:,} | "
                      f"`{r['sha256'][:12]}` | {r['origem']} |".replace(",", "."))
    linhas += ["", "## Falhas", ""]
    linhas += ([f"- {r['origem']} · {r['erro']}" for r in falhas] if falhas else ["Nenhuma."])

    saida = PASTA_INVENTARIO / f"{versao}_{BASE_ID}.md"
    saida.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"Inventário salvo em {saida}")
    return saida


def hash_do_manifesto(caminho_manifesto="corpus_manifest.yaml") -> str:
    """
    sha256 do manifesto. É o que amarra o corpus coletado à versão exata do
    manifesto que o gerou: se alguém trocar uma URL ou um volume_alvo, o hash
    muda e fica evidente que o inventário é de outro corpus.

    O fim de linha é normalizado para LF antes de calcular: no Windows o Git
    converte o arquivo para CRLF ao baixar, e sem isso a mesma versão do
    manifesto teria um hash diferente em cada máquina. Com a normalização, o
    hash é o do arquivo como está no repositório.
    """
    conteudo = Path(caminho_manifesto).read_bytes().replace(b"\r\n", b"\n")
    return "sha256:" + sha256_de(conteudo)


def inventario_json(versao, caminho_manifesto="corpus_manifest.yaml"):
    """
    Monta data/inventory/<versao>.json no formato do enunciado (item 2.3):
    scenario_id, version, coletado_em, manifest_hash, totais, por_base,
    arquivos e falhas.

    Junta o que cada base já gravou em <versao>_<base_id>.json. Base ainda não
    coletada simplesmente não aparece, e entra sozinha quando a coleta dela
    rodar — ninguém precisa esperar por ninguém.
    """
    manifesto = yaml.safe_load(Path(caminho_manifesto).read_text(encoding="utf-8"))

    arquivos, falhas, por_base = [], [], []
    for base in manifesto["bases"]:
        caminho = PASTA_INVENTARIO / f"{versao}_{base['base_id']}.json"
        if not caminho.exists():
            continue
        registros = json.loads(caminho.read_text(encoding="utf-8"))
        ok = [r for r in registros if r["status"] == "ok"]

        formatos = {}
        for r in ok:
            formatos[r["tipo"]] = formatos.get(r["tipo"], 0) + 1

        por_base.append({
            "base_id": base["base_id"],
            "natureza": base["natureza"],
            "arquivos": len(ok),
            "formatos": formatos,
            "bytes": sum(r["bytes"] for r in ok),
            "volume_alvo": base.get("volume_alvo"),
            "divergencia_alvo": (base.get("volume_alvo") or 0) - len(ok),
        })
        arquivos += ok
        falhas += [{"origem": r["origem"], "erro": r["erro"]}
                   for r in registros if r["status"] != "ok"]

    inventario = {
        "scenario_id": manifesto["scenario_id"],
        "version": versao,
        "coletado_em": max((r["coletado_em"] for r in arquivos), default=agora()),
        "manifest_hash": hash_do_manifesto(caminho_manifesto),
        "totais": {
            "arquivos": len(arquivos),
            "bytes": sum(r["bytes"] for r in arquivos),
            "falhas": len(falhas),
        },
        "por_base": por_base,
        "arquivos": arquivos,
        "falhas": falhas,
    }

    saida = PASTA_INVENTARIO / f"{versao}.json"
    saida.write_text(json.dumps(inventario, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventário JSON salvo em {saida} "
          f"({len(arquivos)} arquivos, {len(falhas)} falhas, {len(por_base)} base(s))")
    return saida


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Coleta e versionamento da base normativa")
    sub = parser.add_subparsers(dest="comando")

    p_fetch = sub.add_parser("fetch", help="coleta inicial em uma versão")
    p_fetch.add_argument("--manifest", default="corpus_manifest.yaml")
    p_fetch.add_argument("--versao", default="v1")
    p_fetch.add_argument("--raiz", default="data/raw")

    p_update = sub.add_parser("update", help="cria a versão seguinte e gera o diff")
    p_update.add_argument("--manifest", default="corpus_manifest.yaml")
    p_update.add_argument("--raiz", default="data/raw")

    p_inv = sub.add_parser("inventory", help="gera o inventário legível de uma versão")
    p_inv.add_argument("--versao", default="v1")
    p_inv.add_argument("--manifest", default="corpus_manifest.yaml")

    # Sem subcomando, mantém o uso antigo: coleta a versão indicada
    parser.add_argument("--manifest", default="corpus_manifest.yaml")
    parser.add_argument("--versao", default="v1")
    parser.add_argument("--raiz", default="data/raw")

    args = parser.parse_args()
    if args.comando == "update":
        atualizar(args.manifest, args.raiz)
    elif args.comando == "inventory":
        escrever_inventario(args.versao)
        inventario_json(args.versao, args.manifest) 
    else:
        coletar(args.manifest, args.versao, args.raiz)
