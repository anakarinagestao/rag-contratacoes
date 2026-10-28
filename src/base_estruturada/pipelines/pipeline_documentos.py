"""Pipeline mensal do PNCP.

O fluxo ativo coleta contratos e contratações e os salva por ano/mês.
A etapa de documentos permanece disponível, mas não é executada aqui.
"""

from calendar import monthrange
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.base_estruturada.pncp.repository import (
    adicionar_registros_novos,
    carregar_checkpoint_documentos,
    carregar_json,
    extrair_zips_do_manifesto,
    identificar_arquivos_do_manifesto,
    atualizar_falha_reprocessada,
    salvar_falhas_coleta,
    salvar_json,
    materializar_manifesto_com_checkpoint,
    salvar_estado_documentos,
    salvar_lista_de_documentos_contratos,
    salvar_lista_documentos_contratacao,
)
from src.base_estruturada.pncp.service import (
    baixar_documentos_lista,
    coletar_pagina_contratacoes,
    coletar_pagina_contratos,
    coletar_contratacoes,
    coletar_contratos,
)


@dataclass(frozen=True)
class PeriodoColeta:
    """Representa um mês fechado de coleta."""

    ano: int
    mes: int

    def __post_init__(self) -> None:
        if not 1 <= self.mes <= 12:
            raise ValueError("mes deve estar entre 1 e 12.")

    @property
    def data_inicial(self) -> str:
        return f"{self.ano}{self.mes:02d}01"

    @property
    def data_final(self) -> str:
        ultimo_dia = monthrange(self.ano, self.mes)[1]
        return f"{self.ano}{self.mes:02d}{ultimo_dia:02d}"

    @property
    def identificador(self) -> str:
        return f"{self.ano}_{self.mes:02d}"


def caminhos_do_periodo(periodo: PeriodoColeta) -> dict[str, Path]:
    """Monta os caminhos mensais de dados, falhas e manifestos."""

    ano = str(periodo.ano)
    identificador = periodo.identificador

    return {
        "contratos": Path(
            f"data/normalized/contratos/{ano}/"
            f"contratos_{identificador}.json"
        ),
        "contratacoes": Path(
            f"data/normalized/contratacoes/{ano}/"
            f"contratacoes_{identificador}.json"
        ),
        "falhas_contratos": Path(
            f"data/checkpoints/contratos/{ano}/"
            f"falhas_{identificador}.json"
        ),
        "falhas_contratacoes": Path(
            f"data/checkpoints/contratacoes/{ano}/"
            f"falhas_{identificador}.json"
        ),
        "manifesto_contratos": Path(
            f"data/normalized/contratos/{ano}/"
            f"contratos_documentos_{identificador}.json"
        ),
        "manifesto_contratacoes": Path(
            f"data/normalized/contratacoes/{ano}/"
            f"contratacoes_documentos_{identificador}.json"
        ),
        # Estado local separado: erros, downloads, formatos e extracoes.
        # O manifesto acima permanece como a listagem original do PNCP.
        "checkpoint_documentos_contratos": Path(
            f"data/checkpoints/documentos/contratos/{ano}/"
            f"estado_documentos_{identificador}.json"
        ),
        "checkpoint_documentos_contratacoes": Path(
            f"data/checkpoints/documentos/contratacoes/{ano}/"
            f"estado_documentos_{identificador}.json"
        ),
        # A extracao de texto tem checkpoint proprio. Assim, a resposta
        # original da listagem do PNCP nunca recebe status de Docling.
        "fila_extracao_docling": Path(
            f"data/checkpoints/extracao/{ano}/"
            f"fila_docling_{identificador}.json"
        ),
        "checkpoint_extracao_docling": Path(
            f"data/checkpoints/extracao/{ano}/"
            f"estado_docling_{identificador}.json"
        ),
        "relatorio_extracao_docling": Path(
            f"data/normalized/inventarios/{ano}/"
            f"extracao_docling_{identificador}.json"
        ),
        # Esta pasta e' portavel: pode ser gravada no Drive pelo Colab e
        # depois copiada de volta sem alterar os caminhos dos metadados.
        "pasta_textos_docling": Path(
            f"data/extracted/docling/{ano}/{periodo.mes:02d}"
        ),
    }


