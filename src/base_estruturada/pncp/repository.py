import json
import zipfile
import shutil
import stat
import os
import base64
import binascii
import copy

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from src.base_estruturada.pncp.service import (
    consultar_documentos_contrato_detalhado,
    consultar_documentos_contratacao_detalhado,
)


ARQUIVO_CONTRATOS = Path(
    "data/normalized/contratos_rj.json"
)

ARQUIVO_CONTRATACOES = Path(
    "data/normalized/contratacoes_rj.json"
)


def carregar_json(caminho: Path | str, padrao: Any) -> Any:
    caminho = Path(caminho)

    if not caminho.exists():
        return padrao

    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            return json.load(arquivo)

    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"O arquivo JSON está inválido: {caminho}"
        ) from exc


def salvar_json(caminho: Path | str, dados: Any) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)

    temporario = caminho.with_suffix(".tmp")

    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)

    os.replace(temporario, caminho)


def _chave_documento(
    numero_controle: str,
    documento: dict[str, Any],
) -> str:
    """Identificador estavel do documento dentro de um contrato/compra."""

    sequencial = documento.get("sequencialDocumento", "sem_sequencial")
    return f"{numero_controle}|{sequencial}"


def carregar_checkpoint_documentos(caminho: Path | str) -> dict[str, Any]:
    """Carrega o estado separado da listagem e do processamento documental."""

    checkpoint = carregar_json(
        caminho,
        padrao={"entidades": {}, "documentos": {}},
    )
    if not isinstance(checkpoint, dict):
        raise RuntimeError(f"Checkpoint de documentos invalido: {caminho}")

    checkpoint.setdefault("schemaVersion", "estado_documentos_pncp_v1")
    checkpoint.setdefault("entidades", {})
    checkpoint.setdefault("documentos", {})
    if not isinstance(checkpoint["entidades"], dict):
        checkpoint["entidades"] = {}
    if not isinstance(checkpoint["documentos"], dict):
        checkpoint["documentos"] = {}
    return checkpoint


def materializar_manifesto_com_checkpoint(
    manifesto_original: list[dict[str, Any]],
    checkpoint: dict[str, Any],
) -> list[dict[str, Any]]:
    """Aplica estado local a uma copia, sem alterar a listagem original."""

    manifesto = copy.deepcopy(manifesto_original)
    estados = checkpoint.get("documentos") or {}

    for entidade in manifesto:
        if not isinstance(entidade, dict):
            continue
        numero_controle = str(entidade.get("numeroControlePNCP") or "")
        if not numero_controle:
            continue
        for documento in entidade.get("documentos") or []:
            if not isinstance(documento, dict):
                continue
            estado = estados.get(_chave_documento(numero_controle, documento))
            if not isinstance(estado, dict):
                continue
            for campo in ("download", "extracao", "processamentoTexto"):
                if campo in estado:
                    documento[campo] = copy.deepcopy(estado[campo])

    return manifesto


def salvar_estado_documentos(
    caminho: Path | str,
    checkpoint: dict[str, Any],
    manifesto_processado: list[dict[str, Any]],
) -> None:
    """Atualiza somente o checkpoint com o estado produzido no processamento."""

    documentos = checkpoint.setdefault("documentos", {})
    for entidade in manifesto_processado:
        if not isinstance(entidade, dict):
            continue
        numero_controle = str(entidade.get("numeroControlePNCP") or "")
        if not numero_controle:
            continue
        for documento in entidade.get("documentos") or []:
            if not isinstance(documento, dict):
                continue
            estado = {
                campo: copy.deepcopy(documento[campo])
                for campo in ("download", "extracao", "processamentoTexto")
                if campo in documento
            }
            # Todo documento listado aparece no checkpoint. A ausencia de
            # download deixa de ser ambigua: significa explicitamente pendente.
            if not estado:
                estado = {"download": {"status": "pendente"}}
            documentos[_chave_documento(numero_controle, documento)] = estado

    salvar_json(caminho, checkpoint)


def salvar_pagina_raw(
        dados: dict[str, Any],
        pagina: int,
        data_inicial: str,
        data_final: str
) -> None:
    caminho = Path(
        "data/raw/contratos/"
        f"{data_inicial}_{data_final}/"
        f"pagina_{pagina:04d}.json"
    )

    salvar_json(caminho, dados)


