from datetime import date
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any
import re


import requests

from src.base_estruturada.pncp.client import pncp_client
from src.base_estruturada.pncp.utils import (
    url_pura, nome_seguro, extensao_por_content_type, deve_baixar_documento)


# ============================================================
# URLs BASE
# ============================================================

BASE_URL_CONSULTA = (
    "https://pncp.gov.br/api/consulta/v1"
)

BASE_URL_CONTRATACOES = (
    f"{BASE_URL_CONSULTA}/contratacoes/publicacao"
)

BASE_URL_CONTRATOS = (
    f"{BASE_URL_CONSULTA}/contratos"
)

BASE_URL_PROPOSTA = (
    f"{BASE_URL_CONSULTA}/contratacoes/proposta"
)

BASE_URL_PNCP = (
    "https://pncp.gov.br/pncp-api"
)


# ============================================================
# MODALIDADES
# ============================================================

def carregar_modalidades(
    somente_ativas: bool = True,
) -> dict[int, str]:
    """
    Consulta as modalidades de contratação disponíveis no PNCP.

    Retorna:
        dict[int, str]:
            Dicionário no formato:
            {
                codigo_modalidade: nome_modalidade
            }
    """

    url = (
        f"{BASE_URL_PNCP}/v1/modalidades"
    )

    try:
        response = pncp_client.get(url)

        response.raise_for_status()

        dados = response.json()

        modalidades: dict[int, str] = {}

        for item in dados:
            if (
                somente_ativas
                and not item.get(
                    "statusAtivo",
                    False,
                )
            ):
                continue

            codigo = item.get("id")
            nome = item.get("nome")

            if codigo is not None and nome:
                modalidades[codigo] = nome

        return modalidades

    except requests.RequestException as exc:
        print(
            f"Erro ao consultar modalidades: {exc}"
        )

        return {}

    except ValueError:
        print(
            "Resposta inválida do PNCP "
            "ao consultar modalidades."
        )

        return {}

# ============================================================
# FILTRAR DOCUMENTOS
# ============================================================


def carregar_tipos_documento(somente_ativos: bool = True) -> dict[int, str]:
    url = f"{BASE_URL_PNCP}/v1/tipos-documentos"

    params = {"statusAtivo": "true"} if somente_ativos else {}

    r = requests.get(
        url,
        params=params,
        timeout=30,
    )
    r.raise_for_status()

    return {
        int(d["id"]): d["nome"]
        for d in r.json()
    }

# ============================================================
# CONTRATAÇÕES PUBLICADAS
# ============================================================


@dataclass
class ResultadoColetaContratacoes:
    """Resultado da coleta, inclusive páginas que devem ser retentadas."""

    contratacoes: list[dict[str, Any]]
    falhas: list[dict[str, Any]]

    @property
    def paginas_com_erro(self) -> list[int]:
        """Compatibilidade temporária com o formato antigo."""
        return [falha["pagina"] for falha in self.falhas]