def coletar_e_salvar_contratos(
    periodo: PeriodoColeta,
    arquivo_saida: Path,
    arquivo_falhas: Path,
    tamanho_pagina: int,
    limite_paginas: int | None,
) -> int:
    """Coleta contratos de um mês e mescla apenas os registros novos."""

    resultado = coletar_contratos(
        data_inicial=periodo.data_inicial,
        data_final=periodo.data_final,
        tamanho_pagina=tamanho_pagina,
        limite_paginas=limite_paginas,
    )

    quantidade_nova = adicionar_registros_novos(
        resultado.contratos,
        arquivo=arquivo_saida,
    )
    salvar_falhas_coleta(
        falhas=resultado.falhas,
        caminho=arquivo_falhas,
        tipo="contrato",
    )

    print(
        f"Contratos | período={periodo.identificador} | "
        f"novos={quantidade_nova} | falhas={len(resultado.falhas)}"
    )
    return quantidade_nova


def coletar_e_salvar_contratacoes(
    periodo: PeriodoColeta,
    arquivo_saida: Path,
    arquivo_falhas: Path,
    modalidades: list[int],
    tamanho_pagina: int,
    limite_paginas_por_modalidade: int | None,
) -> int:
    """Coleta contratações de um mês e mescla apenas as novas."""

    resultado = coletar_contratacoes(
        uf="RJ",
        data_inicial=periodo.data_inicial,
        data_final=periodo.data_final,
        modalidades=modalidades,
        tamanho_pagina=tamanho_pagina,
        limite_paginas_por_modalidade=limite_paginas_por_modalidade,
    )

    quantidade_nova = adicionar_registros_novos(
        resultado.contratacoes,
        arquivo=arquivo_saida,
    )
    salvar_falhas_coleta(
        falhas=resultado.falhas,
        caminho=arquivo_falhas,
        tipo="contratacao",
    )

    print(
        f"Contratações | período={periodo.identificador} | "
        f"novas={quantidade_nova} | falhas={len(resultado.falhas)}"
    )
    return quantidade_nova


def reprocessar_falhas_contratos(
    arquivo_saida: Path,
    arquivo_falhas: Path,
    tamanho_pagina: int,
    max_tentativas: int = 3,
) -> dict[str, int]:
    """Reprocessa manualmente apenas páginas pendentes de contratos."""

    checkpoint = carregar_json(arquivo_falhas, padrao={"falhas": []})
    falhas = checkpoint.get("falhas", []) if isinstance(
        checkpoint, dict) else []
    pendentes = [
        falha
        for falha in falhas
        if isinstance(falha, dict)
        and falha.get("tipo") == "contrato"
        and falha.get("status") == "pendente"
    ]

    resumo = {"tentadas": 0, "resolvidas": 0, "pendentes": 0, "novos": 0}

    for falha in pendentes:
        pagina = falha.get("pagina")
        if not isinstance(pagina, int):
            continue

        resumo["tentadas"] += 1
        resultado = coletar_pagina_contratos(
            data_inicial=str(falha["dataInicial"]),
            data_final=str(falha["dataFinal"]),
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )

        if not resultado.falhas:
            resumo["novos"] += adicionar_registros_novos(
                resultado.contratos,
                arquivo=arquivo_saida,
            )
            atualizar_falha_reprocessada(
                caminho=arquivo_falhas,
                falha_original=falha,
                sucesso=True,
                max_tentativas=max_tentativas,
            )
            resumo["resolvidas"] += 1
        else:
            atualizar_falha_reprocessada(
                caminho=arquivo_falhas,
                falha_original=falha,
                sucesso=False,
                motivo=resultado.falhas[0].get("motivo"),
                max_tentativas=max_tentativas,
            )
            resumo["pendentes"] += 1

    print("Reprocessamento manual | contratos |", resumo)
    return resumo