def adicionar_registros_novos(
        novos_registros: list[dict[str, Any]],
        arquivo: Path | str
) -> int:
    """
    Mescla novos_registros no arquivo indicado, deduplicando por
    numeroControlePNCP. Serve tanto para contratos quanto para
    contratacoes -- o dado que muda e' so o arquivo de destino.
    """

    registros_existentes = carregar_json(
        arquivo,
        padrao=[],
    )

    if not isinstance(registros_existentes, list):
        raise RuntimeError(
            f"O arquivo {arquivo} deveria conter uma lista."
        )

    controles_existentes = {
        item.get("numeroControlePNCP")
        for item in registros_existentes
        if isinstance(item, dict)
        and item.get("numeroControlePNCP")
    }

    quantidade_nova = 0

    for registro in novos_registros:

        if not isinstance(registro, dict):
            continue

        numero_controle = registro.get("numeroControlePNCP")

        if not numero_controle:
            continue

        if numero_controle in controles_existentes:
            continue

        registros_existentes.append(registro)
        controles_existentes.add(numero_controle)
        quantidade_nova += 1

    salvar_json(arquivo, registros_existentes)

    return quantidade_nova


def adicionar_contratos_novos(novos_contratos: list[dict[str, Any]]) -> int:
    return adicionar_registros_novos(novos_contratos, ARQUIVO_CONTRATOS)


def adicionar_contratacoes_novas(novas_contratacoes: list[dict[str, Any]]) -> int:
    return adicionar_registros_novos(novas_contratacoes, ARQUIVO_CONTRATACOES)


def salvar_paginas_com_erro(
    data_inicial: str,
    data_final: str,
    paginas: list[int],
    caminho: Path | str
) -> None:
    """
    caminho = Path(
        "data/checkpoints/contratos_paginas_com_erro.json"
    )
    """

    dados = {
        "dataInicial": data_inicial,
        "dataFinal": data_final,
        "paginasComErro": paginas,
    }

    salvar_json(caminho, dados)


def salvar_falhas_coleta(
    falhas: list[dict[str, Any]],
    caminho: Path | str,
    tipo: str,
) -> None:
    """
    Persiste falhas estruturadas de contratos ou contratações.

    A chave formada por tipo, período, página e modalidade evita
    duplicar a mesma falha em execuções consecutivas.
    """

    caminho = Path(caminho)
    dados_existentes = carregar_json(caminho, padrao={})

    falhas_existentes: list[dict[str, Any]] = []

    if isinstance(dados_existentes, dict):
        falhas_existentes = dados_existentes.get("falhas", [])

        # Migra o formato antigo, que guardava somente páginas.
        if not falhas_existentes and dados_existentes.get("paginasComErro"):
            for pagina in dados_existentes["paginasComErro"]:
                falha_migrada: dict[str, Any] = {
                    "tipo": tipo,
                    "dataInicial": dados_existentes.get("dataInicial"),
                    "dataFinal": dados_existentes.get("dataFinal"),
                    "pagina": pagina,
                    "motivo": "migrado_formato_antigo",
                    "status": "pendente",
                    "tentativasReprocessamento": 0,
                }

                # Uma contratação antiga sem modalidade não pode ser
                # reprocessada automaticamente com segurança.
                if tipo == "contratacao":
                    falha_migrada["modalidade"] = None
                    falha_migrada["status"] = "revisao_manual"

                falhas_existentes.append(falha_migrada)

    def chave(falha: dict[str, Any]) -> tuple[Any, ...]:
        return (
            falha.get("tipo"),
            falha.get("dataInicial"),
            falha.get("dataFinal"),
            falha.get("modalidade"),
            falha.get("pagina"),
        )

    chaves_existentes = {
        chave(falha)
        for falha in falhas_existentes
        if isinstance(falha, dict)
    }

    for falha in falhas:
        if not isinstance(falha, dict):
            continue

        if chave(falha) in chaves_existentes:
            continue

        falhas_existentes.append(falha)
        chaves_existentes.add(chave(falha))

    salvar_json(
        caminho,
        {"falhas": falhas_existentes},
    )