def coletar_contratacoes(
    uf: str,
    data_inicial: str,
    data_final: str,
    modalidades: list[int] | None = None,
    tamanho_pagina: int = 50,
    limite_paginas_por_modalidade: int | None = None,
) -> ResultadoColetaContratacoes:
    """
    Consulta contratações publicadas no PNCP.

    A consulta é realizada por modalidade e percorre
    automaticamente as páginas disponíveis.

    `limite_paginas_por_modalidade` serve para testes. Quando
    informado, limita quantas páginas serão consultadas em cada
    modalidade. Com None, a coleta percorre todas as páginas.
    """

    if tamanho_pagina <= 0:
        tamanho_pagina = 50

    if (
        limite_paginas_por_modalidade is not None
        and limite_paginas_por_modalidade <= 0
    ):
        raise ValueError(
            "limite_paginas_por_modalidade deve ser maior que zero."
        )

    uf = uf.upper().strip()

    if modalidades is None:
        modalidades_disponiveis = (
            carregar_modalidades()
        )

        modalidades = list(
            modalidades_disponiveis.keys()
        )

    resultados: list[dict] = []
    falhas: list[dict[str, Any]] = []

    controles_encontrados: set[str] = set()

    def registrar_falha(
        pagina: int,
        modalidade: int,
        motivo: str,
    ) -> None:
        falhas.append(
            {
                "tipo": "contratacao",
                "dataInicial": data_inicial,
                "dataFinal": data_final,
                "pagina": pagina,
                "modalidade": modalidade,
                "motivo": motivo,
                "status": "pendente",
                "tentativasReprocessamento": 0,
            }
        )

    for modalidade in modalidades:
        print(
            f"Coletando modalidade {modalidade}..."
        )

        pagina = 1
        total_paginas: int | None = None

        while total_paginas is None or pagina <= total_paginas:
            if (
                limite_paginas_por_modalidade is not None
                and pagina > limite_paginas_por_modalidade
            ):
                print(
                    f"Limite de {limite_paginas_por_modalidade} "
                    f"página(s) atingido | modalidade={modalidade}"
                )
                break

            params = {
                "dataInicial": data_inicial,
                "dataFinal": data_final,
                "codigoModalidadeContratacao": modalidade,
                "uf": uf,
                "pagina": pagina,
                "tamanhoPagina": tamanho_pagina,
            }

            try:
                response = pncp_client.get(
                    BASE_URL_CONTRATACOES,
                    params=params,
                )

            except requests.exceptions.Timeout:
                print(
                    "Timeout | contratações | "
                    f"modalidade={modalidade} | "
                    f"página={pagina}")

                registrar_falha(pagina, modalidade, "timeout")

                if total_paginas is None:
                    break

                pagina += 1
                continue

            except requests.exceptions.ConnectionError as exc:
                print(
                    "Erro de conexão | contratações | "
                    f"modalidade={modalidade} | "
                    f"página={pagina} | "
                    f"{exc}"
                )
                registrar_falha(pagina, modalidade, "erro_conexao")

                if total_paginas is None:
                    break

                pagina += 1
                continue

            except requests.RequestException as exc:
                print(
                    "Erro HTTP | contratações | "
                    f"modalidade={modalidade} | "
                    f"página={pagina} | "
                    f"{exc}"
                )

                registrar_falha(pagina, modalidade, "erro_requisicao")

                if total_paginas is None:
                    break

                pagina += 1
                continue

            # ------------------------------------------------
            # SEM CONTEÚDO
            # ------------------------------------------------

            if response.status_code == 204:
                print(f"Página {pagina}: sem conteúdo.")
                break

            # ------------------------------------------------
            # RATE LIMIT
            # ------------------------------------------------

            if response.status_code == 429:
                print("PNCP retornou HTTP 429 "
                      "(muitas requisições). "
                      f"Modalidade={modalidade} | "
                      f"página={pagina}")

                registrar_falha(pagina, modalidade, "http_429")

                if total_paginas is None:
                    break

                pagina += 1
                continue

            # ------------------------------------------------
            # OUTROS ERROS
            # ------------------------------------------------

            if response.status_code != 200:
                print(
                    f"Erro HTTP {response.status_code} | "
                    f"contratações | "
                    f"modalidade={modalidade} | "
                    f"página={pagina}"
                )

                registrar_falha(
                    pagina,
                    modalidade,
                    f"http_{response.status_code}",
                )

                if total_paginas is None:
                    break

                pagina += 1
                continue

            # ------------------------------------------------
            # JSON
            # ------------------------------------------------

            try:
                dados = response.json()

            except ValueError:
                print("Resposta inválida do PNCP " "(não é JSON) | "
                      f"modalidade={modalidade} | "
                      f"página={pagina}")

                registrar_falha(pagina, modalidade, "json_invalido")

                if total_paginas is None:
                    break

                pagina += 1
                continue

            if not isinstance(dados, dict):
                print(
                    f"Formato inválido | Contratações | página={pagina}"
                )

                registrar_falha(pagina, modalidade, "formato_invalido")

                if total_paginas is None:
                    break

                pagina += 1
                continue

            registros = (
                dados.get("data")
                or []
            )

            if not isinstance(registros, list):
                print("Campo 'data' inválido | "
                      f"modalidade={modalidade} | "
                      f"página={pagina}")

                registrar_falha(pagina, modalidade, "campo_data_invalido")

                if total_paginas is None:
                    break

                pagina += 1
                continue

            # ----------------------------------------------------
            # DESCOBRE TOTAL DE PÁGINAS
            # ----------------------------------------------------
            total_paginas = dados.get("totalPaginas") or pagina

            print(f"Página {pagina}/{total_paginas} | "
                  f"recebidos={len(registros)}")

            if not registros:
                break
            # ------------------------------------------------
            # REMOVE DUPLICADOS
            # ------------------------------------------------

            novos_registros = 0

            for item in registros:

                numero_controle = (
                    item.get(
                        "numeroControlePNCP"
                    )
                )

                if numero_controle:
                    if (
                        numero_controle
                        in controles_encontrados
                    ):
                        continue

                    controles_encontrados.add(
                        numero_controle
                    )

                resultados.append(item)

                novos_registros += 1

            print(f"Página {pagina}/{total_paginas} | "
                  f"novos={novos_registros} | "
                  f"total acumulado={len(resultados)}")

        # ------------------------------------------------
        # PAGINAÇÃO
        # ------------------------------------------------
            if pagina >= total_paginas:
                break

            pagina += 1
            time.sleep(5)

        time.sleep(5)

    return ResultadoColetaContratacoes(
        contratacoes=resultados,
        falhas=falhas,
    )


def coletar_pagina_contratacoes(
    uf: str,
    data_inicial: str,
    data_final: str,
    modalidade: int,
    pagina: int,
    tamanho_pagina: int = 50,
) -> ResultadoColetaContratacoes:
    """Consulta uma página específica de contratações.

    Usada somente no reprocessamento manual dos checkpoints: não percorre
    outras páginas e mantém os mesmos campos de falha da coleta principal.
    """

    def falha(motivo: str) -> ResultadoColetaContratacoes:
        return ResultadoColetaContratacoes(
            contratacoes=[],
            falhas=[
                {
                    "tipo": "contratacao",
                    "dataInicial": data_inicial,
                    "dataFinal": data_final,
                    "pagina": pagina,
                    "modalidade": modalidade,
                    "motivo": motivo,
                    "status": "pendente",
                    "tentativasReprocessamento": 0,
                }
            ],
        )

    params = {
        "dataInicial": data_inicial,
        "dataFinal": data_final,
        "codigoModalidadeContratacao": modalidade,
        "uf": uf.upper().strip(),
        "pagina": pagina,
        "tamanhoPagina": tamanho_pagina,
    }

    try:
        response = pncp_client.get(BASE_URL_CONTRATACOES, params=params)
    except requests.exceptions.Timeout:
        return falha("timeout")
    except requests.exceptions.ConnectionError:
        return falha("erro_conexao")
    except requests.RequestException:
        return falha("erro_requisicao")

    if response.status_code == 204:
        return ResultadoColetaContratacoes(contratacoes=[], falhas=[])
    if response.status_code == 429:
        return falha("http_429")
    if response.status_code != 200:
        return falha(f"http_{response.status_code}")

    try:
        dados = response.json()
    except ValueError:
        return falha("json_invalido")

    if not isinstance(dados, dict):
        return falha("formato_invalido")

    registros = dados.get("data") or []
    if not isinstance(registros, list):
        return falha("campo_data_invalido")

    return ResultadoColetaContratacoes(
        contratacoes=[item for item in registros if isinstance(item, dict)],
        falhas=[],
    )