def reprocessar_falhas_contratacoes(
    arquivo_saida: Path,
    arquivo_falhas: Path,
    tamanho_pagina: int,
    max_tentativas: int = 3,
) -> dict[str, int]:
    """Reprocessa manualmente páginas pendentes de contratações."""

    checkpoint = carregar_json(arquivo_falhas, padrao={"falhas": []})
    falhas = checkpoint.get("falhas", []) if isinstance(
        checkpoint, dict) else []
    pendentes = [
        falha
        for falha in falhas
        if isinstance(falha, dict)
        and falha.get("tipo") == "contratacao"
        and falha.get("status") == "pendente"
    ]

    resumo = {"tentadas": 0, "resolvidas": 0, "pendentes": 0, "novas": 0}

    for falha in pendentes:
        pagina = falha.get("pagina")
        modalidade = falha.get("modalidade")
        if not isinstance(pagina, int) or not isinstance(modalidade, int):
            continue

        resumo["tentadas"] += 1
        resultado = coletar_pagina_contratacoes(
            uf="RJ",
            data_inicial=str(falha["dataInicial"]),
            data_final=str(falha["dataFinal"]),
            modalidade=modalidade,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )

        if not resultado.falhas:
            resumo["novas"] += adicionar_registros_novos(
                resultado.contratacoes,
                arquivo=arquivo_saida,
            )
            atualizar_falha_reprocessada(
                caminho=arquivo_falhas,
                falha_original=falha,
                sucesso=True,
                max_tentativas=max_tentativas,
            )
            resumo["resolvidas"] += 1
        else:
            atualizar_falha_reprocessada(
                caminho=arquivo_falhas,
                falha_original=falha,
                sucesso=False,
                motivo=resultado.falhas[0].get("motivo"),
                max_tentativas=max_tentativas,
            )
            resumo["pendentes"] += 1

    print("Reprocessamento manual | contratações |", resumo)
    return resumo


# ---------------------------------------------------------------------------
# ETAPA FUTURA — MANIFESTO E DOWNLOAD DE DOCUMENTOS
# ---------------------------------------------------------------------------

def gerar_manifesto_documentos(
    nome_etapa: str,
    arquivo_entrada: Path,
    caminho_manifesto: Path,
    caminho_checkpoint: Path,
    funcao_gerar_manifesto: Callable[..., list[dict[str, Any]]],
    limite_itens: int = 5,
) -> list[dict[str, Any]]:
    """Lista documentos de itens já coletados, sem baixar arquivos."""

    itens = carregar_json(arquivo_entrada, padrao=[])
    if not isinstance(itens, list):
        raise RuntimeError(
            f"O arquivo de entrada deve conter uma lista: {arquivo_entrada}"
        )

    return funcao_gerar_manifesto(
        itens,
        caminho_manifesto,
        caminho_checkpoint=caminho_checkpoint,
        limite=limite_itens,
    )


def processar_documentos(
    nome_etapa: str,
    arquivo_entrada: Path,
    caminho_manifesto: Path,
    caminho_checkpoint: Path,
    pasta_base: Path,
    pasta_extrair: Path,
    funcao_gerar_manifesto: Callable[..., list[dict[str, Any]]],
    limite_itens: int = 5,
    limite_documentos_por_item: int = 1,
) -> dict[str, int]:
    """Etapa futura: gera manifesto, baixa, identifica e extrai arquivos."""

    manifesto_original = gerar_manifesto_documentos(
        nome_etapa=nome_etapa,
        arquivo_entrada=arquivo_entrada,
        caminho_manifesto=caminho_manifesto,
        caminho_checkpoint=caminho_checkpoint,
        funcao_gerar_manifesto=funcao_gerar_manifesto,
        limite_itens=limite_itens,
    )
    checkpoint = carregar_checkpoint_documentos(caminho_checkpoint)
    manifesto = materializar_manifesto_com_checkpoint(
        manifesto_original,
        checkpoint,
    )

    qtd_baixada = baixar_documentos_lista(
        manifesto=manifesto,
        pasta_base=pasta_base,
        limite_contratos=limite_itens,
        limite_documentos_por_contrato=limite_documentos_por_item,
    )
    qtd_identificada = identificar_arquivos_do_manifesto(manifesto)
    qtd_extraida = extrair_zips_do_manifesto(
        manifesto=manifesto,
        pasta_base=pasta_extrair,
    )
    salvar_estado_documentos(
        caminho_checkpoint,
        checkpoint,
        manifesto,
    )

    return {
        "baixados": qtd_baixada,
        "identificados": qtd_identificada,
        "extraidos": qtd_extraida,
    }