def atualizar_falha_reprocessada(
    caminho: Path | str,
    falha_original: dict[str, Any],
    sucesso: bool,
    motivo: str | None = None,
    max_tentativas: int = 3,
) -> None:
    """Atualiza uma falha após uma tentativa manual de reprocessamento."""

    caminho = Path(caminho)
    dados = carregar_json(caminho, padrao={"falhas": []})
    falhas = dados.get("falhas", []) if isinstance(dados, dict) else []

    def chave(falha: dict[str, Any]) -> tuple[Any, ...]:
        return (
            falha.get("tipo"),
            falha.get("dataInicial"),
            falha.get("dataFinal"),
            falha.get("modalidade"),
            falha.get("pagina"),
        )

    agora = datetime.now(timezone.utc).isoformat()
    chave_original = chave(falha_original)

    for falha in falhas:
        if not isinstance(falha, dict) or chave(falha) != chave_original:
            continue

        tentativas = int(falha.get("tentativasReprocessamento", 0)) + 1
        falha["tentativasReprocessamento"] = tentativas
        falha["ultimoReprocessamentoEm"] = agora

        if sucesso:
            falha["status"] = "resolvida"
            falha["resolvidaEm"] = agora
            falha.pop("ultimoMotivo", None)
        else:
            falha["ultimoMotivo"] = motivo or "falha_desconhecida"
            falha["status"] = (
                "revisao_manual"
                if tentativas >= max_tentativas
                else "pendente"
            )
        break

    salvar_json(caminho, {"falhas": falhas})


def salvar_lista_de_documentos_contratos(
    contratos: list[dict[str, Any]],
    caminho: Path | str,
    caminho_checkpoint: Path | str,
    limite: int = 5,
) -> list[dict[str, Any]]:
    """Lista documentos sem gravar estado de execucao no retorno da API."""

    manifesto = carregar_json(caminho, padrao=[])
    if not isinstance(manifesto, list):
        raise RuntimeError(f"O manifesto deveria conter uma lista: {caminho}")

    checkpoint = carregar_checkpoint_documentos(caminho_checkpoint)
    estados_entidades = checkpoint["entidades"]

    controles_existentes = {
        item.get("numeroControlePNCP")
        for item in manifesto
        if isinstance(item, dict)
        and item.get("numeroControlePNCP")
        and (item.get("listagem") or {}).get("status") != "erro"
    }

    # Compatibilidade com manifestos antigos, que ainda contem ``listagem``.
    for item in manifesto:
        if not isinstance(item, dict):
            continue
        numero_controle = item.get("numeroControlePNCP")
        if not numero_controle or numero_controle in estados_entidades:
            continue
        listagem_antiga = item.get("listagem") or {}
        estados_entidades[numero_controle] = {
            "statusListagem": listagem_antiga.get("status", "concluida"),
            "consultadaEm": listagem_antiga.get("consultadaEm"),
            "ultimoErro": listagem_antiga.get("erro"),
            "tentativas": 0,
        }

    processados = 0
    manifesto_alterado = False

    for contrato in contratos:
        if processados >= limite:
            break

        numero_controle = contrato.get("numeroControlePNCP")
        if not numero_controle or numero_controle in controles_existentes:
            continue

        resultado = consultar_documentos_contrato_detalhado(contrato)
        status = "concluida" if resultado.erro is None else "erro"

        estado_anterior = estados_entidades.get(numero_controle, {})
        estados_entidades[numero_controle] = {
            "statusListagem": status,
            "consultadaEm": datetime.now(timezone.utc).isoformat(),
            "ultimoErro": resultado.erro,
            "tentativas": int(estado_anterior.get("tentativas", 0)) + 1,
        }

        # Sem resposta valida, somente o checkpoint recebe o erro. Assim a
        # proxima execucao tenta novamente sem contaminar o JSON original.
        if resultado.erro is None:
            registro = {
                "numeroControlePNCP": numero_controle,
                "cnpjOrgao": contrato.get("cnpjOrgao")
                or (contrato.get("orgaoEntidade") or {}).get("cnpj"),
                "anoContrato": contrato.get("anoContrato"),
                "sequencialContrato": contrato.get("sequencialContrato"),
                "documentos": resultado.documentos,
            }
            manifesto.append(registro)
            manifesto_alterado = True
            for documento in resultado.documentos:
                if isinstance(documento, dict):
                    checkpoint["documentos"].setdefault(
                        _chave_documento(numero_controle, documento),
                        {"download": {"status": "pendente"}},
                    )
            controles_existentes.add(numero_controle)
        processados += 1
        print(
            f"Contrato: {numero_controle} | documentos="
            f"{len(resultado.documentos)} | status={status}"
        )

    if manifesto_alterado:
        salvar_json(caminho, manifesto)
    salvar_json(caminho_checkpoint, checkpoint)
    return manifesto


