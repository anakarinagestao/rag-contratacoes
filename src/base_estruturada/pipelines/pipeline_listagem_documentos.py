"""Lista documentos do PNCP e salva manifestos mensais.

Este pipeline não baixa, identifica nem extrai arquivos. Ele somente lê os
JSONs mensais já coletados e consulta o endpoint ``/arquivos`` de cada item.
"""

from pathlib import Path
from typing import Any, Callable

from src.base_estruturada.pipelines.pipeline_documentos import (
    PeriodoColeta,
    caminhos_do_periodo,
)
from src.base_estruturada.pncp.repository import (
    carregar_checkpoint_documentos,
    carregar_json,
    salvar_lista_de_documentos_contratos,
    salvar_lista_documentos_contratacao,
)


def listar_documentos_do_periodo(
    nome_etapa: str,
    arquivo_entrada: Path,
    arquivo_manifesto: Path,
    arquivo_checkpoint: Path,
    funcao_listagem: Callable[..., list[dict[str, Any]]],
    limite_novos_itens: int,
) -> None:
    """Lista documentos para os próximos itens ainda ausentes do manifesto."""

    itens = carregar_json(arquivo_entrada, padrao=[])
    if not isinstance(itens, list):
        raise RuntimeError(
            f"O arquivo de entrada deve conter uma lista: {arquivo_entrada}"
        )

    antes = carregar_json(arquivo_manifesto, padrao=[])
    total_antes = len(antes) if isinstance(antes, list) else 0

    print(f"\n=== {nome_etapa}: listagem de documentos ===")
    print(f"Itens disponíveis: {len(itens)} | já no manifesto: {total_antes}")

    manifesto = funcao_listagem(
        itens,
        arquivo_manifesto,
        caminho_checkpoint=arquivo_checkpoint,
        limite=limite_novos_itens,
    )

    novos = len(manifesto) - total_antes
    checkpoint = carregar_checkpoint_documentos(arquivo_checkpoint)
    estados = checkpoint.get("entidades") or {}
    concluidos = sum(
        1
        for estado in estados.values()
        if isinstance(estado, dict)
        and estado.get("statusListagem") == "concluida"
    )
    erros = sum(
        1
        for estado in estados.values()
        if isinstance(estado, dict)
        and estado.get("statusListagem") == "erro"
    )

    print(
        f"Listagem original: {arquivo_manifesto}\n"
        f"Checkpoint: {arquivo_checkpoint} | novos={novos} | "
        f"concluídos={concluidos} | erros={erros}"
    )


if __name__ == "__main__":
    # 📆 AJUSTE AQUI O ANO E OS MESES QUE DESEJA RODAR:
    ANO_ALVO = 2026

    # Altere o range conforme o bloco de meses que quer processar:
    # range(1, 4)  -> Meses 1, 2 e 3
    # range(4, 7)  -> Meses 4, 5 e 6
    # range(7, 10) -> Meses 7, 8 e 9
    MESES_A_COLETAR = range(9, 10)

    # Número de novas consultas por execução. Ao rodar novamente, o pipeline
    # pula itens que já estão no manifesto e avança para os próximos.
    LIMITE_NOVOS_CONTRATOS = 7000
    LIMITE_NOVAS_CONTRATACOES = 7000

    EXECUTAR_CONTRATOS = True
    EXECUTAR_CONTRATACOES = True

    # 🔄 INÍCIO DO LOOP AUTOMÁTICO POR MÊS
    for mes in MESES_A_COLETAR:
        # Gerando o período e os caminhos dinamicamente para cada mês do loop
        PERIODO = PeriodoColeta(ano=ANO_ALVO, mes=mes)
        CAMINHOS = caminhos_do_periodo(PERIODO)

        print("\n" + "="*60)
        print(f"📂 PROCESSANDO DOCUMENTOS DO MÊS {mes:02d}/{ANO_ALVO}")
        print("="*60)

        # 1️⃣ ETAPA DE CONTRATOS
        if EXECUTAR_CONTRATOS:
            print(f"⏳ [{mes:02d}/{ANO_ALVO}] Listando documentos de Contratos...")
            listar_documentos_do_periodo(
                nome_etapa="Contratos",
                arquivo_entrada=CAMINHOS["contratos"],
                arquivo_manifesto=CAMINHOS["manifesto_contratos"],
                arquivo_checkpoint=CAMINHOS["checkpoint_documentos_contratos"],
                funcao_listagem=salvar_lista_de_documentos_contratos,
                limite_novos_itens=LIMITE_NOVOS_CONTRATOS,
            )
            print(
                f"✅ [{mes:02d}/{ANO_ALVO}] Documentos de Contratos concluídos.\n")

        # 2️⃣ ETAPA DE CONTRATAÇÕES
        if EXECUTAR_CONTRATACOES:
            print(
                f"⏳ [{mes:02d}/{ANO_ALVO}] Listando documentos de Contratações...")
            listar_documentos_do_periodo(
                nome_etapa="Contratações",
                arquivo_entrada=CAMINHOS["contratacoes"],
                arquivo_manifesto=CAMINHOS["manifesto_contratacoes"],
                arquivo_checkpoint=CAMINHOS["checkpoint_documentos_contratacoes"],
                funcao_listagem=salvar_lista_documentos_contratacao,
                limite_novos_itens=LIMITE_NOVAS_CONTRATACOES,
            )
            print(
                f"✅ [{mes:02d}/{ANO_ALVO}] Documentos de Contratações concluídos.\n")

    print(
        "\n🎉 [FIM] Todos os meses do bloco selecionado foram processados com sucesso!")