if __name__ == "__main__":
    # 📆 AJUSTE AQUI O ANO E OS MESES QUE DESEJA RODAR:
    ANO_ALVO = 2026

    # Exemplo 1: Para rodar de Janeiro a Março (1 a 3), use range(1, 4)
    # Exemplo 2: Para rodar de Abril a Junho (4 a 6), use range(4, 7)
    MESES_A_COLETAR = range(9, 10)

    # Configurações de paginação e limites
    TAMANHO_PAGINA_CONTRATO = 500
    LIMITE_PAGINAS_CONTRATOS = None
    TAMANHO_PAGINA_CONTRATACAO = 50
    MODALIDADES_CONTRATACOES = None
    LIMITE_PAGINAS_POR_MODALIDADE = None
    MAX_TENTATIVAS_REPROCESSAMENTO = 3

    # Modos de execução
    MODO_EXECUCAO = "coletar"
    EXECUTAR_CONTRATOS = True
    EXECUTAR_CONTRATACOES = True

    if MODO_EXECUCAO not in {"coletar", "reprocessar"}:
        raise ValueError("MODO_EXECUCAO deve ser 'coletar' ou 'reprocessar'.")

    # 🔄 O LOOP "FOR" ENTRA AQUI COMPORTANDO TODO O PROCESSO:
    for mes in MESES_A_COLETAR:
        # Criando o período dinamicamente com a variável do 'for'
        PERIODO = PeriodoColeta(ano=ANO_ALVO, mes=mes)
        CAMINHOS = caminhos_do_periodo(PERIODO)

        print("\n" + "="*60)
        print(
            f"📅 INICIANDO MÊS {mes:02d}/{ANO_ALVO} - MODO: '{MODO_EXECUCAO}'")
        print("="*60)

        # 1️⃣ BLOCO DE CONTRATOS (COLETAR)
        if MODO_EXECUCAO == "coletar" and EXECUTAR_CONTRATOS:
            print(f"⏳ [{mes:02d}/{ANO_ALVO}] Iniciando coleta de CONTRATOS...")
            coletar_e_salvar_contratos(
                periodo=PERIODO,
                arquivo_saida=CAMINHOS["contratos"],
                arquivo_falhas=CAMINHOS["falhas_contratos"],
                tamanho_pagina=TAMANHO_PAGINA_CONTRATO,
                limite_paginas=LIMITE_PAGINAS_CONTRATOS,
            )
            print(f"✅ [{mes:02d}/{ANO_ALVO}] CONTRATOS finalizados.\n")

        # 2️⃣ BLOCO DE CONTRATAÇÕES (COLETAR)
        if MODO_EXECUCAO == "coletar" and EXECUTAR_CONTRATACOES:
            print(f"⏳ [{mes:02d}/{ANO_ALVO}] Iniciando coleta de CONTRATAÇÕES...")
            coletar_e_salvar_contratacoes(
                periodo=PERIODO,
                arquivo_saida=CAMINHOS["contratacoes"],
                arquivo_falhas=CAMINHOS["falhas_contratacoes"],
                modalidades=MODALIDADES_CONTRATACOES,
                tamanho_pagina=TAMANHO_PAGINA_CONTRATACAO,
                limite_paginas_por_modalidade=LIMITE_PAGINAS_POR_MODALIDADE,
            )
            print(f"✅ [{mes:02d}/{ANO_ALVO}] CONTRATAÇÕES finalizadas.\n")

        # 3️⃣ BLOCO DE CONTRATOS (REPROCESSAR)
        if MODO_EXECUCAO == "reprocessar" and EXECUTAR_CONTRATOS:
            print(
                f"⏳ [{mes:02d}/{ANO_ALVO}] Reprocessando falhas de CONTRATOS...")
            reprocessar_falhas_contratos(
                arquivo_saida=CAMINHOS["contratos"],
                arquivo_falhas=CAMINHOS["falhas_contratos"],
                tamanho_pagina=TAMANHO_PAGINA_CONTRATO,
                max_tentativas=MAX_TENTATIVAS_REPROCESSAMENTO,
            )
            print(
                f"✅ [{mes:02d}/{ANO_ALVO}] Reprocessamento de CONTRATOS finalizado.\n")

        # 4️⃣ BLOCO DE CONTRATAÇÕES (REPROCESSAR)
        if MODO_EXECUCAO == "reprocessar" and EXECUTAR_CONTRATACOES:
            print(
                f"⏳ [{mes:02d}/{ANO_ALVO}] Reprocessando falhas de CONTRATAÇÕES...")
            reprocessar_falhas_contratacoes(
                arquivo_saida=CAMINHOS["contratacoes"],
                arquivo_falhas=CAMINHOS["falhas_contratacoes"],
                tamanho_pagina=TAMANHO_PAGINA_CONTRATACAO,
                max_tentativas=MAX_TENTATIVAS_REPROCESSAMENTO,
            )
            print(
                f"✅ [{mes:02d}/{ANO_ALVO}] Reprocessamento de CONTRATAÇÕES finalizado.\n")

    print("\n🎉 [FIM] Todos os meses selecionados foram processados em sequência!")