def salvar_lista_documentos_contratacao(
    contratacoes: list[dict[str, Any]],
    caminho: Path | str,
    caminho_checkpoint: Path | str,
    limite: int = 5,
) -> list[dict[str, Any]]:
    """Lista documentos sem gravar estado de execucao no retorno da API."""

    manifesto = carregar_json(caminho, padrao=[])
    if not isinstance(manifesto, list):
        raise RuntimeError(f"O manifesto deveria conter uma lista: {caminho}")

    checkpoint = carregar_checkpoint_documentos(caminho_checkpoint)
    estados_entidades = checkpoint["entidades"]

    controles_existentes = {
        item.get("numeroControlePNCP")
        for item in manifesto
        if isinstance(item, dict)
        and item.get("numeroControlePNCP")
        and (item.get("listagem") or {}).get("status") != "erro"
    }
    for item in manifesto:
        if not isinstance(item, dict):
            continue
        numero_controle = item.get("numeroControlePNCP")
        if not numero_controle or numero_controle in estados_entidades:
            continue
        listagem_antiga = item.get("listagem") or {}
        estados_entidades[numero_controle] = {
            "statusListagem": listagem_antiga.get("status", "concluida"),
            "consultadaEm": listagem_antiga.get("consultadaEm"),
            "ultimoErro": listagem_antiga.get("erro"),
            "tentativas": 0,
        }
    processados = 0
    manifesto_alterado = False

    for contratacao in contratacoes:
        if processados >= limite:
            break

        numero_controle = contratacao.get("numeroControlePNCP")
        if not numero_controle or numero_controle in controles_existentes:
            continue

        resultado = consultar_documentos_contratacao_detalhado(contratacao)
        orgao = contratacao.get("orgaoEntidade") or {}
        status = "concluida" if resultado.erro is None else "erro"

        estado_anterior = estados_entidades.get(numero_controle, {})
        estados_entidades[numero_controle] = {
            "statusListagem": status,
            "consultadaEm": datetime.now(timezone.utc).isoformat(),
            "ultimoErro": resultado.erro,
            "tentativas": int(estado_anterior.get("tentativas", 0)) + 1,
        }

        if resultado.erro is None:
            registro = {
                "numeroControlePNCP": numero_controle,
                "cnpjOrgao": orgao.get("cnpj"),
                "anoCompra": contratacao.get("anoCompra"),
                "sequencialCompra": contratacao.get("sequencialCompra"),
                "documentos": resultado.documentos,
            }
            manifesto.append(registro)
            manifesto_alterado = True
            for documento in resultado.documentos:
                if isinstance(documento, dict):
                    checkpoint["documentos"].setdefault(
                        _chave_documento(numero_controle, documento),
                        {"download": {"status": "pendente"}},
                    )
            controles_existentes.add(numero_controle)
        processados += 1
        print(
            f"Contratação: {numero_controle} | documentos="
            f"{len(resultado.documentos)} | status={status}"
        )

    if manifesto_alterado:
        salvar_json(caminho, manifesto)
    salvar_json(caminho_checkpoint, checkpoint)
    return manifesto


def _detectar_formato_arquivo(
    caminho: Path,
) -> tuple[str, str]:
    """
    Retorna: (extensão, descrição do formato).
    """

    with caminho.open("rb") as arquivo:
        inicio = arquivo.read(16)

    if inicio.startswith(b"%PDF-"):
        return ".pdf", "PDF"

    if inicio.startswith(b"Rar!\x1a\x07\x00"):
        return ".rar", "RAR 4"

    if inicio.startswith(b"Rar!\x1a\x07\x01\x00"):
        return ".rar", "RAR 5"

    if inicio.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "PNG"

    if inicio.startswith(b"\xff\xd8\xff"):
        return ".jpg", "JPEG"

    if inicio.startswith(b"PK"):
        try:
            with zipfile.ZipFile(caminho) as arquivo_zip:
                nomes = arquivo_zip.namelist()

                if any(nome.startswith("word/") for nome in nomes):
                    return ".docx", "DOCX"

                if any(nome.startswith("xl/") for nome in nomes):
                    return ".xlsx", "XLSX"

                if any(nome.startswith("ppt/") for nome in nomes):
                    return ".pptx", "PPTX"

                if "mimetype" in nomes:
                    tipo_odf = (
                        arquivo_zip.read("mimetype")
                        .decode("utf-8", errors="ignore")
                    )

                    if "opendocument.text" in tipo_odf:
                        return ".odt", "ODT"

                return ".zip", "ZIP"

        except zipfile.BadZipFile:
            return ".bin", "Binário desconhecido"

    # DOC, XLS e PPT antigos usam o mesmo formato-base OLE.
    if inicio.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return ".ole", "Documento Office antigo (OLE)"

    return ".bin", "Binário desconhecido"