def consultar_detalhe_contratacao(
    contratacao: dict,
) -> dict:
    """
    Consulta os detalhes de uma contratação específica.
    """

    orgao = contratacao.get("orgaoEntidade") or {}

    cnpj = orgao.get("cnpj")
    ano = contratacao.get("anoCompra")
    sequencial = contratacao.get("sequencialCompra")

    if not cnpj or ano is None or sequencial is None:
        print(
            "Contratação sem cnpj, anoCompra ou "
            "sequencialCompra."
        )
        return {}

    url = (
        f"{BASE_URL_CONSULTA}/orgaos/"
        f"{cnpj}/compras/{ano}/{sequencial}"
    )

    print(url)

    try:
        response = pncp_client.get(url)

    except requests.RequestException as exc:
        print(
            f"Erro ao consultar detalhe da contratação: {exc}"
        )
        return {}

    if response.status_code == 204:
        return {}

    if response.status_code != 200:
        print(
            f"Erro HTTP {response.status_code} | "
            "detalhe da contratação"
        )
        return {}

    try:
        dados = response.json()

        if isinstance(dados, dict):
            return dados

        return {}

    except ValueError:
        print("Resposta inválida no detalhe da contratação.")
        return {}


def consultar_documentos_contratacao(
    contratacao: dict,
) -> list[dict]:
    """
    Lista documentos de uma contratação.
    Não faz download.
    """

    orgao = contratacao.get("orgaoEntidade") or {}

    cnpj = orgao.get("cnpj")
    ano = contratacao.get("anoCompra")
    sequencial = contratacao.get("sequencialCompra")

    if not cnpj or ano is None or sequencial is None:
        print(
            "Contratação sem cnpj, anoCompra ou "
            "sequencialCompra."
        )
        return []

    url = (
        f"{BASE_URL_PNCP}/v1/orgaos/"
        f"{cnpj}/compras/{ano}/{sequencial}/arquivos"
    )

    try:
        response = pncp_client.get(url)

    except requests.RequestException as exc:
        print(
            f"Erro ao listar documentos da contratação "
            f"{contratacao.get('numeroControlePNCP')}: {exc}"
        )
        return []

    if response.status_code == 204:
        return []

    if response.status_code != 200:
        print(
            f"Erro HTTP {response.status_code} | "
            f"documentos da contratação "
            f"{contratacao.get('numeroControlePNCP')}"
        )
        return []

    try:
        dados = response.json()

    except ValueError:
        print("Resposta de documentos não é JSON válido.")
        return []

    if isinstance(dados, list):
        return dados

    if isinstance(dados, dict):
        return (
            dados.get("documentos")
            or dados.get("data")
            or []
        )

    return []


@dataclass
class ResultadoListaDocumentos:
    """Resultado da listagem de documentos de um único item do PNCP."""

    documentos: list[dict[str, Any]]
    erro: str | None = None


def consultar_documentos_contratacao_detalhado(
    contratacao: dict[str, Any],
) -> ResultadoListaDocumentos:
    """Lista documentos de contratação preservando o motivo de falha."""

    orgao = contratacao.get("orgaoEntidade") or {}
    cnpj = orgao.get("cnpj")
    ano = contratacao.get("anoCompra")
    sequencial = contratacao.get("sequencialCompra")

    if not cnpj or ano is None or sequencial is None:
        return ResultadoListaDocumentos([], "identificador_incompleto")

    url = f"{BASE_URL_PNCP}/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos"

    try:
        response = pncp_client.get(url)
    except requests.exceptions.Timeout:
        return ResultadoListaDocumentos([], "timeout")
    except requests.exceptions.ConnectionError:
        return ResultadoListaDocumentos([], "erro_conexao")
    except requests.RequestException:
        return ResultadoListaDocumentos([], "erro_requisicao")

    if response.status_code == 204:
        return ResultadoListaDocumentos([])
    if response.status_code == 429:
        return ResultadoListaDocumentos([], "http_429")
    if response.status_code != 200:
        return ResultadoListaDocumentos([], f"http_{response.status_code}")

    try:
        dados = response.json()
    except ValueError:
        return ResultadoListaDocumentos([], "json_invalido")

    if isinstance(dados, list):
        return ResultadoListaDocumentos(
            [item for item in dados if isinstance(item, dict)]
        )
    if isinstance(dados, dict):
        documentos = dados.get("documentos") or dados.get("data") or []
        if isinstance(documentos, list):
            return ResultadoListaDocumentos(
                [item for item in documentos if isinstance(item, dict)]
            )

    return ResultadoListaDocumentos([], "formato_invalido")


# ============================================================
# CONTRATOS
# ============================================================


