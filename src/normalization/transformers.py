from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from src.ingestion.identity import TimestampStatus, parse_timestamp


class NormalizationError(ValueError):
    """Payload RAW não satisfaz o contrato mínimo da normalização."""


def required(value: Any, field: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise NormalizationError(f"Campo obrigatório ausente: {field}")
    return value


def money_to_cents(value: Any) -> int | None:
    """Converte moeda decimal em centavos sem usar ponto flutuante binário."""

    if value in (None, ""):
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise NormalizationError(f"Valor monetário inválido: {value!r}") from error
    if not decimal_value.is_finite():
        raise NormalizationError(f"Valor monetário inválido: {value!r}")
    return int(
        (decimal_value * Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def boolean_to_integer(value: Any, field: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        return int(value)
    if value in (0, 1):
        return int(value)
    raise NormalizationError(f"Booleano inválido em {field}: {value!r}")


def _raw_columns(raw_asset: Mapping[str, Any], normalized_at: str) -> dict[str, Any]:
    return {
        "current_source_raw_asset_id": required(raw_asset.get("id"), "raw_asset.id"),
        "current_source_payload_hash": required(
            raw_asset.get("payload_hash"), "raw_asset.payload_hash"
        ),
        "normalized_at": normalized_at,
    }


def normalize_procurement(
    raw_asset: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    normalized_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    organization = required(payload.get("orgaoEntidade"), "orgaoEntidade")
    unit = required(payload.get("unidadeOrgao"), "unidadeOrgao")
    if not isinstance(organization, Mapping) or not isinstance(unit, Mapping):
        raise NormalizationError("Orgão ou unidade não é um objeto JSON")

    raw_columns = _raw_columns(raw_asset, normalized_at)
    cnpj = str(required(organization.get("cnpj"), "orgaoEntidade.cnpj"))
    unit_code = str(
        required(unit.get("codigoUnidade"), "unidadeOrgao.codigoUnidade")
    )
    organization_row = {
        "cnpj": cnpj,
        "razao_social": str(
            required(organization.get("razaoSocial"), "orgaoEntidade.razaoSocial")
        ),
        "poder_id": organization.get("poderId"),
        "esfera_id": organization.get("esferaId"),
        **raw_columns,
    }
    unit_row = {
        "orgao_cnpj": cnpj,
        "codigo_unidade": unit_code,
        "nome_unidade": unit.get("nomeUnidade"),
        "codigo_ibge": unit.get("codigoIbge"),
        "municipio_nome": unit.get("municipioNome"),
        "uf_sigla": str(required(unit.get("ufSigla"), "unidadeOrgao.ufSigla")),
        "uf_nome": unit.get("ufNome"),
        **raw_columns,
    }
    legal = payload.get("amparoLegal") or {}
    if not isinstance(legal, Mapping):
        legal = {}
    procurement_row = {
        "numero_controle_pncp": str(
            required(payload.get("numeroControlePNCP"), "numeroControlePNCP")
        ),
        "orgao_cnpj": cnpj,
        "codigo_unidade": unit_code,
        "ano_compra": int(required(payload.get("anoCompra"), "anoCompra")),
        "sequencial_compra": int(
            required(payload.get("sequencialCompra"), "sequencialCompra")
        ),
        "numero_compra": payload.get("numeroCompra"),
        "processo": payload.get("processo"),
        "modalidade_id": int(required(payload.get("modalidadeId"), "modalidadeId")),
        "modalidade_nome": payload.get("modalidadeNome"),
        "modo_disputa_id": payload.get("modoDisputaId"),
        "modo_disputa_nome": payload.get("modoDisputaNome"),
        "tipo_instrumento_codigo": payload.get("tipoInstrumentoConvocatorioCodigo"),
        "tipo_instrumento_nome": payload.get("tipoInstrumentoConvocatorioNome"),
        "situacao_compra_id": payload.get("situacaoCompraId"),
        "situacao_compra_nome": payload.get("situacaoCompraNome"),
        "amparo_legal_codigo": legal.get("codigo"),
        "amparo_legal_nome": legal.get("nome"),
        "objeto_compra": payload.get("objetoCompra"),
        "informacao_complementar": payload.get("informacaoComplementar"),
        "srp": boolean_to_integer(payload.get("srp"), "srp"),
        "valor_total_estimado_centavos": money_to_cents(
            payload.get("valorTotalEstimado")
        ),
        "valor_total_homologado_centavos": money_to_cents(
            payload.get("valorTotalHomologado")
        ),
        "data_publicacao_pncp": payload.get("dataPublicacaoPncp"),
        "data_abertura_proposta": payload.get("dataAberturaProposta"),
        "data_encerramento_proposta": payload.get("dataEncerramentoProposta"),
        "source_updated_at": payload.get("dataAtualizacaoGlobal")
        or payload.get("dataAtualizacao"),
        **raw_columns,
    }
    return organization_row, unit_row, procurement_row


def _parent_and_number(external_id: str, expected_parts: int) -> tuple[str, ...]:
    parts = external_id.rsplit("|", expected_parts - 1)
    if len(parts) != expected_parts or any(not part for part in parts):
        raise NormalizationError(f"external_id composto inválido: {external_id!r}")
    return tuple(parts)


def normalize_item(
    raw_asset: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    normalized_at: str,
) -> dict[str, Any]:
    parent, external_number = _parent_and_number(
        str(required(raw_asset.get("external_id"), "raw_asset.external_id")), 2
    )
    number = int(required(payload.get("numeroItem"), "numeroItem"))
    if number != int(external_number):
        raise NormalizationError("numeroItem diverge do external_id")
    return {
        "numero_controle_pncp": parent,
        "numero_item": number,
        "descricao": payload.get("descricao"),
        "quantidade": payload.get("quantidade"),
        "unidade_medida": payload.get("unidadeMedida"),
        "valor_unitario_estimado_centavos": money_to_cents(
            payload.get("valorUnitarioEstimado")
        ),
        "valor_total_estimado_centavos": money_to_cents(payload.get("valorTotal")),
        "material_ou_servico": payload.get("materialOuServico"),
        "material_ou_servico_nome": payload.get("materialOuServicoNome"),
        "criterio_julgamento_id": payload.get("criterioJulgamentoId"),
        "criterio_julgamento_nome": payload.get("criterioJulgamentoNome"),
        "categoria_item_id": payload.get("itemCategoriaId"),
        "categoria_item_nome": payload.get("itemCategoriaNome"),
        "situacao_item_id": payload.get("situacaoCompraItem"),
        "situacao_item_nome": payload.get("situacaoCompraItemNome"),
        "tem_resultado": boolean_to_integer(payload.get("temResultado"), "temResultado"),
        "data_inclusao": payload.get("dataInclusao"),
        "source_updated_at": payload.get("dataAtualizacao"),
        **_raw_columns(raw_asset, normalized_at),
    }


def _result_date_quality(
    value: Any,
    purchase_id: str,
    *,
    reference_date: date,
) -> str:
    parsed = parse_timestamp(value)
    if parsed.status != TimestampStatus.VALID or parsed.normalized is None:
        return "INVALID"
    try:
        result_date = date.fromisoformat(parsed.normalized[:10])
        purchase_year = int(purchase_id.rsplit("/", 1)[1])
    except (ValueError, IndexError):
        return "INVALID"
    if result_date.year < purchase_year - 1 or result_date > reference_date:
        return "SUSPECT_DATE"
    return "VALID"


def normalize_result(
    raw_asset: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    normalized_at: str,
    reference_date: date,
) -> tuple[dict[str, Any], dict[str, Any]]:
    purchase_id, external_item, external_sequence = _parent_and_number(
        str(required(raw_asset.get("external_id"), "raw_asset.external_id")), 3
    )
    payload_purchase = str(
        required(payload.get("numeroControlePNCPCompra"), "numeroControlePNCPCompra")
    )
    item_number = int(required(payload.get("numeroItem"), "numeroItem"))
    sequence = int(required(payload.get("sequencialResultado"), "sequencialResultado"))
    if (payload_purchase, item_number, sequence) != (
        purchase_id,
        int(external_item),
        int(external_sequence),
    ):
        raise NormalizationError("Identidade do resultado diverge do external_id")

    person_type = str(required(payload.get("tipoPessoa"), "tipoPessoa"))
    supplier_id = str(required(payload.get("niFornecedor"), "niFornecedor"))
    raw_columns = _raw_columns(raw_asset, normalized_at)
    supplier_row = {
        "tipo_pessoa": person_type,
        "ni_fornecedor": supplier_id,
        "nome_razao_social": payload.get("nomeRazaoSocialFornecedor"),
        "porte_id": payload.get("porteFornecedorId"),
        "porte_nome": payload.get("porteFornecedorNome"),
        "natureza_juridica_id": payload.get("naturezaJuridicaId"),
        "natureza_juridica_nome": payload.get("naturezaJuridicaNome"),
        "codigo_pais": payload.get("codigoPais"),
        "identificador_pessoal": int(person_type.upper() == "PF"),
        **raw_columns,
    }
    result_row = {
        "numero_controle_pncp": purchase_id,
        "numero_item": item_number,
        "sequencial_resultado": sequence,
        "tipo_pessoa_fornecedor": person_type,
        "ni_fornecedor": supplier_id,
        "ordem_classificacao_srp": payload.get("ordemClassificacaoSrp"),
        "quantidade_homologada": payload.get("quantidadeHomologada"),
        "valor_unitario_homologado_centavos": money_to_cents(
            payload.get("valorUnitarioHomologado")
        ),
        "valor_total_homologado_centavos": money_to_cents(
            payload.get("valorTotalHomologado")
        ),
        "percentual_desconto": payload.get("percentualDesconto"),
        "situacao_resultado_id": payload.get("situacaoCompraItemResultadoId"),
        "situacao_resultado_nome": payload.get("situacaoCompraItemResultadoNome"),
        "aplicacao_beneficio_me_epp": boolean_to_integer(
            payload.get("aplicacaoBeneficioMeEpp"),
            "aplicacaoBeneficioMeEpp",
            nullable=True,
        ),
        "data_resultado": payload.get("dataResultado"),
        "data_cancelamento": payload.get("dataCancelamento"),
        "motivo_cancelamento": payload.get("motivoCancelamento"),
        "source_updated_at": payload.get("dataAtualizacao"),
        "data_quality_status": _result_date_quality(
            payload.get("dataResultado"), purchase_id, reference_date=reference_date
        ),
        **raw_columns,
    }
    return supplier_row, result_row


def normalize_contract(
    raw_asset: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    normalized_at: str,
    parent_is_present: bool,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any],
]:
    organization = required(payload.get("orgaoEntidade"), "orgaoEntidade")
    unit = required(payload.get("unidadeOrgao"), "unidadeOrgao")
    if not isinstance(organization, Mapping) or not isinstance(unit, Mapping):
        raise NormalizationError("Orgão ou unidade do contrato não é objeto")
    raw_columns = _raw_columns(raw_asset, normalized_at)
    cnpj = str(required(organization.get("cnpj"), "orgaoEntidade.cnpj"))
    unit_code = str(
        required(unit.get("codigoUnidade"), "unidadeOrgao.codigoUnidade")
    )
    person_type = str(required(payload.get("tipoPessoa"), "tipoPessoa"))
    supplier_value = payload.get("niFornecedor")
    supplier_id = (
        str(supplier_value).strip()
        if supplier_value not in (None, "") and str(supplier_value).strip()
        else None
    )
    purchase_id = str(
        required(payload.get("numeroControlePncpCompra"), "numeroControlePncpCompra")
    )
    contract_type = payload.get("tipoContrato") or {}
    process_category = payload.get("categoriaProcesso") or {}
    if not isinstance(contract_type, Mapping):
        contract_type = {}
    if not isinstance(process_category, Mapping):
        process_category = {}
    organization_row = {
        "cnpj": cnpj,
        "razao_social": str(
            required(organization.get("razaoSocial"), "orgaoEntidade.razaoSocial")
        ),
        "poder_id": organization.get("poderId"),
        "esfera_id": organization.get("esferaId"),
        **raw_columns,
    }
    unit_row = {
        "orgao_cnpj": cnpj,
        "codigo_unidade": unit_code,
        "nome_unidade": unit.get("nomeUnidade"),
        "codigo_ibge": unit.get("codigoIbge"),
        "municipio_nome": unit.get("municipioNome"),
        "uf_sigla": str(required(unit.get("ufSigla"), "unidadeOrgao.ufSigla")),
        "uf_nome": unit.get("ufNome"),
        **raw_columns,
    }
    supplier_row = None
    if supplier_id is not None:
        supplier_row = {
            "tipo_pessoa": person_type,
            "ni_fornecedor": supplier_id,
            "nome_razao_social": payload.get("nomeRazaoSocialFornecedor"),
            "porte_id": None,
            "porte_nome": None,
            "natureza_juridica_id": None,
            "natureza_juridica_nome": None,
            "codigo_pais": payload.get("codigoPaisFornecedor"),
            "identificador_pessoal": int(person_type.upper() == "PF"),
            **raw_columns,
        }
    contract_row = {
        "numero_controle_pncp": str(
            required(payload.get("numeroControlePNCP"), "numeroControlePNCP")
        ),
        "numero_controle_compra_externo": purchase_id,
        "contratacao_id": purchase_id if parent_is_present else None,
        "parent_resolution_status": "RESOLVED" if parent_is_present else "OUT_OF_SCOPE",
        "orgao_cnpj": cnpj,
        "codigo_unidade": unit_code,
        "tipo_pessoa_fornecedor": person_type,
        "ni_fornecedor": supplier_id,
        "nome_fornecedor_informado": payload.get("nomeRazaoSocialFornecedor"),
        "supplier_resolution_status": (
            "RESOLVED" if supplier_id is not None else "MISSING_IDENTIFIER"
        ),
        "ano_contrato": payload.get("anoContrato"),
        "sequencial_contrato": payload.get("sequencialContrato"),
        "numero_contrato_empenho": payload.get("numeroContratoEmpenho"),
        "processo": payload.get("processo"),
        "objeto_contrato": payload.get("objetoContrato"),
        "tipo_contrato_id": contract_type.get("id"),
        "tipo_contrato_nome": contract_type.get("nome"),
        "categoria_processo_id": process_category.get("id"),
        "categoria_processo_nome": process_category.get("nome"),
        "valor_inicial_centavos": money_to_cents(payload.get("valorInicial")),
        "valor_global_centavos": money_to_cents(payload.get("valorGlobal")),
        "valor_acumulado_centavos": money_to_cents(payload.get("valorAcumulado")),
        "valor_parcela_centavos": money_to_cents(payload.get("valorParcela")),
        "numero_parcelas": payload.get("numeroParcelas"),
        "data_publicacao_pncp": payload.get("dataPublicacaoPncp"),
        "data_assinatura": payload.get("dataAssinatura"),
        "vigencia_inicio": payload.get("dataVigenciaInicio"),
        "vigencia_fim": payload.get("dataVigenciaFim"),
        "source_updated_at": payload.get("dataAtualizacaoGlobal")
        or payload.get("dataAtualizacao"),
        **raw_columns,
    }
    return organization_row, unit_row, supplier_row, contract_row


def normalize_commitment(
    raw_asset: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    normalized_at: str,
) -> dict[str, Any]:
    contract_id, external_sequence = _parent_and_number(
        str(required(raw_asset.get("external_id"), "raw_asset.external_id")), 2
    )
    sequence = int(required(payload.get("sequencialEmpenho"), "sequencialEmpenho"))
    if sequence != int(external_sequence):
        raise NormalizationError("sequencialEmpenho diverge do external_id")
    return {
        "numero_controle_contrato": contract_id,
        "sequencial_empenho": sequence,
        "numero_empenho": payload.get("numeroEmpenho"),
        "ano": payload.get("ano"),
        "tipo_pessoa_credor": str(required(payload.get("tipoPessoa"), "tipoPessoa")),
        "ni_credor_fornecedor": str(
            required(payload.get("niCredorFornecedor"), "niCredorFornecedor")
        ),
        "nome_credor_fornecedor": payload.get("nomeCredorFornecedor"),
        "codigo_natureza_despesa": payload.get("codigoNaturezaDespesa"),
        "numero_plano_interno": payload.get("numeroPlanoInterno"),
        "situacao_empenho_id": payload.get("situacaoEmpenhoId"),
        "situacao_empenho_nome": payload.get("situacaoEmpenhoNome"),
        "data_emissao": payload.get("dataEmissaoEmpenho"),
        "data_situacao": payload.get("dataSituacaoEmpenho"),
        "valor_total_centavos": money_to_cents(payload.get("valorTotal")),
        "codigo_emenda": payload.get("codigoEmenda"),
        "coverage_status": "PARTIAL",
        "source_updated_at": payload.get("dataAtualizacao"),
        **_raw_columns(raw_asset, normalized_at),
    }


def normalize_price_registry(
    raw_asset: Mapping[str, Any],
    payload: Mapping[str, Any],
    query_params: Mapping[str, Any],
    *,
    normalized_at: str,
    parent_is_present: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    nested_organization = payload.get("orgaoEntidade") or {}
    nested_unit = payload.get("unidadeOrgao") or {}
    if not isinstance(nested_organization, Mapping):
        nested_organization = {}
    if not isinstance(nested_unit, Mapping):
        nested_unit = {}
    ata_id = payload.get("numeroControlePNCPAta") or payload.get("numeroControlePNCP")
    purchase_id = payload.get("numeroControlePNCPCompra") or query_params.get(
        "numeroControlePNCPCompra"
    )
    cnpj = payload.get("cnpjOrgao") or nested_organization.get("cnpj")
    unit_code = payload.get("codigoUnidadeOrgao") or nested_unit.get("codigoUnidade")
    organization_name = payload.get("nomeOrgao") or nested_organization.get("razaoSocial")
    unit_name = payload.get("nomeUnidadeOrgao") or nested_unit.get("nomeUnidade")
    uf = nested_unit.get("ufSigla") or query_params.get("uf")
    raw_columns = _raw_columns(raw_asset, normalized_at)
    organization_row = {
        "cnpj": str(required(cnpj, "ata.orgao.cnpj")),
        "razao_social": str(required(organization_name, "ata.orgao.razaoSocial")),
        "poder_id": nested_organization.get("poderId"),
        "esfera_id": nested_organization.get("esferaId"),
        **raw_columns,
    }
    unit_row = {
        "orgao_cnpj": organization_row["cnpj"],
        "codigo_unidade": str(required(unit_code, "ata.unidade.codigoUnidade")),
        "nome_unidade": unit_name,
        "codigo_ibge": nested_unit.get("codigoIbge"),
        "municipio_nome": nested_unit.get("municipioNome"),
        "uf_sigla": str(required(uf, "ata.unidade.ufSigla")),
        "uf_nome": nested_unit.get("ufNome"),
        **raw_columns,
    }
    ata_row = {
        "numero_controle_pncp_ata": str(required(ata_id, "ata.numeroControlePNCP")),
        "numero_controle_compra_externo": str(
            required(purchase_id, "ata.numeroControlePNCPCompra")
        ),
        "contratacao_id": str(purchase_id) if parent_is_present else None,
        "parent_resolution_status": "RESOLVED" if parent_is_present else "OUT_OF_SCOPE",
        "orgao_cnpj": organization_row["cnpj"],
        "codigo_unidade": unit_row["codigo_unidade"],
        "ano_ata": payload.get("anoAta"),
        "numero_ata": payload.get("numeroAtaRegistroPreco"),
        "objeto_contratacao": payload.get("objetoContratacao")
        or payload.get("objetoCompra"),
        "data_publicacao_pncp": payload.get("dataPublicacaoPncp"),
        "data_assinatura": payload.get("dataAssinatura"),
        "vigencia_inicio": payload.get("vigenciaInicio"),
        "vigencia_fim": payload.get("vigenciaFim"),
        "cancelado": boolean_to_integer(payload.get("cancelado"), "cancelado"),
        "data_cancelamento": payload.get("dataCancelamento"),
        "possibilidade_adesao": boolean_to_integer(
            payload.get("possibilidadeAdesao"),
            "possibilidadeAdesao",
            nullable=True,
        ),
        "source_updated_at": payload.get("dataAtualizacaoGlobal")
        or payload.get("dataAtualizacao"),
        "coverage_status": "PARTIAL",
        **raw_columns,
    }
    return organization_row, unit_row, ata_row


def normalize_pca(
    raw_asset: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    normalized_at: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    items = payload.get("itens")
    if not isinstance(items, list) or not items:
        raise NormalizationError("PCA sem itens")
    top_level = payload.get("idPcaPncp") not in (None, "")
    identities = {
        (
            str(item.get("cnpj") or payload.get("orgaoEntidadeCnpj") or ""),
            str(item.get("codigoUnidade") or payload.get("codigoUnidade") or ""),
            item.get("anoPca", payload.get("anoPca")),
        )
        for item in items
        if isinstance(item, Mapping)
    }
    if len(identities) != 1:
        raise NormalizationError("Itens do PCA divergem em CNPJ, unidade ou ano")
    cnpj, unit_code, year = next(iter(identities))
    required(cnpj, "pca.cnpj")
    required(unit_code, "pca.codigoUnidade")
    required(year, "pca.anoPca")
    numbers = [int(required(item.get("numeroItem"), "pca.item.numeroItem")) for item in items]
    if len(numbers) != len(set(numbers)):
        raise NormalizationError("PCA possui numeroItem duplicado")
    raw_columns = _raw_columns(raw_asset, normalized_at)
    first = items[0]
    pca_row = {
        "id_pca_pncp": str(required(raw_asset.get("external_id"), "raw_asset.external_id")),
        "orgao_cnpj": cnpj,
        "codigo_unidade": unit_code,
        "ano_pca": int(year),
        "nome_unidade": payload.get("nomeUnidade")
        or payload.get("unidadeResponsavel")
        or first.get("nomeUnidade"),
        "data_publicacao_pncp": payload.get("dataPublicacaoPNCP")
        or first.get("dataPublicacaoPncp"),
        "source_updated_at": payload.get("dataAtualizacaoGlobalPCA")
        or first.get("dataAtualizacao"),
        "source_format": "TOP_LEVEL" if top_level else "UASG_WRAPPER",
        "scope_status": "IN_SCOPE",
        **raw_columns,
    }
    unit_row = {
        "orgao_cnpj": cnpj,
        "codigo_unidade": unit_code,
        "nome_unidade": pca_row["nome_unidade"],
        "codigo_ibge": None,
        "municipio_nome": None,
        "uf_sigla": "RJ",
        "uf_nome": "Rio de Janeiro",
        **raw_columns,
    }
    item_rows = []
    for item in items:
        item_rows.append(
            {
                "id_pca_pncp": pca_row["id_pca_pncp"],
                "numero_item": int(item["numeroItem"]),
                "descricao": item.get("descricaoItem") or item.get("descricao"),
                "quantidade_estimada": item.get("quantidadeEstimada")
                if item.get("quantidadeEstimada") is not None
                else item.get("quantidade"),
                "unidade_fornecimento": item.get("unidadeFornecimento"),
                "valor_unitario_planejado_centavos": money_to_cents(
                    item.get("valorUnitario")
                ),
                "valor_total_planejado_centavos": money_to_cents(item.get("valorTotal")),
                "valor_orcamento_exercicio_centavos": money_to_cents(
                    item.get("valorOrcamentoExercicio")
                ),
                "data_desejada": item.get("dataDesejada"),
                "grupo_contratacao_codigo": item.get("grupoContratacaoCodigo"),
                "grupo_contratacao_nome": item.get("grupoContratacaoNome"),
                "categoria_item_nome": item.get("categoriaItemPcaNome"),
                "codigo_item": item.get("codigoItem"),
                "classificacao_superior_codigo": item.get(
                    "classificacaoSuperiorCodigo"
                ),
                "classificacao_superior_nome": item.get("classificacaoSuperiorNome"),
                "source_updated_at": item.get("dataAtualizacao"),
                **raw_columns,
            }
        )
    return pca_row, unit_row, item_rows