def _decodificar_pdf_base64(caminho: Path) -> Path | None:
    """Decodifica um PDF que o PNCP tenha devolvido como texto Base64.

    Alguns endpoints retornam ``application/octet-stream`` cujo conteudo e'
    Base64 (``JVBER...``), em vez dos bytes normais de um PDF. A conversao so
    acontece quando o resultado pode ser validado pelo cabecalho ``%PDF-``.
    """

    try:
        conteudo = caminho.read_bytes()
    except OSError:
        return None

    # Remove quebras de linha que podem existir no Base64, sem tentar
    # interpretar arquivos binarios desconhecidos como texto.
    conteudo_base64 = b"".join(conteudo.split())
    if not conteudo_base64.startswith(b"JVBER"):
        return None

    try:
        conteudo_decodificado = base64.b64decode(
            conteudo_base64,
            validate=True,
        )
    except (binascii.Error, ValueError):
        return None

    if not conteudo_decodificado.startswith(b"%PDF-"):
        return None

    destino = caminho.with_suffix(".pdf")
    if destino.exists() and destino != caminho:
        destino = destino.with_name(
            f"{destino.stem} (base64){destino.suffix}"
        )

    temporario = destino.with_name(f"{destino.name}.tmp")
    try:
        temporario.write_bytes(conteudo_decodificado)
        os.replace(temporario, destino)
        if caminho != destino:
            caminho.unlink()
    except OSError:
        temporario.unlink(missing_ok=True)
        return None

    return destino


def renomear_arquivo_detectado(
    caminho: Path,
) -> tuple[Path, str, str | None]:
    caminho_pdf = _decodificar_pdf_base64(caminho)
    if caminho_pdf is not None:
        return caminho_pdf, "PDF", "base64"

    extensao, formato = _detectar_formato_arquivo(caminho)

    novo_caminho = caminho.with_suffix(extensao)

    if novo_caminho != caminho:
        caminho.replace(novo_caminho)

    return novo_caminho, formato, None


def identificar_arquivos_do_manifesto(
    manifesto: list[dict[str, Any]],
) -> int:
    quantidade_identificada = 0

    for contrato in manifesto:
        documentos = contrato.get("documentos") or []
        for documento in documentos:
            download = documento.get("download") or {}

            status_atual = download.get("status")
            binario_desconhecido = (
                status_atual == "identificado"
                and download.get("formato") == "Binário desconhecido"
            )
            if status_atual != "baixado" and not binario_desconhecido:
                continue

            caminho_texto = download.get("caminho")

            if not caminho_texto:
                continue

            caminho = Path(caminho_texto)

            if not caminho.exists():
                download["status"] = "arquivo_nao_encontrado"
                documento["download"] = download
                continue

            novo_caminho, formato, codificacao_origem = (
                renomear_arquivo_detectado(caminho)
            )

            dados_download = {
                "status": "identificado",
                "caminho": str(novo_caminho),
                "formato": formato,
                "tentativas": int(download.get("tentativas", 0)),
            }
            if codificacao_origem is not None:
                dados_download["codificacaoOrigem"] = codificacao_origem

            documento["download"] = dados_download

            quantidade_identificada += 1

            print(
                f"Identificado | {novo_caminho.name} | "
                f"formato={formato}"
            )

    return quantidade_identificada


def _nome_seguro(texto: str) -> str:
    caracteres_invalidos = '<>:"/\\|?*'

    for caractere in caracteres_invalidos:
        texto = texto.replace(caractere, "-")

    return texto