@dataclass
class ResultadoColetaContratos:
    """Resultado da coleta, inclusive páginas que devem ser retentadas."""

    contratos: list[dict[str, Any]]
    falhas: list[dict[str, Any]]

    @property
    def paginas_com_erro(self) -> list[int]:
        """Compatibilidade temporária com o formato antigo."""
        return [falha["pagina"] for falha in self.falhas]


def coletar_contratos(
    data_inicial: str,
    data_final: str,
    tamanho_pagina: int = 50,
    limite_paginas: int | None = None,
) -> ResultadoColetaContratos:
    """
    Consulta contratos publicados no PNCP.

    `limite_paginas` serve para testes. Quando informado, limita
    quantas páginas serão consultadas. Com None, a coleta percorre
    todas as páginas.
    """

    if tamanho_pagina <= 0:
        tamanho_pagina = 50

    if limite_paginas is not None and limite_paginas <= 0:
        raise ValueError(
            "limite_paginas deve ser maior que zero."
        )

    resultados: list[dict[str, Any]] = []
    falhas: list[dict[str, Any]] = []

    controles_encontrados: set[str] = set()

    def registrar_falha(
        pagina: int,
        motivo: str,
    ) -> None:
        falhas.append(
            {
                "tipo": "contrato",
                "dataInicial": data_inicial,
                "dataFinal": data_final,
                "pagina": pagina,
                "motivo": motivo,
                "status": "pendente",
                "tentativasReprocessamento": 0,
            }
        )

    pagina = 1
    total_paginas: int | None = None

    while total_paginas is None or pagina <= total_paginas:
        if limite_paginas is not None and pagina > limite_paginas:
            print(
                f"Limite de {limite_paginas} página(s) atingido "
                "na coleta de contratos."
            )
            break

        params = {
            "dataInicial": data_inicial,
            "dataFinal": data_final,
            "pagina": pagina,
            "tamanhoPagina": tamanho_pagina,
        }

        try:
            response = pncp_client.get(
                BASE_URL_CONTRATOS,
                params=params,
            )

        except requests.exceptions.Timeout:
            print(
                "Timeout | contratos | "
                f"página={pagina}"
            )

            registrar_falha(pagina, "timeout")

            if total_paginas is None:
                break

            pagina += 1
            continue

        except requests.exceptions.ConnectionError as exc:
            print(
                "Erro de conexão | contratos | "
                f"página={pagina} | {exc}"
            )

            registrar_falha(pagina, "erro_conexao")

            if total_paginas is None:
                break

            pagina += 1
            continue

        except requests.RequestException as exc:
            print(
                "Erro HTTP | contratos | "
                f"página={pagina} | {exc}"
            )

            registrar_falha(pagina, "erro_requisicao")

            if total_paginas is None:
                break

            pagina += 1
            continue

        if response.status_code == 204:
            break

        if response.status_code == 429:
            print(
                f"PNCP continuou retornando HTTP 429 "
                f"na página {pagina} após as tentativas automáticas."
            )

            registrar_falha(pagina, "http_429")

            if total_paginas is None:
                break

            pagina += 1
            continue

        if response.status_code != 200:
            print(
                f"Erro {response.status_code} | "
                f"contratos | página={pagina}"
            )
            print(response.text[:500])

            registrar_falha(
                pagina,
                f"http_{response.status_code}",
            )

            if total_paginas is None:
                break

            pagina += 1
            continue

        try:
            dados = response.json()

        except ValueError:
            print(
                "Resposta inválida do PNCP "
                "ao consultar contratos."
            )

            registrar_falha(pagina, "json_invalido")

            if total_paginas is None:
                break

            pagina += 1
            continue

        if not isinstance(dados, dict):
            print(
                f"Formato inválido | contratos | página={pagina}"
            )

            registrar_falha(pagina, "formato_invalido")

            if total_paginas is None:
                break

            pagina += 1
            continue

        registros = (
            dados.get("data")
            or []
        )

        if not isinstance(registros, list):
            print(
                f"Campo 'data' inválido | contratos | página={pagina}"
            )

            registrar_falha(pagina, "campo_data_invalido")

            if total_paginas is None:
                break

            pagina += 1
            continue

        total_paginas = dados.get("totalPaginas") or pagina

        if not registros:
            print(
                f"Página {pagina}/{total_paginas} | recebidos=0"
            )

            if pagina >= total_paginas:
                break

            pagina += 1
            time.sleep(5)
            continue

        # ----------------------------------------------------
        # FILTRAGEM LOCAL DA UF E REMOÇÃO DE DUPLICADOS
        # ----------------------------------------------------
        for item in registros:
            # Captura a UF do órgão emissor do contrato
            unidade_orgao = item.get("unidadeOrgao") or {}
            uf_orgao = str(
                unidade_orgao.get("ufSigla") or ""
            ).upper()
            # Se NÃO for do Rio de Janeiro, pula para o próximo item
            if uf_orgao != "RJ":
                continue

            numero_controle = item.get("numeroControlePNCP")

            if numero_controle:
                if numero_controle in controles_encontrados:
                    continue
                controles_encontrados.add(numero_controle)

            resultados.append(item)

        print(
            f"Página {pagina}/{total_paginas} | "
            f"recebidos={len(registros)} | "
            f"RJ acumulados={len(resultados)}"
        )

        if pagina >= total_paginas:
            break

        pagina += 1

        # Pausa entre páginas bem-sucedidas para reduzir HTTP 429.
        time.sleep(5)

    return ResultadoColetaContratos(
        contratos=resultados,
        falhas=falhas,
    )


