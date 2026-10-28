from __future__ import annotations

from datetime import date
import unittest

from src.normalization.transformers import (
    NormalizationError,
    money_to_cents,
    normalize_commitment,
    normalize_contract,
    normalize_item,
    normalize_pca,
    normalize_price_registry,
    normalize_procurement,
    normalize_result,
)


RAW = {
    "id": "raw-1",
    "external_id": "12345678000199-1-000001/2023",
    "payload_hash": "hash-1",
}


class TransformerTests(unittest.TestCase):
    def test_money_uses_decimal_rounding(self) -> None:
        self.assertEqual(money_to_cents("0.10"), 10)
        self.assertEqual(money_to_cents(317227.64), 31_722_764)
        self.assertEqual(money_to_cents("1.005"), 101)
        self.assertIsNone(money_to_cents(None))

    def test_normalizes_procurement_and_dimensions(self) -> None:
        payload = {
            "numeroControlePNCP": RAW["external_id"],
            "anoCompra": 2023,
            "sequencialCompra": 1,
            "modalidadeId": 6,
            "srp": False,
            "valorTotalEstimado": "10.20",
            "valorTotalHomologado": "9.99",
            "orgaoEntidade": {
                "cnpj": "12345678000199",
                "razaoSocial": "Orgao",
                "poderId": "E",
                "esferaId": "E",
            },
            "unidadeOrgao": {
                "codigoUnidade": "U1",
                "nomeUnidade": "Unidade",
                "ufSigla": "RJ",
            },
        }
        organization, unit, procurement = normalize_procurement(
            RAW, payload, normalized_at="2026-10-19"
        )
        self.assertEqual(organization["cnpj"], "12345678000199")
        self.assertEqual(unit["codigo_unidade"], "U1")
        self.assertEqual(procurement["valor_total_estimado_centavos"], 1020)
        self.assertEqual(procurement["valor_total_homologado_centavos"], 999)

    def test_item_identity_must_match_external_id(self) -> None:
        raw = {**RAW, "external_id": f"{RAW['external_id']}|2"}
        with self.assertRaisesRegex(NormalizationError, "diverge"):
            normalize_item(
                raw,
                {"numeroItem": 1, "temResultado": False},
                normalized_at="2026-10-19",
            )

    def test_result_preserves_suspect_date(self) -> None:
        raw = {**RAW, "external_id": f"{RAW['external_id']}|1|1"}
        supplier, result = normalize_result(
            raw,
            {
                "numeroControlePNCPCompra": RAW["external_id"],
                "numeroItem": 1,
                "sequencialResultado": 1,
                "tipoPessoa": "PJ",
                "niFornecedor": "99887766000155",
                "valorTotalHomologado": "66000.00",
                "dataResultado": "2013-09-13",
            },
            normalized_at="2026-10-19",
            reference_date=date(2026, 10, 19),
        )
        self.assertEqual(supplier["identificador_pessoal"], 0)
        self.assertEqual(result["valor_total_homologado_centavos"], 6_600_000)
        self.assertEqual(result["data_resultado"], "2013-09-13")
        self.assertEqual(result["data_quality_status"], "SUSPECT_DATE")

    def test_normalizes_contract_and_commitment_identities(self) -> None:
        contract_id = "12345678000199-2-000001/2023"
        contract_raw = {**RAW, "external_id": contract_id}
        organization, unit, supplier, contract = normalize_contract(
            contract_raw,
            {
                "numeroControlePNCP": contract_id,
                "numeroControlePncpCompra": RAW["external_id"],
                "tipoPessoa": "PJ",
                "niFornecedor": "99887766000155",
                "valorGlobal": "123.45",
                "orgaoEntidade": {
                    "cnpj": "12345678000199",
                    "razaoSocial": "Orgao",
                },
                "unidadeOrgao": {
                    "codigoUnidade": "U1",
                    "ufSigla": "RJ",
                },
            },
            normalized_at="2026-10-19",
            parent_is_present=True,
        )
        self.assertEqual(organization["cnpj"], "12345678000199")
        self.assertEqual(unit["codigo_unidade"], "U1")
        self.assertEqual(supplier["ni_fornecedor"], "99887766000155")
        self.assertEqual(contract["contratacao_id"], RAW["external_id"])
        self.assertEqual(contract["parent_resolution_status"], "RESOLVED")
        self.assertEqual(contract["valor_global_centavos"], 12_345)

        commitment = normalize_commitment(
            {**RAW, "external_id": f"{contract_id}|7"},
            {
                "sequencialEmpenho": 7,
                "tipoPessoa": "PJ",
                "niCredorFornecedor": "99887766000155",
                "valorTotal": "9.99",
            },
            normalized_at="2026-10-19",
        )
        self.assertEqual(commitment["numero_controle_contrato"], contract_id)
        self.assertEqual(commitment["sequencial_empenho"], 7)
        self.assertEqual(commitment["valor_total_centavos"], 999)
        self.assertEqual(commitment["coverage_status"], "PARTIAL")

    def test_contract_without_supplier_identifier_is_preserved(self) -> None:
        contract_id = "12345678000199-2-000002/2023"
        _, _, supplier, contract = normalize_contract(
            {**RAW, "external_id": contract_id},
            {
                "numeroControlePNCP": contract_id,
                "numeroControlePncpCompra": RAW["external_id"],
                "tipoPessoa": "PE",
                "niFornecedor": "",
                "nomeRazaoSocialFornecedor": "Fornecedor informado em texto",
                "orgaoEntidade": {
                    "cnpj": "12345678000199",
                    "razaoSocial": "Orgao",
                },
                "unidadeOrgao": {
                    "codigoUnidade": "U1",
                    "ufSigla": "RJ",
                },
            },
            normalized_at="2026-10-19",
            parent_is_present=False,
        )
        self.assertIsNone(supplier)
        self.assertIsNone(contract["ni_fornecedor"])
        self.assertEqual(
            contract["supplier_resolution_status"], "MISSING_IDENTIFIER"
        )
        self.assertEqual(
            contract["nome_fornecedor_informado"],
            "Fornecedor informado em texto",
        )

    def test_normalizes_both_price_registry_formats(self) -> None:
        flat = {
            "numeroControlePNCPAta": "12345678000199-1-000001/2023-000001",
            "numeroControlePNCPCompra": RAW["external_id"],
            "cnpjOrgao": "12345678000199",
            "nomeOrgao": "Orgao",
            "codigoUnidadeOrgao": "U1",
            "nomeUnidadeOrgao": "Unidade",
            "cancelado": False,
            "possibilidadeAdesao": True,
        }
        _, _, flat_ata = normalize_price_registry(
            RAW,
            flat,
            {"uf": "RJ"},
            normalized_at="2026-10-19",
            parent_is_present=True,
        )
        nested = {
            "numeroControlePNCP": "12345678000199-1-000001/2023-000002",
            "orgaoEntidade": {
                "cnpj": "12345678000199",
                "razaoSocial": "Orgao",
            },
            "unidadeOrgao": {
                "codigoUnidade": "U1",
                "nomeUnidade": "Unidade",
                "ufSigla": "RJ",
            },
            "cancelado": False,
        }
        _, _, nested_ata = normalize_price_registry(
            RAW,
            nested,
            {"numeroControlePNCPCompra": RAW["external_id"]},
            normalized_at="2026-10-19",
            parent_is_present=False,
        )
        self.assertEqual(flat_ata["contratacao_id"], RAW["external_id"])
        self.assertEqual(flat_ata["possibilidade_adesao"], 1)
        self.assertEqual(nested_ata["numero_controle_compra_externo"], RAW["external_id"])
        self.assertEqual(nested_ata["parent_resolution_status"], "OUT_OF_SCOPE")

    def test_normalizes_pca_wrapper_and_rejects_mixed_identity(self) -> None:
        payload = {
            "unidadeResponsavel": "Unidade",
            "itens": [
                {
                    "cnpj": "12345678000199",
                    "codigoUnidade": "U1",
                    "anoPca": 2023,
                    "numeroItem": 1,
                    "descricaoItem": "Servico",
                    "valorTotal": "20.50",
                }
            ],
        }
        pca, unit, items = normalize_pca(
            RAW, payload, normalized_at="2026-10-19"
        )
        self.assertEqual(pca["source_format"], "UASG_WRAPPER")
        self.assertEqual(unit["uf_sigla"], "RJ")
        self.assertEqual(items[0]["valor_total_planejado_centavos"], 2050)

        payload["itens"].append(
            {
                "cnpj": "00000000000000",
                "codigoUnidade": "U2",
                "anoPca": 2023,
                "numeroItem": 2,
            }
        )
        with self.assertRaisesRegex(NormalizationError, "divergem"):
            normalize_pca(RAW, payload, normalized_at="2026-10-19")


if __name__ == "__main__":
    unittest.main()