def extrair_zip_com_seguranca(
    caminho_zip: Path,
    pasta_destino: Path,
    sufixo_colisao: str,
    max_arquivos: int = 100,
    max_tamanho_total: int = 200 * 1024 * 1024,
) -> list[Path]:
    """
    Descompacta um ZIP com limites de segurança.

    Retorna os caminhos dos arquivos extraídos.
    """

    with zipfile.ZipFile(caminho_zip) as arquivo_zip:
        itens = arquivo_zip.infolist()

        if len(itens) > max_arquivos:
            raise RuntimeError(
                f"ZIP possui {len(itens)} itens; "
                f"limite permitido: {max_arquivos}."
            )

        tamanho_total = sum(
            item.file_size
            for item in itens
        )

        if tamanho_total > max_tamanho_total:
            raise RuntimeError(
                "ZIP excede o limite de tamanho "
                "descompactado permitido."
            )

        pasta_destino.mkdir(
            parents=True,
            exist_ok=True,
        )

        destino_absoluto = pasta_destino.resolve()
        arquivos_extraidos: list[Path] = []

        for item in itens:
            if item.is_dir():
                continue

            modo = item.external_attr >> 16

            if stat.S_ISLNK(modo):
                print(
                    f"Link simbólico ignorado: {item.filename}"
                )
                continue

            caminho_saida = (pasta_destino / item.filename).resolve()

            # Evita arquivos com caminhos como ../../arquivo.txt
            if not str(caminho_saida).startswith(
                str(destino_absoluto)
            ):
                print(
                    f"Caminho inválido ignorado: "
                    f"{item.filename}"
                )
                continue

            caminho_saida.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            # A pasta de destino e' unica para o contrato/contratacao.
            # Preservamos o nome e a hierarquia do ZIP; em colisao real,
            # apenas acrescentamos a origem do documento para nao sobrescrever.
            if caminho_saida.exists():
                caminho_saida = caminho_saida.with_name(
                    f"{caminho_saida.stem} ({sufixo_colisao})"
                    f"{caminho_saida.suffix}"
                )

            with arquivo_zip.open(item) as origem:
                with caminho_saida.open("wb") as destino:
                    shutil.copyfileobj(origem, destino)

            arquivos_extraidos.append(caminho_saida)

    return arquivos_extraidos


def extrair_zips_aninhados_com_seguranca(
    arquivos_iniciais: list[Path],
    sufixo_colisao: str,
    max_profundidade: int = 3,
    max_arquivos_total: int = 500,
    max_tamanho_total: int = 500 * 1024 * 1024,
) -> dict[str, Any]:
    """Expande ZIPs encontrados dentro de outro ZIP ja extraido.

    Os conteudos sao extraidos ao lado do ZIP interno, preservando a estrutura
    original. Os limites sao globais para essa cadeia de ZIPs aninhados.
    """

    pendentes = [(Path(caminho), 1) for caminho in arquivos_iniciais]
    zips_processados: list[str] = []
    arquivos_extraidos: list[Path] = []
    arquivos_office_ignorados: list[dict[str, str]] = []
    avisos: list[str] = []
    caminhos_processados: set[str] = set()
    total_arquivos = 0
    total_tamanho = 0

    while pendentes:
        caminho_zip, profundidade = pendentes.pop(0)
        if not caminho_zip.exists() or not zipfile.is_zipfile(caminho_zip):
            continue

        # DOCX, XLSX, PPTX e ODT tambem sao ZIPs internamente. Eles nao devem
        # ser expandidos aqui: sao documentos finais e terao extratores
        # proprios na etapa de texto/tabelas.
        _, formato = _detectar_formato_arquivo(caminho_zip)
        if formato != "ZIP":
            arquivos_office_ignorados.append(
                {"caminho": str(caminho_zip), "formato": formato}
            )
            continue

        caminho_resolvido = str(caminho_zip.resolve())
        if caminho_resolvido in caminhos_processados:
            continue

        if profundidade > max_profundidade:
            avisos.append(
                f"limite_de_profundidade | arquivo={caminho_zip}"
            )
            continue

        try:
            with zipfile.ZipFile(caminho_zip) as arquivo_zip:
                itens = [
                    item for item in arquivo_zip.infolist()
                    if not item.is_dir()
                ]
                quantidade_itens = len(itens)
                tamanho_itens = sum(item.file_size for item in itens)
        except (OSError, zipfile.BadZipFile) as exc:
            avisos.append(
                f"zip_interno_invalido | arquivo={caminho_zip} | {exc}")
            continue

        if total_arquivos + quantidade_itens > max_arquivos_total:
            avisos.append(
                f"limite_total_de_arquivos | arquivo={caminho_zip}"
            )
            continue

        if total_tamanho + tamanho_itens > max_tamanho_total:
            avisos.append(
                f"limite_total_de_tamanho | arquivo={caminho_zip}"
            )
            continue

        try:
            novos_arquivos = extrair_zip_com_seguranca(
                caminho_zip=caminho_zip,
                pasta_destino=caminho_zip.parent,
                sufixo_colisao=sufixo_colisao,
                max_arquivos=max_arquivos_total - total_arquivos,
                max_tamanho_total=max_tamanho_total - total_tamanho,
            )
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            avisos.append(
                f"erro_ao_extrair_zip_interno | arquivo={caminho_zip} | {exc}")
            continue

        caminhos_processados.add(caminho_resolvido)
        zips_processados.append(str(caminho_zip))
        arquivos_extraidos.extend(novos_arquivos)
        total_arquivos += quantidade_itens
        total_tamanho += tamanho_itens

        pendentes.extend(
            (novo_arquivo, profundidade + 1)
            for novo_arquivo in novos_arquivos
        )

    return {
        "arquivos": arquivos_extraidos,
        "zipsProcessados": zips_processados,
        "arquivosOfficeIgnorados": arquivos_office_ignorados,
        "avisos": avisos,
    }