def coletar_pagina_contratos(
    data_inicial: str,
    data_final: str,
    pagina: int,
    tamanho_pagina: int = 500,
) -> ResultadoColetaContratos:
    """Consulta uma página específica de contratos para reprocessamento."""

    def falha(motivo: str) -> ResultadoColetaContratos:
        return ResultadoColetaContratos(
            contratos=[],
            falhas=[
                {
                    "tipo": "contrato",
                    "dataInicial": data_inicial,
                    "dataFinal": data_final,
                    "pagina": pagina,
                    "motivo": motivo,
                    "status": "pendente",
                    "tentativasReprocessamento": 0,
                }
            ],
        )

    params = {
        "dataInicial": data_inicial,
        "dataFinal": data_final,
        "pagina": pagina,
        "tamanhoPagina": tamanho_pagina,
    }

    try:
        response = pncp_client.get(BASE_URL_CONTRATOS, params=params)
    except requests.exceptions.Timeout:
        return falha("timeout")
    except requests.exceptions.ConnectionError:
        return falha("erro_conexao")
    except requests.RequestException:
        return falha("erro_requisicao")

    if response.status_code == 204:
        return ResultadoColetaContratos(contratos=[], falhas=[])
    if response.status_code == 429:
        return falha("http_429")
    if response.status_code != 200:
        return falha(f"http_{response.status_code}")

    try:
        dados = response.json()
    except ValueError:
        return falha("json_invalido")

    if not isinstance(dados, dict):
        return falha("formato_invalido")

    registros = dados.get("data") or []
    if not isinstance(registros, list):
        return falha("campo_data_invalido")

    contratos_rj: list[dict[str, Any]] = []
    for item in registros:
        if not isinstance(item, dict):
            continue

        unidade_orgao = item.get("unidadeOrgao") or {}
        uf_orgao = str(unidade_orgao.get("ufSigla") or "").upper()
        if uf_orgao == "RJ":
            contratos_rj.append(item)

    return ResultadoColetaContratos(contratos=contratos_rj, falhas=[])


# ============================================================
# CONTRATAÇÕES COM PROPOSTAS ABERTAS
# ============================================================

def coletar_contratacoes_abertas(
    data_final: str | None = None,
    uf: str = "RJ",
    pagina: int = 1,
) -> dict:
    """
    Consulta contratações com período de recebimento
    de propostas aberto.
    """

    if data_final is None:
        data_final = (
            date.today().strftime(
                "%Y%m%d"
            )
        )

    params = {
        "dataFinal": data_final,
        "uf": uf,
        "pagina": pagina,
    }

    try:
        response = pncp_client.get(
            BASE_URL_PROPOSTA,
            params=params,
        )

    except requests.exceptions.Timeout:
        print(
            "Timeout ao consultar "
            "contratações abertas."
        )

        return {}

    except requests.exceptions.ConnectionError as exc:
        print(
            "Erro de conexão ao consultar "
            f"contratações abertas: {exc}"
        )

        return {}

    except requests.RequestException as exc:
        print(
            "Erro ao consultar "
            f"contratações abertas: {exc}"
        )

        return {}

    if response.status_code == 204:
        return {}

    if response.status_code == 429:
        print(
            "PNCP retornou HTTP 429 "
            "ao consultar contratações abertas."
        )

        return {}

    if response.status_code != 200:
        print(
            f"Erro {response.status_code} "
            "ao consultar contratações abertas."
        )

        try:
            print(response.text)
        except Exception:
            pass

        return {}

    try:
        dados = response.json()

        if isinstance(dados, dict):
            return dados

        return {}

    except ValueError:
        print(
            "Resposta inválida do PNCP "
            "ao consultar contratações abertas."
        )

        return {}


# ============================================================
# ATAS DA COMPRA
# ============================================================

def consultar_atas_compra(
    cnpj: str,
    ano_compra: int,
    sequencial_compra: int,
) -> list[dict]:
    """
    Consulta as Atas de Registro de Preços
    associadas a uma compra.
    """

    url = (
        f"{BASE_URL_PNCP}/v1/orgaos/"
        f"{cnpj}/compras/"
        f"{ano_compra}/"
        f"{sequencial_compra}/atas"
    )

    try:
        response = pncp_client.get(url)

        if response.status_code == 204:
            return []

        if response.status_code == 429:
            print(
                "PNCP retornou HTTP 429 "
                "ao consultar atas."
            )

            return []

        if response.status_code != 200:
            print(
                f"Erro {response.status_code} "
                "ao consultar atas."
            )

            try:
                print(response.text)
            except Exception:
                pass

            return []

        dados = response.json()

        # O endpoint real pode retornar
        # diretamente uma lista.
        if isinstance(dados, list):
            return dados

        # Compatibilidade caso o PNCP
        # retorne um objeto com "data".
        if isinstance(dados, dict):
            return (
                dados.get("data")
                or []
            )

        return []

    except requests.RequestException as exc:
        print(
            f"Erro ao consultar atas: {exc}"
        )

        return []

    except ValueError:
        print(
            "Resposta inválida do PNCP "
            "ao consultar atas."
        )

        return []


# ============================================================
# QUANTIDADE DE DOCUMENTOS DA ATA
# ============================================================

