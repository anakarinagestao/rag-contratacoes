import unittest

from src.ingestion.scope_inventory import (
    OrganizationUnit,
    ScopeInventory,
    ScopeInventoryError,
    build_scope_inventory,
)


class ScopeInventoryTests(unittest.TestCase):
    @staticmethod
    def payload(cnpj, unit, uf="RJ"):
        return {
            "orgaoEntidade": {"cnpj": cnpj},
            "unidadeOrgao": {
                "codigoUnidade": unit,
                "ufSigla": uf,
            },
        }

    def test_builds_stable_deduplicated_inventory(self):
        first = self.payload("22222222000199", "U2")
        second = self.payload("11111111000199", "U1")
        inventory = build_scope_inventory(
            [first, second, first], expected_uf="rj"
        )

        self.assertEqual(
            inventory.coverage_members,
            (
                "orgao-unidade:11111111000199:U1",
                "orgao-unidade:22222222000199:U2",
            ),
        )
        self.assertEqual(
            inventory.targets[0],
            {
                "cnpj": "11111111000199",
                "codigo_unidade": "U1",
                "uf": "RJ",
            },
        )
        repeated = build_scope_inventory(
            [second, first], expected_uf="RJ"
        )
        self.assertEqual(inventory.inventory_id, repeated.inventory_id)

    def test_rejects_missing_or_out_of_scope_payload(self):
        with self.assertRaisesRegex(ScopeInventoryError, "sem campos"):
            build_scope_inventory(
                [{"orgaoEntidade": {"cnpj": "12345678000199"}}],
                expected_uf="RJ",
            )
        with self.assertRaisesRegex(ScopeInventoryError, "fora da UF"):
            build_scope_inventory(
                [self.payload("12345678000199", "U1", "SP")],
                expected_uf="RJ",
            )

    def test_rejects_invalid_cnpj_and_empty_inventory(self):
        with self.assertRaisesRegex(ScopeInventoryError, "CNPJ"):
            build_scope_inventory(
                [self.payload("123", "U1")], expected_uf="RJ"
            )
        with self.assertRaisesRegex(ScopeInventoryError, "vazio"):
            build_scope_inventory([], expected_uf="RJ")

    def test_direct_inventory_construction_is_canonical(self):
        member = OrganizationUnit(
            " 12345678000199 ", " 00042 ", " rj "
        )
        inventory = ScopeInventory(" rj ", (member,))

        self.assertEqual(
            (member.cnpj, member.unit_code, member.uf),
            ("12345678000199", "00042", "RJ"),
        )
        self.assertEqual(inventory.uf, "RJ")


if __name__ == "__main__":
    unittest.main()