def extrair_zips_do_manifesto(
    manifesto: list[dict[str, Any]],
    pasta_base: Path | str,
) -> int:
    quantidade_extraida = 0
    pasta_base = Path(pasta_base)

    for contrato in manifesto:
        numero_controle = contrato.get(
            "numeroControlePNCP"
        )

        if not numero_controle:
            continue

        for documento in contrato.get("documentos") or []:
            download = documento.get("download") or {}

            if download.get("formato") != "ZIP":
                continue

            caminho_zip = Path(
                download.get("caminho", "")
            )

            if not caminho_zip.exists():
                print(
                    f"ZIP não encontrado: {caminho_zip}"
                )
                continue

            sequencial = str(documento.get(
                "sequencialDocumento", "sem_sequencial"))

            # Uma pasta por contrato/contratacao. Nenhuma pasta artificial e'
            # criada por documento; somente a hierarquia que ja vier no ZIP.
            pasta_destino = pasta_base / _nome_seguro(numero_controle)
            extracao_anterior = documento.get("extracao") or {}
            arquivos = [
                Path(caminho)
                for caminho in extracao_anterior.get("arquivos") or []
                if Path(caminho).exists()
            ]

            if extracao_anterior.get("status") != "extraido":
                try:
                    arquivos = extrair_zip_com_seguranca(
                        caminho_zip=caminho_zip,
                        pasta_destino=pasta_destino,
                        sufixo_colisao=f"documento {sequencial}",
                    )
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    documento["extracao"] = {
                        "status": "erro",
                        "mensagem": str(exc),
                    }

                    print(
                        f"Erro ao extrair ZIP | "
                        f"contrato={numero_controle} | {exc}"
                    )
                    continue

                quantidade_extraida += 1

            # Manifestos antigos podem ja ter o ZIP externo como extraido.
            # Neles, este bloco processa apenas ZIPs internos ainda pendentes.
            aninhados_anteriores = extracao_anterior.get("zipsAninhados") or {}
            if aninhados_anteriores.get("status") == "concluida":
                continue

            resultado_aninhados = extrair_zips_aninhados_com_seguranca(
                arquivos_iniciais=arquivos,
                sufixo_colisao=f"documento {sequencial}",
            )
            arquivos.extend(resultado_aninhados["arquivos"])

            documento["extracao"] = {
                "status": "extraido",
                "caminho": str(pasta_destino),
                "arquivos": [str(arquivo) for arquivo in arquivos],
                "zipsAninhados": {
                    "status": (
                        "concluida"
                        if not resultado_aninhados["avisos"]
                        else "com_avisos"
                    ),
                    "zipsProcessados": resultado_aninhados["zipsProcessados"],
                    "arquivosOfficeIgnorados": resultado_aninhados[
                        "arquivosOfficeIgnorados"
                    ],
                    "avisos": resultado_aninhados["avisos"],
                },
            }

            print(
                f"ZIP extraído | contrato={numero_controle} | "
                f"arquivos={len(arquivos)} | "
                f"zips_aninhados={len(resultado_aninhados['zipsProcessados'])}"
            )

    return quantidade_extraida