def consultar_quantidade_documentos_ata(
    cnpj: str,
    ano_compra: int,
    sequencial_compra: int,
    sequencial_ata: int,
) -> int:
    """
    Consulta a quantidade de documentos existentes
    em uma Ata de Registro de Preços.
    """

    url = (
        f"{BASE_URL_PNCP}/v1/orgaos/"
        f"{cnpj}/compras/"
        f"{ano_compra}/"
        f"{sequencial_compra}/atas/"
        f"{sequencial_ata}/"
        "arquivos/quantidade"
    )

    try:
        response = pncp_client.get(url)

        if response.status_code == 204:
            return 0

        if response.status_code == 429:
            print(
                "PNCP retornou HTTP 429 "
                "ao consultar quantidade "
                "de documentos."
            )

            return 0

        if response.status_code != 200:
            print(
                f"Erro {response.status_code} "
                "ao consultar quantidade "
                "de documentos."
            )

            try:
                print(response.text)
            except Exception:
                pass

            return 0

        dados = response.json()

        # Resposta observada no endpoint:
        #
        # 4

        if isinstance(dados, int):
            return dados

        # Compatibilidade caso a API retorne:
        #
        # {"quantidade": 4}

        if isinstance(dados, dict):
            quantidade = (
                dados.get("quantidade")
            )

            if quantidade is not None:
                return int(quantidade)

        return 0

    except requests.RequestException as exc:
        print(
            "Erro ao consultar quantidade "
            f"de documentos: {exc}"
        )

        return 0

    except (
        ValueError,
        TypeError,
    ):
        print(
            "Resposta inválida ao consultar "
            "quantidade de documentos."
        )

        return 0


# ============================================================
# DOCUMENTOS DA ATA
# ============================================================

def consultar_documentos_ata(
    cnpj: str,
    ano_compra: int,
    sequencial_compra: int,
    sequencial_ata: int,
) -> list[dict]:
    """
    Lista os documentos associados a uma Ata.
    """

    url = (
        f"{BASE_URL_PNCP}/v1/orgaos/"
        f"{cnpj}/compras/"
        f"{ano_compra}/"
        f"{sequencial_compra}/atas/"
        f"{sequencial_ata}/arquivos"
    )

    try:
        response = pncp_client.get(url)

        if response.status_code == 204:
            return []

        if response.status_code == 429:
            print(
                "PNCP retornou HTTP 429 "
                "ao consultar documentos da ata."
            )

            return []

        if response.status_code != 200:
            print(
                f"Erro {response.status_code} "
                "ao consultar documentos da ata."
            )

            try:
                print(response.text)
            except Exception:
                pass

            return []

        dados = response.json()

        # O endpoint real retorna diretamente uma lista.
        if isinstance(dados, list):
            return dados

        # Compatibilidade com eventual
        # resposta no formato {"data": [...]}.
        if isinstance(dados, dict):
            return (
                dados.get("data")
                or []
            )

        return []

    except requests.RequestException as exc:
        print(
            "Erro ao consultar documentos "
            f"da ata: {exc}"
        )

        return []

    except ValueError:
        print(
            "Resposta inválida do PNCP "
            "ao consultar documentos da ata."
        )

        return []


# ============================================================
# DOWNLOAD DE UM DOCUMENTO
# ============================================================

def baixar_documento_ata(
    cnpj: str,
    ano_compra: int,
    sequencial_compra: int,
    sequencial_ata: int,
    sequencial_documento: int,
    destino: str | Path,
    url_documento: str | None = None,
) -> Path | None:
    """
    Baixa um documento associado a uma Ata do PNCP.

    Para downloads utilizamos requests diretamente,
    pois este endpoint entrega conteúdo binário e não JSON.

    A URL retornada pelo próprio PNCP tem prioridade.
    """

    destino = Path(destino)

    # --------------------------------------------------------
    # DEFINE A URL
    # --------------------------------------------------------

    if url_documento:
        url = url_documento.strip()

    else:
        url = (
            "https://pncp.gov.br/"
            "pncp-api/v1/orgaos/"
            f"{cnpj}/compras/"
            f"{ano_compra}/"
            f"{sequencial_compra}/atas/"
            f"{sequencial_ata}/arquivos/"
            f"{sequencial_documento}"
        )

    try:
        # ----------------------------------------------------
        # DOWNLOAD BINÁRIO
        # ----------------------------------------------------

        response = requests.get(
            url,
            stream=True,
            timeout=120,
            headers={
                "Accept": "application/pdf,*/*",
                "User-Agent": "Mozilla/5.0",
            },
        )

        # ----------------------------------------------------
        # ERRO HTTP
        # ----------------------------------------------------

        if response.status_code != 200:
            print(
                f"Erro {response.status_code} "
                "ao baixar documento."
            )

            try:
                print(
                    "Resposta do PNCP:",
                    response.text[:1000],
                )
            except Exception:
                pass

            return None

        # ----------------------------------------------------
        # INFORMAÇÕES DA RESPOSTA
        # ----------------------------------------------------

        content_type = (
            response.headers.get(
                "Content-Type",
                "",
            )
        )

        print(
            f"Content-Type recebido: "
            f"{content_type}"
        )

        # ----------------------------------------------------
        # CRIA DIRETÓRIO
        # ----------------------------------------------------

        destino.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # SALVA O ARQUIVO
        # ----------------------------------------------------

        with destino.open("wb") as arquivo:
            for bloco in response.iter_content(
                chunk_size=8192
            ):
                if bloco:
                    arquivo.write(bloco)

        # ----------------------------------------------------
        # VALIDA ARQUIVO
        # ----------------------------------------------------

        if not destino.exists():
            print(
                "O arquivo não foi criado."
            )

            return None

        tamanho = destino.stat().st_size

        if tamanho == 0:
            print(
                "O arquivo recebido está vazio."
            )

            destino.unlink(
                missing_ok=True
            )

            return None

        print(
            f"Arquivo recebido: "
            f"{tamanho} bytes"
        )

        return destino

    except requests.exceptions.Timeout:
        print(
            "Timeout durante o download "
            "do documento."
        )

        return None

    except requests.exceptions.ConnectionError as exc:
        print(
            "Erro de conexão durante "
            f"o download: {exc}"
        )

        return None

    except requests.RequestException as exc:
        print(
            f"Erro ao baixar documento: {exc}"
        )

        return None

    except OSError as exc:
        print(
            f"Erro ao salvar documento: {exc}"
        )

        return None


# ============================================================
# DOWNLOAD DE TODOS OS DOCUMENTOS DA ATA
# ============================================================

def baixar_todos_documentos_ata(
    cnpj: str,
    ano_compra: int,
    sequencial_compra: int,
    sequencial_ata: int,
    pasta_destino: str | Path = "downloads_pncp",
) -> list[Path]:
    """
    Lista e baixa todos os documentos de uma Ata.

    Retorna uma lista contendo os caminhos dos arquivos
    que foram baixados com sucesso.
    """

    documentos = consultar_documentos_ata(
        cnpj=cnpj,
        ano_compra=ano_compra,
        sequencial_compra=sequencial_compra,
        sequencial_ata=sequencial_ata,
    )

    pasta_destino = Path(
        pasta_destino
    )

    pasta_destino.mkdir(
        parents=True,
        exist_ok=True,
    )

    arquivos_baixados: list[Path] = []

    for documento in documentos:
        sequencial_documento = (
            documento.get(
                "sequencialDocumento"
            )
        )

        url_documento = (
            documento.get("url")
        )

        if sequencial_documento is None:
            print(
                "Documento sem "
                "sequencialDocumento. Ignorando."
            )

            continue

        destino = (
            pasta_destino
            / (
                f"ata_{sequencial_ata}"
                f"_documento_"
                f"{sequencial_documento}.pdf"
            )
        )

        caminho = baixar_documento_ata(
            cnpj=cnpj,
            ano_compra=ano_compra,
            sequencial_compra=sequencial_compra,
            sequencial_ata=sequencial_ata,
            sequencial_documento=(
                sequencial_documento
            ),
            destino=destino,
            url_documento=url_documento,
        )

        if caminho:
            arquivos_baixados.append(
                caminho
            )

    return arquivos_baixados


# ============================================================
# CONSULTAR DOCUMENTOS DO CONTRATO
# ============================================================

def consultar_documentos_contrato(
        contrato: dict
) -> list[dict]:
    """
    Lista os documentos vinculados a um contrato.
    Não faz download.
    """

    orgao_entidade = contrato.get("orgaoEntidade") or {}

    cnpj = orgao_entidade.get("cnpj")
    ano = contrato.get("anoContrato")
    sequencial = contrato.get("sequencialContrato")

    if not cnpj or ano is None or sequencial is None:
        print(
            "Contrato sem cnpj, anoContrato ou "
            "sequencialContrato. Ignorando."
        )
        return []
    url = (
        f"{BASE_URL_PNCP}/v1/orgaos/"
        f"{cnpj}/contratos/"
        f"{ano}/{sequencial}/arquivos"
    )

    try:
        response = pncp_client.get(url)

    except requests.RequestException as exc:
        print(
            f"Erro ao listar documentos do contrato "
            f"{contrato.get('numeroControlePNCP')}: {exc}"
        )
        return []

    if response.status_code == 204:
        return []

    if response.status_code != 200:
        print(
            f"Erro HTTP {response.status_code} ao listar "
            f"documentos do contrato "
            f"{contrato.get('numeroControlePNCP')}"
        )
        return []
    try:
        dados = response.json()
    except ValueError:
        print("Resposta de documentos não é um JSON válido.")
        return []

    if isinstance(dados, list):
        return dados

    if isinstance(dados, dict):
        return (
            dados.get("documentos")
            or dados.get("data")
            or []
        )

    return []


def consultar_documentos_contrato_detalhado(
    contrato: dict[str, Any],
) -> ResultadoListaDocumentos:
    """Lista documentos de contrato preservando o motivo de falha."""

    orgao = contrato.get("orgaoEntidade") or {}
    cnpj = orgao.get("cnpj")
    ano = contrato.get("anoContrato")
    sequencial = contrato.get("sequencialContrato")

    if not cnpj or ano is None or sequencial is None:
        return ResultadoListaDocumentos([], "identificador_incompleto")

    url = f"{BASE_URL_PNCP}/v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/arquivos"

    try:
        response = pncp_client.get(url)
    except requests.exceptions.Timeout:
        return ResultadoListaDocumentos([], "timeout")
    except requests.exceptions.ConnectionError:
        return ResultadoListaDocumentos([], "erro_conexao")
    except requests.RequestException:
        return ResultadoListaDocumentos([], "erro_requisicao")

    if response.status_code == 204:
        return ResultadoListaDocumentos([])
    if response.status_code == 429:
        return ResultadoListaDocumentos([], "http_429")
    if response.status_code != 200:
        return ResultadoListaDocumentos([], f"http_{response.status_code}")

    try:
        dados = response.json()
    except ValueError:
        return ResultadoListaDocumentos([], "json_invalido")

    if isinstance(dados, list):
        return ResultadoListaDocumentos(
            [item for item in dados if isinstance(item, dict)]
        )
    if isinstance(dados, dict):
        documentos = dados.get("documentos") or dados.get("data") or []
        if isinstance(documentos, list):
            return ResultadoListaDocumentos(
                [item for item in documentos if isinstance(item, dict)]
            )

    return ResultadoListaDocumentos([], "formato_invalido")


# ============================================================
# BAIXAR DOCUMENTOS DO CONTRATO
# ============================================================


def baixar_documentos(
    numero_controle: str,
    documento: dict[str, Any],
    pasta_base: Path | str,

) -> Path | None:
    """
    Baixa um documento do manifesto e preserva seu formato original.
    """

    url = documento.get("url") or documento.get("uri")

    if not url:
        print("Documento sem URL. Ignorando.")
        return None

    url = url_pura(str(url))

    titulo_documento = documento.get("titulo")
    sequencial = documento.get("sequencialDocumento", "sem_sequencial")

    pasta_contrato = (
        Path(pasta_base) / nome_seguro(numero_controle)
    )

    try:
        response = pncp_client.get(
            url,
            stream=True,
            timeout=120
        )

        if response.status_code != 200:
            print(
                f"Erro HTTP {response.status_code} | "
                f"download | contrato={numero_controle}"
            )
            return None

        content_type = response.headers.get(
            "Content-Type",
            "",
        )

        extensao = extensao_por_content_type(
            content_type,
            url,
        )

        # ``titulo`` e' o nome informado pelo PNCP. Ele e' preservado sempre
        # que for possivel; somente caracteres proibidos no Windows sao
        # alterados. O sequencial nao vira prefixo do arquivo.
        if titulo_documento:
            nome_original = str(titulo_documento).strip()
            nome_final = nome_seguro(nome_original)
        else:
            nome_final = f"documento_{sequencial}"

        if not Path(nome_final).suffix:
            nome_final = f"{nome_final}{extensao}"

        destino = pasta_contrato / nome_final

        # Dois documentos de uma mesma entidade podem ter o mesmo titulo.
        # So nesse caso acrescentamos o sequencial, sem perder o nome original.
        if destino.exists():
            nome_base = destino.stem
            destino = destino.with_name(
                f"{nome_base} (documento {sequencial}){destino.suffix}"
            )

        temporario = destino.with_name(
            f"{destino.name}.part"
        )

        pasta_contrato.mkdir(
            parents=True,
            exist_ok=True,
        )

        with temporario.open("wb") as arquivo:
            for bloco in response.iter_content(
                chunk_size=82192
            ):
                if bloco:
                    arquivo.write(bloco)

        response.close()

        if temporario.stat().st_size == 0:
            temporario.unlink(missing_ok=True)

            print(
                f"Arquivo vazio | contrato={numero_controle}"
            )
            return None

        temporario.replace(destino)

        print(
            f"Baixado | contrato={numero_controle} | "
            f"arquivo={destino.name} | "
            f"tipo={content_type}"
        )

        return destino
    except requests.RequestException as exc:

        print(
            f"Erro no download | contrato={numero_controle} | "
            f"{exc}"
        )
        return None

    except OSError as exc:
        print(
            f"Erro ao salvar arquivo | contrato={numero_controle} | "
            f"{exc}"
        )
        return None


def baixar_documentos_lista(
    manifesto: list[dict[str, Any]],
    pasta_base: str,
    limite_contratos: int | None = 5,
    limite_documentos_por_contrato: int | None = 1,
    somente_tipos_prioritarios: bool = False,
    tipos_permitidos: set[str] | None = None,
) -> int:

    tipos_por_id = carregar_tipos_documento()

    quantidade_baixada = 0

    entidades_processadas = 0

    for contrato in manifesto:
        if (
            limite_contratos is not None
            and entidades_processadas >= limite_contratos
        ):
            break

        numero_controle = contrato.get("numeroControlePNCP")

        if not numero_controle:
            continue

        documentos = contrato.get("documentos") or []

        documentos_selecionados = []
        for documento in documentos:
            # ``tipos_permitidos`` tem precedencia para permitir campanhas
            # pequenas sem alterar a lista global de prioridades do projeto.
            aplicar_filtro = somente_tipos_prioritarios or tipos_permitidos is not None
            if aplicar_filtro and not deve_baixar_documento(
                documento,
                tipos_por_id,
                tipos_permitidos=tipos_permitidos,
            ):
                continue

            download = documento.get("download") or {}
            caminho_existente = Path(download.get("caminho", ""))
            if (
                download.get("status") in {"baixado", "identificado"}
                and caminho_existente.exists()
            ):
                continue

            documentos_selecionados.append(documento)

        if not documentos_selecionados:
            continue

        entidades_processadas += 1
        if limite_documentos_por_contrato is not None:
            documentos_selecionados = documentos_selecionados[
                :limite_documentos_por_contrato
            ]

        for documento in documentos_selecionados:

            caminho = baixar_documentos(
                numero_controle=numero_controle,
                documento=documento,
                pasta_base=pasta_base,
            )

            if caminho:
                download_anterior = documento.get("download") or {}
                documento["download"] = {
                    "status": "baixado",
                    "caminho": str(caminho),
                    "tentativas": int(download_anterior.get("tentativas", 0)) + 1,
                }

                quantidade_baixada += 1
            else:
                download_anterior = documento.get("download") or {}
                documento["download"] = {
                    "status": "erro",
                    "tentativas": int(download_anterior.get("tentativas", 0)) + 1,
                    "ultimoErro": "falha_no_download",
                }

    return quantidade_baixada
